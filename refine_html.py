# -----------------------------------------------------------------------------------------------------------------------
# Refine the HTML （2nd.py）
# -----------------------------------------------------------------------------------------------------------------------

#!/usr/bin/env python3
"""
Convert the old single-page report (pubmed_results.html / index.html) into:
  - a NEW HTML report with one tab per journal, papers sorted by publication date (newest first),
    and a search box on top that filters and highlights papers by keyword
  - a CSV file (journal, title, abstract, English/Mandarin summary, publication date, ...)

It re-uses the summaries already in your HTML file, so it makes NO new Gemini requests.

The old file only stores the publication YEAR and no abstract. With LOOKUP_PUBMED = True the script
asks PubMed (free, no key needed) for each paper's exact publication date and abstract, using the DOI
or PubMed ID found in the paper's link. Papers it cannot find keep their year only.

HOW TO USE: edit only the SETTINGS block below, then run the file (or the Colab cell).
Requires: pip install requests
"""

import csv
import glob
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from html import escape, unescape

import requests

# =============================================================================
# SETTINGS - edit this block only
# =============================================================================

INPUT_HTML = "pubmed_results.html"              # the old report (e.g. "index.html" or "pubmed_results.html")
OUTPUT_HTML = "pubmed_results_tabs.html"        # set to None to skip
OUTPUT_CSV = "pubmed_results_tabs.csv"          # set to None to skip
REPORT_TITLE = None                             # None = reuse the title found in the input file

LOOKUP_PUBMED = True    # True = fetch exact publication dates + abstracts from PubMed (needs internet)
EMAIL = ""              # optional: your email, recommended by NCBI (NOT used to send results)
API_KEY = ""            # optional: NCBI API key for a higher rate limit
LOOKUP_CHUNK = 40       # DOIs per PubMed search request

# =============================================================================
# CODE - no need to edit below this line
# =============================================================================

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# ------------------------------------------------- 1. read the old HTML file ---
def _grab(pattern, text, default=""):
    m = re.search(pattern, text, re.DOTALL)
    return unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else default


def parse_old_html(path):
    with open(path, encoding="utf-8") as f:
        page = f.read()

    title = _grab(r"<h1>(.*?)</h1>", page)
    records = []
    for block in page.split('<div class="card">')[1:]:          # one block per paper
        link = _grab(r'<a href="(.*?)" class="title"', block)
        doi_m = re.search(r"doi\.org/(.+)$", link)
        pmid_m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", link)
        year = _grab(r'class="year-badge">(.*?)</span>', block)
        records.append({
            "pub_date": year if year.isdigit() else "",
            "sort_date": f"{year}-01-01" if year.isdigit() else "0000-00-00",
            "title": _grab(r'class="title"[^>]*>(.*?)</a>', block),
            "journal": _grab(r'<div class="journal">(.*?)</div>', block) or "Unknown journal",
            "authors": _grab(r"<strong>Authors:</strong>(.*?)</div>", block),
            "english_summary": _grab(r"English Summary</span>(.*?)</div>", block),
            "mandarin_summary": _grab(r"Mandarin Summary[^<]*</span>(.*?)</div>", block),
            "matched_keywords": _grab(r"Matched keywords:(.*?)</div>", block),
            "url": link,
            "doi": doi_m.group(1).strip() if doi_m else "",
            "pmid": pmid_m.group(1) if pmid_m else "",
            "abstract": "",
        })
    return title, records


# ------------------------------------------- 2. look up dates/abstracts (PubMed) ---
def _params():
    p = {}
    if EMAIL:
        p["email"] = EMAIL
    if API_KEY:
        p["api_key"] = API_KEY
    return p


def _pause():
    time.sleep(0.12 if API_KEY else 0.4)               # NCBI allows ~3 requests/second without a key


def text_of(parent, tag):
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def _month_num(value):
    value = (value or "").strip()
    if value.isdigit():
        n = int(value)
        return n if 1 <= n <= 12 else None
    return MONTHS.get(value[:3].lower())


def make_date(year, month, day):
    """Possibly partial date parts -> (sort_key 'YYYY-MM-DD', display string)."""
    if not (year and year.isdigit()):
        return "0000-00-00", ""
    y, m = int(year), _month_num(month)
    d = int(day) if (m and day and day.isdigit()) else None
    sort_key = f"{y:04d}-{m or 1:02d}-{d or 1:02d}"      # missing month/day count as the 1st
    shown = f"{y:04d}" + (f"-{m:02d}" if m else "") + (f"-{d:02d}" if d else "")
    return sort_key, shown


