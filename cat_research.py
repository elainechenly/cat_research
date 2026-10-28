# -*- coding: utf-8 -*-
"""Cat Research.ipynb
"""

# n journals, n key words + LLM + HTML -----------------------------------------

#!/usr/bin/env python3
"""
Search PubMed for papers in chosen journals whose abstract contains chosen keywords,
and output the results as a clean, readable HTML file featuring both English and Mandarin summaries.

HOW TO USE: edit only the SETTINGS block below, then run the file (or the Colab cell).
Requires: pip install requests
"""

import csv
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

import requests

# =============================================================================
# SETTINGS - edit this block only
# =============================================================================

# Journals: use the PubMed abbreviation. To add one, add a new line.
JOURNALS = [
    # --- Feline-Specific & Clinical Journals ---
    "J Feline Med Surg",             # Journal of Feline Medicine and Surgery
    "J Vet Intern Med",              # Journal of Veterinary Internal Medicine
    "Vet Surg",                      # Veterinary Surgery

    # --- Comprehensive & General Veterinary Science ---
    "Vet J",                         # The Veterinary Journal
    "Vet Res",                       # Veterinary Research
    "Front Vet Sci",                 # Frontiers in Veterinary Science
    "Am J Vet Res",                  # American Journal of Veterinary Research
    "Annu Rev Anim Biosci",          # Annual Review of Animal Biosciences

    # --- Animal Nutrition, Metabolism & Health ---
    "Anim Nutr",                     # Animal Nutrition
    "J Anim Sci Biotechnol",         # Journal of Animal Science and Biotechnology (JASB)
    "Anim Health Res Rev",           # Animal Health Research Reviews
    "J Anim Physiol Anim Nutr (Berl)", # Journal of Animal Physiology and Animal Nutrition
    "Anim Front",                    # Animal Frontiers

    # --- Behavior, Welfare & Microbiology ---
    "Appl Anim Behav Sci",           # Applied Animal Behaviour Science
    "J Vet Behav",                   # Journal of Veterinary Behavior
    "Vet Microbiol",                 # Veterinary Microbiology

    # --- Traditional Livestock, Dairy, Poultry & General Science ---
    "J Anim Sci",                    # Journal of Animal Science (JAS)
    "J Dairy Sci",                   # Journal of Dairy Science (JDS)
    "Poult Sci",                     # Poultry Science
    "Animals (Basel)",               # Animals (Multidisciplinary open-access)

    # --- Epidemiology & Preventive Medicine ---
    "Prev Vet Med",                  # Preventive Veterinary Medicine
    "Transbound Emerg Dis",          # Transboundary and Emerging Diseases
]

# Keywords / phrases: one per line. Phrases are fine, e.g. "gut microbiota".
KEYWORDS = [
    "cat",
    "cats",
    "felis catus",
    "felis silvestris catus",
    "feline",
    "felines",
    "felid",
    "felidae",
    "housecat",
    "kitten"
]

MATCH_ALL = False       # False = abstract contains ANY keyword (OR); True = ALL keywords (AND)
SUBSTRING = False       # False = whole word only (cat, cats; not "category"); True = loose match
ADD_PLURALS = True      # also search keyword + "s" in the PubMed query

DAYS_BACK = 366           # only papers from the last N days (7 = last week); None = no date limit
DATE_TYPE = "pdat"      # which date to use: "pdat" = publication date (print or online),
                        #                   "edat" = date the record was added to PubMed

OUTPUT_HTML = "pubmed_results.html" # HTML file output path
EMAIL = ""              # optional: your email, recommended by NCBI (NOT used to send results)
API_KEY = ""            # optional: NCBI API key for a higher rate limit
MAX_RECORDS = 5000      # safety cap on records retrieved
PRINT_PAPERS = True     # False = only print the summary counts, not every paper

# --- LLM summaries (free tier of the Gemini API) ---
SUMMARIZE = True        # False = skip summaries (no API key needed)
GEMINI_API_KEY = ""     # paste a key from https://aistudio.google.com/app/api-keys, OR leave empty and
                        # set an environment variable / Colab secret named GEMINI_API_KEY (safer)
