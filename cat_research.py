# -*- coding: utf-8 -*-
"""Cat Research.ipynb
"""
# -----------------------------------------------------------------------------------------------------------------------
# Find Papers + Summarize Papers （1st .py）
# -----------------------------------------------------------------------------------------------------------------------

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
    main()
