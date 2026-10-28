# PubMed Literature Review Generator - Cat Research

A small Python project that searches PubMed for papers in selected journals, keeps only those that are really about your topic (here: **cats / felines**), writes one-sentence **English and Mandarin summaries** with a free LLM, and produces a **browsable HTML report with one tab per journal** plus a **CSV file**.

It is built to be easy to adapt: journals and keywords are plain Python lists at the top of the script.

See final reports: https://elainechenly.github.io/cat_research/

---

## How it works

```
 JOURNALS + KEYWORDS (editable lists)
            │
            ▼
 1. PubMed search (NCBI E-utilities)      journal + keyword + date-window query
            │
            ▼
 2. Fetch full records (XML)              title, authors, abstract, journal, dates, DOI, PMID
            │
            ▼
 3. Strict keyword check                  whole-word match on the abstract (cat/cats, not "category")
            │
            ▼
 4. LLM check + summaries (Gemini free)   drops papers not about cats; adds EN + 中文 one-sentence summaries
            │
            ▼
 5. Sort by publication date (newest first)
            │
            ├──► pubmed_results.html      one tab per journal, papers newest first
            └──► pubmed_results.csv       same papers in a spreadsheet-friendly file
```

### Step details

1. **Search.** The script builds a query such as
   `("Anim Nutr"[Journal] OR "Vet Res"[Journal]) AND (("cat"[Title/Abstract] OR "cats"[Title/Abstract]) OR ...)`
   and sends it to PubMed. A date window (`DAYS_BACK`) limits it to recent papers.
2. **Fetch.** Matching records are downloaded in batches of 200.
3. **Strict keyword check.** PubMed's search is broad, so each abstract is re-checked locally with a whole-word regular expression. Keywords can be single words or phrases, and each paper records which keywords matched.
4. **LLM filter and summaries.** Abstracts are sent to the Gemini API (free tier) in small batches. For each paper the model returns a one-sentence English summary and a one-sentence Mandarin summary, or flags it as not about cats, in which case the paper is removed. If a batch fails (for example when the free quota runs out), those papers are **kept and marked "unchecked"** instead of being silently lost.
5. **Dates and sorting.** Each paper gets a publication date: the online (electronic) date if the record has one, otherwise the journal issue date. Partial dates are kept as they are (`2026-10`, `2026`). Papers are sorted newest first.
6. **Output.** An HTML report with journal tabs and a CSV file are written.

---

## Files

| File | Purpose |
|---|---|
| `cat_research.py` | The main script: search → filter → summarize → HTML + CSV. |

---

## Requirements

- Python 3.8+
- The `requests` package: `pip install requests`
- Internet access
- A free **Gemini API key** (only for the LLM step; see below). Without a key the script still runs, skipping the LLM filter and summaries.

Works as a normal script or pasted into a **Google Colab** cell.

---

## Quick start

1. Open `cat_research.py` and edit only the **SETTINGS** block at the top.
2. (Optional) Add your Gemini key. In Colab, open the key icon (Secrets), add a secret named `GEMINI_API_KEY`, and enable notebook access. Elsewhere, set an environment variable named `GEMINI_API_KEY`. You can also paste the key into `GEMINI_API_KEY = ""`, but then don't share the file.
3. Run it:
   ```
   python cat_research.py
   ```
   In Colab, paste the whole file into a cell and run it.
4. Open `pubmed_results.html` in a browser. The CSV is saved next to it. In Colab, download both from the file panel before the session ends.

---

## Settings

All settings are in the SETTINGS block of `cat_research.py`.

| Setting | What it does |
|---|---|
| `JOURNALS` | List of **PubMed journal abbreviations** (for example `"Anim Nutr"`, `"J Feline Med Surg"`). Add or remove lines freely. |
| `KEYWORDS` | List of words or phrases to look for in abstracts (for example `"cat"`, `"felis catus"`). |
| `MATCH_ALL` | `False` = abstract contains **any** keyword (OR). `True` = **all** keywords (AND). |
| `SUBSTRING` | `False` = whole words only (`cat`, `cats`, not `category`). `True` = loose substring match. |
| `ADD_PLURALS` | Also search each keyword with an added "s" in the PubMed query. |
| `DAYS_BACK` | Only papers from the last N days (`7` = last week, `366` = about a year). `None` = no date limit. |
| `DATE_TYPE` | Date used for the search window: `"pdat"` = publication date, `"edat"` = date added to PubMed. |
| `REPORT_TITLE` | Title shown in the HTML report. |
| `OUTPUT_HTML` / `OUTPUT_CSV` | Output file names. Set either to `None` to skip it. |
| `EMAIL`, `API_KEY` | Optional NCBI details. The email is only used to identify your requests to NCBI, **never to send results**. A key raises NCBI's rate limit. |
| `MAX_RECORDS` | Safety cap on the number of records retrieved. |
| `PRINT_PAPERS` | `False` prints only the summary counts, not every paper. |
| `SUMMARIZE` | `False` skips the LLM step entirely (no key needed). |
| `GEMINI_API_KEY` | Gemini key, or leave empty and use a Colab secret or environment variable. |
| `GEMINI_MODEL` | Gemini model ID. Copy the exact current ID of a Flash-Lite model from Google AI Studio, since model names change and old ones are retired. |
| `SUMMARY_BATCH` | Abstracts per LLM request. Larger batches save requests. |
| `SUMMARY_PAUSE` | Seconds to wait between LLM requests (avoids per-minute limits). |