GEMINI_MODEL = "gemini-3.1-flash-lite"   # copy the exact current ID of a Flash-Lite model from AI Studio;
SUMMARY_BATCH = 3      # abstracts per request (batching saves your free daily request quota)
SUMMARY_PAUSE = 15       # seconds to wait between requests (stays under per-minute limits)

# =============================================================================
# CODE - no need to edit below this line
# =============================================================================

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BATCH_SIZE = 200


def build_regexes(keywords, substring):
    regexes = {}
    for kw in keywords:
        esc = re.escape(kw)
        regexes[kw] = re.compile(esc if substring else rf"\b{esc}(s|es)?\b", re.IGNORECASE)
    return regexes


def build_query(keywords, journals, match_all, add_plurals):
    groups = []
    for kw in keywords:
        variants = [kw, kw + "s"] if add_plurals else [kw]
        groups.append("(" + " OR ".join(f'"{v}"[Title/Abstract]' for v in variants) + ")")
    kw_part = (" AND " if match_all else " OR ").join(groups)
    journal_part = " OR ".join(f'"{j}"[Journal]' for j in journals)
    return f"({journal_part}) AND ({kw_part})"


def common_params():
    params = {}
    if EMAIL:
        params["email"] = EMAIL
    if API_KEY:
        params["api_key"] = API_KEY
    return params


def search_ids(query):
    params = {"db": "pubmed", "term": query, "retmax": MAX_RECORDS,
              "retmode": "json", **common_params()}
    if DAYS_BACK:
        params["datetype"] = DATE_TYPE
        params["reldate"] = DAYS_BACK             # PubMed counts back from today
    r = requests.get(f"{EUTILS}/esearch.fcgi", params=params, timeout=30)
    r.raise_for_status()
    result = r.json()["esearchresult"]
    return result.get("idlist", []), int(result.get("count", 0))


def fetch_records(ids):
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i:i + BATCH_SIZE]
        r = requests.post(f"{EUTILS}/efetch.fcgi",
                          data={"db": "pubmed", "id": ",".join(batch),
                                "retmode": "xml", **common_params()}, timeout=60)
        r.raise_for_status()
        yield from ET.fromstring(r.content).findall(".//PubmedArticle")


def text_of(parent, tag):
    el = parent.find(tag)
    return el.text if el is not None and el.text else ""


def parse_article(art):
    def text(path):
        el = art.find(path)
        return "".join(el.itertext()).strip() if el is not None else ""

    authors = []
    for a in art.findall(".//AuthorList/Author"):
        name = f"{text_of(a, 'ForeName')} {text_of(a, 'LastName')}".strip()
        if name:
            authors.append(name)

    doi_el = art.find(".//ArticleId[@IdType='doi']")
    return {
        "year": text(".//JournalIssue/PubDate/Year") or text(".//ArticleDate/Year"),
        "title": text(".//ArticleTitle"),
        "authors": "; ".join(authors),
        "journal": text(".//Journal/Title"),
        "doi": doi_el.text if doi_el is not None else "",
        "pmid": text(".//MedlineCitation/PMID"),
        "abstract": " ".join("".join(a.itertext()).strip() for a in art.findall(".//AbstractText")),
    }


def get_gemini_key():
    key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:                        # Colab "Secrets" panel (key icon on the left)
            from google.colab import userdata
            key = userdata.get("GEMINI_API_KEY")
        except Exception:
            key = ""
    return key


def call_gemini(prompt, key, retries=3):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    for attempt in range(retries):
        r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=90)
        if r.status_code == 429:               # rate limit: wait and retry
            wait = 20 * (attempt + 1)
            print(f"  Rate limit hit; waiting {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini rate limit: gave up after retries (daily free quota may be used up).")