def parse_pub_date(art):
    """Online (electronic) date if the record has one, otherwise the journal issue date."""
    el = art.find(".//ArticleDate[@DateType='Electronic']")
    if el is not None:
        key, shown = make_date(text_of(el, "Year"), text_of(el, "Month"), text_of(el, "Day"))
        if shown:
            return key, shown
    el = art.find(".//JournalIssue/PubDate")
    if el is not None:
        year, month, day = text_of(el, "Year"), text_of(el, "Month"), text_of(el, "Day")
        if not year:                                      # e.g. <MedlineDate>2026 Oct-Dec</MedlineDate>
            medline = text_of(el, "MedlineDate")
            ym = re.search(r"\d{4}", medline)
            if ym:
                year = ym.group(0)
                mm = re.search(r"[A-Za-z]{3}", medline[ym.end():])
                month = mm.group(0) if mm else ""
        key, shown = make_date(year, month, day)
        if shown:
            return key, shown
    return "0000-00-00", ""


def pubmed_search_dois(dois):
    """Return PubMed IDs for a list of DOIs (searches the Article Identifier field)."""
    def search(term):
        r = requests.post(f"{EUTILS}/esearch.fcgi",
                          data={"db": "pubmed", "term": term, "retmax": 500,
                                "retmode": "json", **_params()}, timeout=60)
        r.raise_for_status()
        return r.json()["esearchresult"].get("idlist", [])

    ids = []
    for i in range(0, len(dois), LOOKUP_CHUNK):
        chunk = dois[i:i + LOOKUP_CHUNK]
        try:
            ids += search(" OR ".join(f'"{d}"[AID]' for d in chunk))
        except requests.RequestException:                 # one odd DOI can break a chunk: retry one by one
            for d in chunk:
                try:
                    ids += search(f'"{d}"[AID]')
                except requests.RequestException:
                    pass
                _pause()
        _pause()
        print(f"  searched {min(i + LOOKUP_CHUNK, len(dois))}/{len(dois)} DOIs")
    return ids


def pubmed_fetch(pmids):
    for i in range(0, len(pmids), 200):
        r = requests.post(f"{EUTILS}/efetch.fcgi",
                          data={"db": "pubmed", "id": ",".join(pmids[i:i + 200]),
                                "retmode": "xml", **_params()}, timeout=90)
        r.raise_for_status()
        yield from ET.fromstring(r.content).findall(".//PubmedArticle")
        _pause()


def enrich_from_pubmed(records):
    doi_to_rec = {r["doi"].lower(): r for r in records if r["doi"]}
    pmid_to_rec = {r["pmid"]: r for r in records if r["pmid"]}
    print(f"Looking up {len(records)} papers in PubMed ({len(doi_to_rec)} by DOI, {len(pmid_to_rec)} by PMID)...")

    try:
        wanted = set(pmid_to_rec) | set(pubmed_search_dois(list(doi_to_rec)))
        found = 0
        for art in pubmed_fetch(sorted(wanted)):
            pmid = text_of(art, ".//MedlineCitation/PMID")
            doi_el = art.find(".//ArticleId[@IdType='doi']")
            doi = (doi_el.text or "").strip().lower() if doi_el is not None else ""
            rec = pmid_to_rec.get(pmid) or doi_to_rec.get(doi)
            if rec is None:
                continue
            sort_date, pub_date = parse_pub_date(art)
            if pub_date:
                rec["sort_date"], rec["pub_date"] = sort_date, pub_date
            rec["abstract"] = " ".join("".join(a.itertext()).strip() for a in art.findall(".//AbstractText"))
            rec["pmid"] = rec["pmid"] or pmid
            found += 1
        print(f"PubMed matched {found} of {len(records)} papers (the rest keep their year only).")
    except requests.RequestException as e:
        print(f"PubMed lookup failed ({e}); continuing with years only.")


# ------------------------------------------------------------ 3. write output ---
CSV_FIELDS = ["pub_date", "journal", "title", "abstract", "english_summary", "mandarin_summary",
              "authors", "doi", "pmid", "url", "matched_keywords"]


def save_csv(records, filename):
    # utf-8-sig so Excel displays the Mandarin text correctly
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    print(f"Saved CSV: {filename}")


