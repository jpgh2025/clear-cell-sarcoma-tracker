# Clear Cell Sarcoma & GNET Research Tracker

A bilingual (English / 简体中文) public website that tracks research and open clinical
trials for **clear cell sarcoma (CCS)**, **gastrointestinal neuroectodermal tumour (GNET)**
and **EWSR1::ATF1 / EWSR1::CREB1**-driven tumours.

- **Live site:** published from `docs/` with GitHub Pages.
- **Updates:** every day at about 08:07 Hong Kong time (`.github/workflows/update-site.yml`).
- **Sources:** PubMed (NCBI E-utilities) and ClinicalTrials.gov (API v2).

## How it works
1. `tracker.py` queries PubMed and ClinicalTrials.gov and merges results into
   `docs/data/feed.json`. Titles, dates and statuses are copied exactly, each with a PMID or NCT link.
2. `docs/index.html` reads that file in the browser and renders it in either language.
3. **No AI writes or summarises the live data.** If a source fails, the old data is kept,
   the site shows a warning, and the workflow run is marked failed (GitHub emails the owner).

The disease overview, key-evidence cards and family guide in `docs/index.html` are written
by hand from the linked sources. Update them there when important new evidence is published.

## Maintenance
| Task | How |
|---|---|
| Run now | Actions → *Update research tracker* → Run workflow |
| Rebuild data from scratch | Same, and tick **baseline** |
| Change searches | Edit `PUBMED_QUERIES` / `CTGOV_QUERIES` in `tracker.py`, then run `python3 test_tracker.py` |
| Pause | Actions → *Update research tracker* → "…" → Disable workflow |

## Scope and limits
- Clear cell sarcoma **of the kidney** (a different childhood tumour) is excluded.
- China's drug trial registry (chinadrugtrials.org.cn) has no public data feed and is not tracked automatically.
- Conference abstracts (ASCO, ESMO, CTOS) are not tracked automatically.

**Not medical advice.** This project contains no personal patient information.