def summarize_abstracts(records):
    """Filter and add bilingual summaries, keeping ONLY papers verified as about cats by LLM."""
    key = get_gemini_key()
    if not key:
        print("No Gemini API key found; skipping summaries and LLM cat filtering.")
        return records

    print(f"Filtering and summarizing {len(records)} abstracts (English & Mandarin) with {GEMINI_MODEL}...")
    filtered_records = []

    for i in range(0, len(records), SUMMARY_BATCH):
        batch = records[i:i + SUMMARY_BATCH]
        items = [{"pmid": r["pmid"], "title": r["title"], "abstract": r["abstract"]} for r in batch]
        prompt = (
            "For each paper's abstract, provide TWO summaries, each in exactly ONE plain sentence (max 35 words) stating "
            "what was studied and the main finding: one in English and one in Mandarin. "
            "Use only information in the abstract. "
            "If the abstract is NOT about felines/cats, set both 'english_summary' and 'mandarin_summary' to 'not about cat'. "
            'Return ONLY a JSON array of objects like {"pmid": "...", "english_summary": "...", "mandarin_summary": "..."}, '
            "one per paper, using the same pmid values.\n\n" + json.dumps(items, ensure_ascii=False)
        )
        try:
            result = json.loads(call_gemini(prompt, key))
            by_pmid = {
                str(x.get("pmid")): {
                    "english": x.get("english_summary", "").strip(),
                    "mandarin": x.get("mandarin_summary", "").strip()
                } for x in result
            }

            for r in batch:
                data = by_pmid.get(r["pmid"], {"english": "not about cat", "mandarin": "not about cat"})
                eng = data["english"]
                mand = data["mandarin"]
                # Keep only papers where summaries are valid and not rejected
                if eng and eng.lower() != "not about cat" and mand and mand.lower() != "not about cat":
                    r["english_summary"] = eng
                    r["mandarin_summary"] = mand
                    filtered_records.append(r)
        except Exception as e:
            print(f"  Summary batch starting at paper {i + 1} failed: {e}")

        if i + SUMMARY_BATCH < len(records):
            time.sleep(SUMMARY_PAUSE)

    return filtered_records