HTML_CSS = r"""
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
       "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
       background: #f4f6f9; color: #333; line-height: 1.6; margin: 0; padding: 20px; }
.container { max-width: 960px; margin: 0 auto; }
header { background: #fff; padding: 20px 30px; border-radius: 8px;
         box-shadow: 0 2px 4px rgba(0,0,0,.05); margin-bottom: 12px; }
h1 { margin: 0 0 10px 0; color: #1a365d; font-size: 24px; }
.meta-info { color: #555; font-size: 14px; }
.hidden { display: none !important; }

.search-bar { position: sticky; top: 0; z-index: 10; background: #f4f6f9; padding: 8px 0 12px 0; }
.search-row { display: flex; gap: 8px; }
.search-row input[type=search] { flex: 1; font-size: 16px; padding: 10px 14px; border: 1px solid #cbd5e0;
        border-radius: 8px; background: #fff; color: #333; }
.search-row input[type=search]:focus { outline: 2px solid #2b6cb0; border-color: #2b6cb0; }
.search-row button { font-size: 14px; padding: 0 16px; border: 1px solid #cbd5e0; background: #fff;
        border-radius: 8px; cursor: pointer; color: #2d3748; }
.search-row button:hover { background: #edf2f7; }
.search-opts { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
        gap: 6px 16px; margin-top: 8px; font-size: 13px; color: #4a5568; }
#status { margin: 0; color: #4a5568; }
#status.found { color: #2b6cb0; font-weight: 600; }
mark { background: #fde68a; color: inherit; padding: 0 1px; border-radius: 2px; }

.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.tab-btn { border: 1px solid #cbd5e0; background: #fff; color: #2d3748; padding: 7px 12px;
           border-radius: 6px; font-size: 13px; cursor: pointer; text-align: left; }
.tab-btn:hover { background: #edf2f7; }
.tab-btn.active { background: #2b6cb0; border-color: #2b6cb0; color: #fff; }
.tab-btn .count { display: inline-block; margin-left: 6px; padding: 0 7px; border-radius: 10px;
                  background: #e2e8f0; color: #2d3748; font-size: 12px; }
.tab-btn.active .count { background: rgba(255,255,255,.25); color: #fff; }

.panel { display: none; scroll-margin-top: 120px; }
.panel.active { display: block; }
.panel-title { display: none; margin: 18px 0 4px 2px; font-size: 18px; color: #1a365d; }
body.show-all .panel.active:not(.has-match) { display: none; }
body.show-all .panel.has-match { display: block; }
body.show-all .panel-title { display: block; }
.panel-note { color: #718096; font-size: 13px; margin: 0 0 12px 2px; }
#empty { display: none; background: #fff; padding: 24px; border-radius: 8px; color: #555; text-align: center; }
#empty.visible { display: block; }

.card { background: #fff; border-radius: 8px; padding: 20px 25px; margin-bottom: 16px;
        box-shadow: 0 2px 4px rgba(0,0,0,.05); }
.card:hover { box-shadow: 0 4px 8px rgba(0,0,0,.1); }
.date-badge { background: #e2e8f0; color: #2d3748; padding: 3px 8px; border-radius: 4px;
              font-size: 12px; font-weight: bold; }
.title { font-size: 18px; font-weight: 600; color: #2b6cb0; text-decoration: none;
         margin: 8px 0; display: block; }
.title:hover { text-decoration: underline; }
.authors { color: #555; font-size: 13px; }
.summary-box { background: #ebf8ff; border-left: 4px solid #3182ce; padding: 10px 15px;
               border-radius: 0 4px 4px 0; margin-top: 12px; font-size: 15px; color: #2c5282; }
.mandarin-box { background: #f0fff4; border-left-color: #38a169; color: #276749; }
.summary-label { font-weight: bold; font-size: 12px; text-transform: uppercase;
                 letter-spacing: .5px; margin-bottom: 4px; display: block; }
details { margin-top: 12px; font-size: 14px; }
summary { cursor: pointer; color: #4a5568; }
details p { margin: 8px 0 0 0; color: #4a5568; }
.keywords { font-size: 12px; color: #718096; margin-top: 10px; }
"""