---

## Output

### HTML report (`pubmed_results.html`)

A single self-contained file you can open in any browser or share.

- A header with the report title, total number of papers, number of journals and the generation date.
- **One tab per journal**, labeled with a paper count, largest journal first. Clicking a tab shows only that journal's papers.
- Inside each tab, papers are sorted by **publication date, newest first**.
- Each paper card shows the date, a linked title (DOI link, or PubMed link if there is no DOI), authors, the English and Mandarin summaries, a "Show abstract" toggle, and the matched keywords.
- Papers the LLM could not check show a yellow **"Not verified by LLM"** badge.

### CSV (`pubmed_results.csv`)

One row per paper, saved with a UTF-8 byte-order mark so Excel displays Mandarin text correctly.

| Column | Content |
|---|---|
| `pub_date` | Publication date (`YYYY-MM-DD`, `YYYY-MM` or `YYYY`) |
| `journal` | Journal title as recorded in PubMed |
| `title` | Paper title |
| `abstract` | Full abstract |
| `english_summary` | One-sentence English summary |
| `mandarin_summary` | One-sentence Mandarin summary |
| `authors` | Authors, separated by `;` |
| `doi`, `pmid`, `url` | Identifiers and a link to the paper |
| `matched_keywords` | Keywords found in the abstract |
| `llm_status` | `verified` (LLM confirmed relevance) or `unchecked` |

---

## Converting an older HTML report

If you already have a single-page report from an earlier version of the script, `cat_research.py` turns it into the tabbed report and a CSV, **reusing the summaries already in the file** (no new LLM requests).

1. Put the old report (for example `index.html`) in the same folder, or upload it in Colab. In Colab the script shows an upload button if it can't find the file.
2. Set `INPUT_HTML` in the settings block and run the script.
3. With `LOOKUP_PUBMED = True`, it looks each paper up in PubMed by DOI to fetch the exact publication date and abstract. Papers PubMed can't match keep their year only.

Outputs: `pubmed_results_tabs.html` and `pubmed_results_tabs.csv`.

---

## Things to know

- **Coverage.** Results come only from PubMed. A journal must be indexed there, and you must use its PubMed abbreviation. If a journal shows no papers, check the abbreviation first. Crossref was tried earlier but is not used, because many publishers, including Elsevier, do not deposit abstracts there.
- **Publication dates.** PubMed treats a date with only a month as the 1st of that month, so a paper from a monthly issue may fall just outside a short `DAYS_BACK` window. If a weekly search seems to miss papers, try `DATE_TYPE = "edat"` or a larger window.
- **Free-tier limits.** The Gemini free tier has low daily request limits that vary by model and are not published as a fixed table. A large search (hundreds of papers) can hit the limit. The script waits and retries on rate-limit errors, and papers it cannot check are kept and marked "unchecked" so you can re-run later.
- **LLM accuracy.** The relevance check and the summaries are produced by a language model and can be wrong. Spot-check important papers against the abstract, which is included in the report.
- **Privacy.** On the free tier, Google may use inputs and outputs to improve its models. Abstracts are public, so this is usually fine, but don't send private text.
- **Be polite to NCBI.** The script stays within NCBI's request limits. For heavy use, add an `EMAIL` and an NCBI `API_KEY`.

---

## Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| `No records found` | Keywords or journal abbreviations don't match, or the date window is too small. Increase `DAYS_BACK` or set it to `None`. |
| A journal never appears | Wrong PubMed abbreviation, or no matching papers in the window. |
| `No Gemini API key found` | Add the key as a Colab secret, environment variable, or in `GEMINI_API_KEY`. The rest of the script still runs without it. |
| Gemini error mentioning the model | `GEMINI_MODEL` is out of date. Copy a current Flash-Lite model ID from Google AI Studio. |
| `Rate limit hit` messages | Free quota is being used up. Increase `SUMMARY_PAUSE`, lower the number of papers, or re-run later. |
| Many papers marked "Not verified by LLM" | The LLM step failed for those batches (quota or errors). Re-run later. |
| Mandarin text looks garbled in Excel | Open the CSV via Data → From Text/CSV and choose UTF-8, or open it in Google Sheets. |
| In Colab: `Input file not found` (converter) | The file isn't in the Colab session. Use the upload button, or set `INPUT_HTML` to the full path such as `/content/index.html`. |

---

## Adapting it to another topic

1. Replace the entries in `KEYWORDS` with your topic's terms.
2. Replace the entries in `JOURNALS` with the PubMed abbreviations you want.
3. Change the topic in the LLM prompt inside `summarize_abstracts()` (it currently says "NOT about felines/cats") and update `REPORT_TITLE`.

---

## Data sources and credits

- Paper metadata and abstracts: [PubMed](https://pubmed.ncbi.nlm.nih.gov/) via NCBI's E-utilities API. Please respect NCBI's usage policies.
- Summaries: [Gemini API](https://ai.google.dev/) (free tier).
- The report links back to each paper's DOI or PubMed page. Copyright of the papers and abstracts remains with their authors and publishers.