def save_html(matches, filename):
    """Generates a clean, modern HTML file containing bilingual results with today's date."""

    # Get today's date in both English and Mandarin formats
    today = datetime.now()
    date_english = today.strftime("%B %d, %Y")  # e.g., October 27, 2026
    # date_mandarin = today.strftime("%Y年%m月%d日") # e.g., 2026年10月27日

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>What We Learned About Cats This Year: A 12-Month Literature Review</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f4f6f9;
            color: #333;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        header {{
            background: #fff;
            padding: 20px 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            margin-bottom: 25px;
        }}
        h1 {{
            margin: 0 0 10px 0;
            color: #1a365d;
            font-size: 24px;
        }}
        .meta-info {{
            color: #555;
            font-size: 14px;
        }}
        .card {{
            background: #fff;
            border-radius: 8px;
            padding: 20px 25px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }}
        .card:hover {{
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 10px;
        }}
        .year-badge {{
            background: #e2e8f0;
            color: #2d3748;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }}
        .title {{
            font-size: 18px;
            font-weight: 600;
            color: #2b6cb0;
            text-decoration: none;
            margin-bottom: 8px;
            display: block;
        }}
        .title:hover {{
            text-decoration: underline;
        }}
        .journal {{
            font-style: italic;
            color: #4a5568;
            font-size: 14px;
            margin-bottom: 12px;
        }}
        .summary-box {{
            background: #ebf8ff;
            border-left: 4px solid #3182ce;
            padding: 10px 15px;
            border-radius: 0 4px 4px 0;
            margin-top: 12px;
            font-size: 15px;
            color: #2c5282;
        }}
        .mandarin-box {{
            background: #f0fff4;
            border-left-color: #38a169;
            color: #276749;
        }}
        .summary-label {{
            font-weight: bold;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #2b6cb0;
            margin-bottom: 4px;
            display: block;
        }}
        .mandarin-box .summary-label {{
            color: #2f855a;
        }}
        .keywords {{
            font-size: 12px;
            color: #718096;
            margin-top: 10px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>What We Learned About Cats This Year: A 12-Month Literature Review</h1>
            <div class="meta-info">
                Total Papers Found: <strong>{len(matches)}</strong> | Generated on: {date_english}
            </div>
        </header>
"""

    for m in matches:
        link = f"https://doi.org/{m['doi']}" if m['doi'] else f"https://pubmed.ncbi.nlm.nih.gov/{m['pmid']}/"
        summary_html = ""
        if m.get('english_summary'):
            summary_html += f"""
            <div class="summary-box english-box">
                <span class="summary-label">English Summary</span>
                {m['english_summary']}
            </div>
            """
        if m.get('mandarin_summary'):
            summary_html += f"""
            <div class="summary-box mandarin-box">
                <span class="summary-label">Mandarin Summary (中文摘要)</span>
                {m['mandarin_summary']}
            </div>
            """

        html_content += f"""
        <div class="card">
            <div class="card-header">
                <div>
                    <span class="year-badge">{m['year'] or 'N/A'}</span>
                </div>
            </div>
            <a href="{link}" class="title" target="_blank">{m['title']}</a>
            <div class="journal">{m['journal']}</div>
            <div class="meta-info"><strong>Authors:</strong> {m['authors'] or 'N/A'}</div>
            {summary_html}
            <div class="keywords">Matched keywords: {m['matched_keywords']}</div>
        </div>
        """

    html_content += """
    </div>
</body>
</html>
"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\nResults successfully saved to HTML file: {filename}")


def main():
    if not JOURNALS or not KEYWORDS:
        sys.exit("Please add at least one journal and one keyword in the SETTINGS block.")

    mode = "ALL" if MATCH_ALL else "ANY"
    regexes = build_regexes(KEYWORDS, SUBSTRING)
    query = build_query(KEYWORDS, JOURNALS, MATCH_ALL, ADD_PLURALS)
    print(f"Journals: {JOURNALS}")
    print(f"Keywords ({mode}): {KEYWORDS}")
    print(f"PubMed query: {query}")
    if DAYS_BACK:
        print(f"Date limit: last {DAYS_BACK} days (by {DATE_TYPE})")
    else:
        print("Date limit: none")

    try:
        ids, total = search_ids(query)
    except requests.RequestException as e:
        sys.exit(f"Search failed: {e}")

    print(f"PubMed reports {total} candidate records; retrieving {len(ids)}.")
    if not ids:
        hint = " Try a larger DAYS_BACK, or set it to None." if DAYS_BACK else ""
        sys.exit("No records found. Check the keywords and the journal abbreviations." + hint)

    matches = []
    try:
        for art in fetch_records(ids):
            rec = parse_article(art)
            found = [kw for kw, rx in regexes.items() if rx.search(rec["abstract"])]
            ok = len(found) == len(regexes) if MATCH_ALL else bool(found)
            if ok:                                      # strict check on the abstract itself
                rec["matched_keywords"] = "; ".join(found)
                matches.append(rec)
    except requests.RequestException as e:
        sys.exit(f"Fetching records failed: {e}")

    matches.sort(key=lambda r: r["year"], reverse=True)

    if SUMMARIZE and matches:
        matches = summarize_abstracts(matches)

    print(f"\n{len(matches)} papers verified as about cats.")

    print("Breakdown by journal:")
    for journal, n in Counter(m["journal"] for m in matches).most_common():
        print(f"   {n:4d}   {journal}")

    print("Breakdown by keyword (a paper can count under several):")
    kw_counts = Counter(kw for m in matches for kw in m["matched_keywords"].split("; ") if kw)
    for kw in KEYWORDS:
        print(f"   {kw_counts.get(kw, 0):4d}   {kw}")
    print()

    if PRINT_PAPERS:
        for m in matches:
            link = f"https://doi.org/{m['doi']}" if m["doi"] else f"https://pubmed.ncbi.nlm.nih.gov/{m['pmid']}/"
            eng_line = f"    English Summary: {m['english_summary']}\n" if m.get("english_summary") else ""
            mand_line = f"    Mandarin Summary: {m['mandarin_summary']}\n" if m.get("mandarin_summary") else ""
            print(f"[{m['year']}] {m['title']}\n    {m['journal']} | matched: {m['matched_keywords']}\n{eng_line}{mand_line}    {link}\n")

    if matches and OUTPUT_HTML:
        save_html(matches, OUTPUT_HTML)


if __name__ == "__main__":
    main()

# Refine the HTML --------------------------------------------------------------

#!/usr/bin/env python3
"""
Convert the old single-page report (pubmed_results.html / index.html) into:
  - a NEW HTML report with one tab per journal, papers sorted by publication date (newest first)
  - a CSV file (journal, title, abstract, English/Mandarin summary, publication date, ...)

It re-uses the summaries already in your HTML file, so it makes NO new Gemini requests.

The old file only stores the publication YEAR and no abstract. With LOOKUP_PUBMED = True the script
asks PubMed (free, no key needed) for each paper's exact publication date and abstract, using the DOI
or PubMed ID found in the paper's link. Papers it cannot find keep their year only.

HOW TO USE: edit only the SETTINGS block below, then run the file (or the Colab cell).
Requires: pip install requests
"""

import csv
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

INPUT_HTML = "pubmed_results.html"                       # the old report (e.g. "index.html" or "pubmed_results.html")
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


HTML_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
       "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
       background: #f4f6f9; color: #333; line-height: 1.6; margin: 0; padding: 20px; }
.container { max-width: 960px; margin: 0 auto; }
header { background: #fff; padding: 20px 30px; border-radius: 8px;
         box-shadow: 0 2px 4px rgba(0,0,0,.05); margin-bottom: 20px; }
h1 { margin: 0 0 10px 0; color: #1a365d; font-size: 24px; }
.meta-info { color: #555; font-size: 14px; }
.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.tab-btn { border: 1px solid #cbd5e0; background: #fff; color: #2d3748; padding: 7px 12px;
           border-radius: 6px; font-size: 13px; cursor: pointer; text-align: left; }
.tab-btn:hover { background: #edf2f7; }
.tab-btn.active { background: #2b6cb0; border-color: #2b6cb0; color: #fff; }
.tab-btn .count { display: inline-block; margin-left: 6px; padding: 0 7px; border-radius: 10px;
                  background: #e2e8f0; color: #2d3748; font-size: 12px; }
.tab-btn.active .count { background: rgba(255,255,255,.25); color: #fff; }
.panel { display: none; }
.panel.active { display: block; }
.panel-note { color: #718096; font-size: 13px; margin: 0 0 12px 2px; }
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

HTML_JS = """
(function () {
  var tabs = document.querySelectorAll('.tab-btn');
  var panels = document.querySelectorAll('.panel');
  function show(i) {
    tabs.forEach(function (b) { b.classList.toggle('active', b.dataset.tab === i); });
    panels.forEach(function (p) { p.classList.toggle('active', p.dataset.tab === i); });
  }
  tabs.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.tab); }); });
  show('0');
})();
"""


def card_html(m):
    summaries = ""
    if m["english_summary"]:
        summaries += ('<div class="summary-box"><span class="summary-label">English Summary</span>'
                      f'{escape(m["english_summary"])}</div>')
    if m["mandarin_summary"]:
        summaries += ('<div class="summary-box mandarin-box" lang="zh">'
                      '<span class="summary-label">Mandarin Summary (中文摘要)</span>'
                      f'{escape(m["mandarin_summary"])}</div>')
    abstract = (f'<details><summary>Show abstract</summary><p>{escape(m["abstract"])}</p></details>'
                if m["abstract"] else "")
    return f"""
    <div class="card">
      <span class="date-badge">{escape(m['pub_date'] or 'Date N/A')}</span>
      <a href="{escape(m['url'], quote=True)}" class="title" target="_blank" rel="noopener">{escape(m['title'])}</a>
      <div class="authors"><strong>Authors:</strong> {escape(m['authors'] or 'N/A')}</div>
      {summaries}
      {abstract}
      <div class="keywords">Matched keywords: {escape(m['matched_keywords'])}</div>
    </div>"""


def save_html(records, title, filename):
    """One tab per journal; papers inside each tab sorted by publication date, newest first."""
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
        f'<section class="panel" data-tab="{i}">'
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
  <nav class="tabs">{tab_buttons}</nav>
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
def main():
    try:
        old_title, records = parse_old_html(INPUT_HTML)
    except FileNotFoundError:
        sys.exit(f"Input file not found: {INPUT_HTML}. Upload it to Colab or fix INPUT_HTML.")
    if not records:
        sys.exit("No papers found in the input file. Is it the report made by the earlier script?")
    print(f"Read {len(records)} papers from {INPUT_HTML}")

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
    main()