HTML_JS = r"""
(function () {
  var tabs = [].slice.call(document.querySelectorAll('.tab-btn'));
  var panels = [].slice.call(document.querySelectorAll('.panel'));
  var input = document.getElementById('q');
  var clearBtn = document.getElementById('clear');
  var groupBox = document.getElementById('group');
  var statusEl = document.getElementById('status');
  var emptyEl = document.getElementById('empty');
  var HINT = 'Tip: type several words to find papers containing all of them. Press / to jump here.';
  var active = 0, timer = null, wasSearching = false;

  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function mark(text, re) {              // wrap matches in <mark>, escaping everything else
    var out = '', last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(text)) !== null) {
      if (m[0] === '') { re.lastIndex++; continue; }
      out += esc(text.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
      last = m.index + m[0].length;
    }
    return out + esc(text.slice(last));
  }
  function parse(q) {                    // words are ANDed; "quoted text" is an exact phrase
    var terms = [], re = /"([^"]+)"|(\S+)/g, m, t;
    while ((m = re.exec(q)) !== null) {
      t = (m[1] || m[2]).replace(/^"+|"+$/g, '').toLowerCase();
      if (t) terms.push(t);
    }
    return terms;
  }

  // Cache each card's searchable text and the original text of its highlightable parts.
  var cards = [];
  panels.forEach(function (p, pi) {
    var name = p.dataset.name, note = p.querySelector('.panel-note');
    note.dataset.orig = note.textContent;
    [].forEach.call(p.querySelectorAll('.card'), function (c) {
      var hls = [].slice.call(c.querySelectorAll('.hl'));
      hls.forEach(function (h) { h.dataset.orig = h.textContent; });
      var kw = c.querySelector('.kw'), date = c.querySelector('.date-badge');
      var text = [name, date ? date.textContent : '', kw ? kw.textContent : '']
        .concat(hls.map(function (h) { return h.dataset.orig; })).join(' ').toLowerCase();
      cards.push({ el: c, panel: pi, hls: hls, text: text,
                   details: c.querySelector('details'), abs: c.querySelector('.abs') });
    });
  });

  function show(i) {
    active = i;
    var all = document.body.classList.contains('show-all');
    tabs.forEach(function (b, k) { b.classList.toggle('active', k === i && !all); });
    panels.forEach(function (p, k) { p.classList.toggle('active', k === i); });
  }

  function apply() {
    var terms = parse(input.value), searching = terms.length > 0;
    var group = searching && groupBox.checked;
    var re = searching ? new RegExp(terms.slice().sort(function (a, b) { return b.length - a.length; })
                                    .map(escRe).join('|'), 'gi') : null;
    var counts = panels.map(function () { return 0; });

    cards.forEach(function (c) {
      var ok = !searching || terms.every(function (t) { return c.text.indexOf(t) !== -1; });
      c.el.classList.toggle('hidden', !ok);
      if (ok) counts[c.panel]++;
      if (searching || wasSearching) {
        c.hls.forEach(function (h) { h.innerHTML = (searching && ok) ? mark(h.dataset.orig, re) : esc(h.dataset.orig); });
        if (c.details) {                 // open the abstract when the match is inside it
          var inAbs = searching && ok && c.abs && terms.some(function (t) {
            return c.abs.dataset.orig.toLowerCase().indexOf(t) !== -1; });
          c.details.open = !!inAbs;
        }
      }
    });
    wasSearching = searching;

    var total = 0, hit = 0;
    panels.forEach(function (p, i) {
      p.classList.toggle('has-match', counts[i] > 0);
      tabs[i].classList.toggle('hidden', searching && counts[i] === 0);
      tabs[i].querySelector('.count').textContent = counts[i];
      var note = p.querySelector('.panel-note');
      note.textContent = searching ? counts[i] + ' matching paper(s), newest first' : note.dataset.orig;
      total += counts[i];
      if (counts[i] > 0) hit++;
    });

    document.body.classList.toggle('show-all', group);
    if (searching && !group && counts[active] === 0) {      // jump to the first journal with a match
      for (var k = 0; k < counts.length; k++) { if (counts[k] > 0) { active = k; break; } }
    }
    show(active);

    statusEl.className = searching ? 'found' : '';
    statusEl.textContent = !searching ? HINT
      : total ? total + ' paper(s) in ' + hit + ' journal(s) match' : 'No matches';
    emptyEl.classList.toggle('visible', searching && total === 0);
  }

  tabs.forEach(function (b, i) {
    b.addEventListener('click', function () {
      if (document.body.classList.contains('show-all')) {
        panels[i].scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else { show(i); }
    });
  });
  input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(apply, 150); });
  groupBox.addEventListener('change', apply);
  clearBtn.addEventListener('click', function () { input.value = ''; apply(); input.focus(); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { input.value = ''; apply(); }
  });
  document.addEventListener('keydown', function (e) {
    var tag = (e.target.tagName || '').toLowerCase();
    if (e.key === '/' && tag !== 'input' && tag !== 'textarea') { e.preventDefault(); input.focus(); }
  });

  show(0);
  apply();
})();
"""


def card_html(m):
    summaries = ""
    if m["english_summary"]:
        summaries += ('<div class="summary-box"><span class="summary-label">English Summary</span>'
                      f'<span class="hl">{escape(m["english_summary"])}</span></div>')
    if m["mandarin_summary"]:
        summaries += ('<div class="summary-box mandarin-box" lang="zh">'
                      '<span class="summary-label">Mandarin Summary (中文摘要)</span>'
                      f'<span class="hl">{escape(m["mandarin_summary"])}</span></div>')
    abstract = (f'<details><summary>Show abstract</summary><p class="hl abs">{escape(m["abstract"])}</p></details>'
                if m["abstract"] else "")
    return f"""
    <div class="card">
      <span class="date-badge">{escape(m['pub_date'] or 'Date N/A')}</span>
      <a href="{escape(m['url'], quote=True)}" class="title hl" target="_blank" rel="noopener">{escape(m['title'])}</a>
      <div class="authors"><strong>Authors:</strong> <span class="hl">{escape(m['authors'] or 'N/A')}</span></div>
      {summaries}
      {abstract}
      <div class="keywords">Matched keywords: <span class="kw">{escape(m['matched_keywords'])}</span></div>
    </div>"""


def save_html(records, title, filename):
    """One tab per journal; papers sorted newest first; search box on top filters and highlights."""
    by_journal = {}
    for m in records:
        by_journal.setdefault(m["journal"], []).append(m)
    for papers in by_journal.values():
        papers.sort(key=lambda r: (r["sort_date"], r["title"].lower()), reverse=True)
    journals = sorted(by_journal, key=lambda j: (-len(by_journal[j]), j.lower()))     # biggest tab first

    tab_buttons = "".join(
        f'<button class="tab-btn" data-tab="{i}">{escape(j)}<span class="count">{len(by_journal[j])}</span></button>'
        for i, j in enumerate(journals))
    panels = "".join(
        f'<section class="panel" data-tab="{i}" data-name="{escape(j, quote=True)}">'
        f'<h2 class="panel-title">{escape(j)}</h2>'
        f'<p class="panel-note">{len(by_journal[j])} paper(s), newest first</p>'
        + "".join(card_html(m) for m in by_journal[j]) + "</section>"
        for i, j in enumerate(journals))

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>{HTML_CSS}</style>
</head>
<body>
<div class="container">
  <header>
    <h1>{escape(title)}</h1>
    <div class="meta-info">Total papers: <strong>{len(records)}</strong> in <strong>{len(journals)}</strong>
      journal(s) | Generated on: {datetime.now().strftime("%B %d, %Y")}</div>
  </header>
  <div class="search-bar">
    <div class="search-row">
      <input id="q" type="search" autocomplete="off" aria-label="Search papers"
             placeholder="Search titles, summaries, abstracts, authors...  (use &quot;quotes&quot; for an exact phrase)">
      <button id="clear" type="button">Clear</button>
    </div>
    <div class="search-opts">
      <p id="status"></p>
      <label><input type="checkbox" id="group" checked> Show matches from all journals together</label>
    </div>
  </div>
  <nav class="tabs">{tab_buttons}</nav>
  <p id="empty">No papers match your search.</p>
  {panels}
</div>
<script>{HTML_JS}</script>
</body>
</html>
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Saved HTML: {filename}")


# ------------------------------------------------------------------- Main ---
def locate_input():
    """Return the path of the old report, offering an upload box when running in Colab."""
    if os.path.exists(INPUT_HTML):
        return INPUT_HTML
    print(f"Input file '{INPUT_HTML}' was not found in the current folder ({os.getcwd()}).")
    try:
        from google.colab import files               # only exists inside Google Colab
        print("Please choose your old HTML report (e.g. index.html / pubmed_results.html) to upload:")
        uploaded = files.upload()
        names = [n for n in uploaded if n.lower().endswith((".html", ".htm"))]
        if names:
            return names[0]
        print("No .html file was uploaded.")
    except ImportError:
        pass
    print("HTML files in the current folder:", sorted(glob.glob("*.html")) or "none")
    print("Fix INPUT_HTML in the SETTINGS block (a full path such as /content/index.html also works).")
    return None


def main():
    path = locate_input()
    if not path:
        return
    old_title, records = parse_old_html(path)
    if not records:
        print("No papers found in the input file. Is it the report made by the earlier script?")
        return
    print(f"Read {len(records)} papers from {path}")

    if LOOKUP_PUBMED:
        enrich_from_pubmed(records)
    else:
        print("LOOKUP_PUBMED is False: dates are years only and there are no abstracts.")

    records.sort(key=lambda r: r["sort_date"], reverse=True)
    print("Papers per journal:")
    for journal, n in Counter(r["journal"] for r in records).most_common():
        print(f"   {n:4d}   {journal}")

    title = REPORT_TITLE or old_title or "Literature Review"
    if OUTPUT_HTML:
        save_html(records, title, OUTPUT_HTML)
    if OUTPUT_CSV:
        save_csv(records, OUTPUT_CSV)


if __name__ == "__main__":
