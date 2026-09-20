#!/usr/bin/env python3
"""
Clear cell sarcoma / GNET research tracker.

Fetches PubMed and ClinicalTrials.gov and merges the results into
docs/data/feed.json, which the public website reads.

No AI / LLM step: every title, date and status is copied straight from the
official API, with its PMID or NCT number and a link. If a source fails, the
old data is kept, the failure is recorded (the site shows a warning), and the
script exits with code 1 so GitHub emails the owner.

Usage:
  python3 tracker.py --baseline   # rebuild from scratch (last 5 years of papers)
  python3 tracker.py              # daily update
Python 3.8+, standard library only.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_FILE = os.path.join(HERE, "docs", "data", "feed.json")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "")

# ---- What to watch ---------------------------------------------------------
# "Clear cell sarcoma of the kidney" is a different childhood kidney tumour.
NOT_KIDNEY = (' NOT ("clear cell sarcoma of the kidney"[Title] OR '
              '"clear cell sarcoma of kidney"[Title] OR "CCSK"[Title])')
PUBMED_QUERIES = [
    '("clear cell sarcoma"[Title/Abstract])' + NOT_KIDNEY,
    '("EWSR1-ATF1"[Title/Abstract] OR "EWSR1::ATF1"[Title/Abstract] OR '
    '"EWSR1-CREB1"[Title/Abstract] OR "EWSR1::CREB1"[Title/Abstract])',
    '("gastrointestinal neuroectodermal tumor"[Title/Abstract] OR '
    '"gastrointestinal neuroectodermal tumour"[Title/Abstract] OR '
    '("GNET"[Title/Abstract] AND sarcoma))',
]
TRIAL_PUBTYPES = ("clinical trial", "randomized controlled trial")

# (group, params). Groups, most specific first:
#   ccs       = clear cell sarcoma / GNET
#   ewsr1     = EWSR1 trials (also matches Ewing sarcoma)
#   sts_china = soft-tissue sarcoma trials with a site in China
CTGOV_QUERIES = [
    ("ccs", {"query.cond": "clear cell sarcoma"}),
    ("ccs", {"query.cond": "gastrointestinal neuroectodermal tumor"}),
    ("ewsr1", {"query.term": "EWSR1"}),
    ("sts_china", {"query.cond": "soft tissue sarcoma", "query.locn": "China"}),
]
GROUP_RANK = {"ccs": 0, "ewsr1": 1, "sts_china": 2}
OPEN_STATUSES = "RECRUITING,NOT_YET_RECRUITING"

DAILY_LOOKBACK_DAYS = 30
BASELINE_LOOKBACK_DAYS = 1825  # 5 years


# ---- HTTP ------------------------------------------------------------------
def fetch_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "ccs-tracker/1.0"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - retried, then raised
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url.split('/')[2]} failed after 3 tries: {last_err}")


# ---- PubMed ----------------------------------------------------------------
def pubmed_items(lookback_days):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    common = {"db": "pubmed", "retmode": "json", "tool": "ccs-tracker"}
    if NCBI_EMAIL:
        common["email"] = NCBI_EMAIL
    pmids = []
    for q in PUBMED_QUERIES:
        res = fetch_json(base + "esearch.fcgi", {**common, "term": q,
                         "datetype": "edat", "reldate": lookback_days,
                         "retmax": 3000})
        for pid in res["esearchresult"]["idlist"]:
            if pid not in pmids:
                pmids.append(pid)
        time.sleep(0.4)  # NCBI: max 3 requests/second without an API key
    items = {}
    for i in range(0, len(pmids), 150):
        batch = pmids[i:i + 150]
        res = fetch_json(base + "esummary.fcgi", {**common, "id": ",".join(batch)})
        time.sleep(0.4)
        for pid in batch:
            doc = res["result"].get(pid)
            if not doc or "error" in doc or not doc.get("title"):
                continue  # no verifiable record -> drop, never fill in
            if _is_kidney_only(doc["title"]):
                continue  # childhood kidney tumours are a different disease
            pubtypes = doc.get("pubtype", [])
            doi = next((a.get("value") for a in doc.get("articleids", [])
                        if a.get("idtype") == "doi"), "")
            items[pid] = {
                "title": doc["title"].strip(),
                "journal": doc.get("fulljournalname", ""),
                "pubtypes": pubtypes,
                "is_trial": any(t in p.lower() for p in pubtypes for t in TRIAL_PUBTYPES),
                "focus": bool(FOCUS_RE.search(doc["title"])),
                "pubdate": doc.get("sortpubdate", "")[:10].replace("/", "-"),
                "doi": doi,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            }
    return items


# ---- ClinicalTrials.gov ----------------------------------------------------
# Must appear in a trial's title/conditions/keywords to count as CCS / GNET.
CORE_RE = re.compile(r"clear[\s-]*cell[\s-]*sarcoma|sarcoma,\s*clear[\s-]*cell|ewsr1|gastrointestinal neuroectodermal tumou?r|\bgnet\b|ccslgt|"
                     r"melanoma of soft parts", re.I)
# In a title, marks a paper or trial whose main topic is this disease.
FOCUS_RE = re.compile(r"clear[\s-]*cell[\s-]*sarcoma|sarcoma,\s*clear[\s-]*cell|\bccs\b|gastrointestinal neuroectodermal tumou?r|\bgnet\b|ccslgt|"
                      r"ewsr1[\s:-]*(atf1|creb1)", re.I)


def _is_kidney_only(text):
    return (re.search(r"kidney|renal|wilms", text, re.I)
            and not re.search(r"soft tissue|tendon|aponeuros|gastrointestinal", text, re.I))


def ctgov_items():
    url = "https://clinicaltrials.gov/api/v2/studies"
    found = {}
    for group, q in CTGOV_QUERIES:
        token = None
        for _ in range(10):  # page cap
            params = {**q, "filter.overallStatus": OPEN_STATUSES,
                      "pageSize": 100, "format": "json"}
            if token:
                params["pageToken"] = token
            res = fetch_json(url, params)
            for s in res.get("studies", []):
                ps = s.get("protocolSection", {})
                ident = ps.get("identificationModule", {})
                nct = ident.get("nctId")
                if not nct:
                    continue
                if nct in found and GROUP_RANK[found[nct]["group"]] <= GROUP_RANK[group]:
                    continue
                conditions = ps.get("conditionsModule", {}).get("conditions", [])
                title = ident.get("briefTitle", "").strip()
                keywords = ps.get("conditionsModule", {}).get("keywords", [])
                text = " ".join([title] + conditions + keywords)
                # ClinicalTrials.gov expands searches loosely, so re-check relevance here.
                if group == "ccs" and (not CORE_RE.search(text) or _is_kidney_only(text)):
                    continue
                if group == "sts_china" and not re.search(r"sarcoma", text, re.I):
                    continue
                status = ps.get("statusModule", {})
                locs = ps.get("contactsLocationsModule", {}).get("locations", [])
                countries = sorted({l.get("country", "") for l in locs if l.get("country")})
                found[nct] = {
                    "group": group,
                    "title": title,
                    "focus": bool(FOCUS_RE.search(title)),
                    "status": status.get("overallStatus", ""),
                    "phases": ps.get("designModule", {}).get("phases", []),
                    "conditions": conditions[:6],
                    "sponsor": ps.get("sponsorCollaboratorsModule", {})
                                 .get("leadSponsor", {}).get("name", ""),
                    "countries": countries,
                    "china": "China" in countries,
                    "first_posted": status.get("studyFirstPostDateStruct", {}).get("date", ""),
                    "last_update": status.get("lastUpdatePostDateStruct", {}).get("date", ""),
                    "url": f"https://clinicaltrials.gov/study/{nct}",
                }
            token = res.get("nextPageToken")
            if not token:
                break
    return found


# ---- Feed ------------------------------------------------------------------
def empty_feed(today):
    return {"schema": 1, "baseline_date": today, "updated": "",
            "sources": {}, "papers": {}, "trials": {}}


def load_feed():
    if not os.path.exists(FEED_FILE):
        return None
    with open(FEED_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_feed(feed):
    os.makedirs(os.path.dirname(FEED_FILE), exist_ok=True)
    tmp = FEED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, FEED_FILE)


def merge_papers(feed, items, today):
    for pid, it in items.items():
        old = feed["papers"].get(pid, {})
        feed["papers"][pid] = {**it, "first_seen": old.get("first_seen", today)}


def merge_trials(feed, items, today):
    for nct, t in feed["trials"].items():
        if nct not in items and t.get("open", True):
            t["open"] = False            # no longer listed as recruiting
            t["closed_seen"] = today
    for nct, it in items.items():
        old = feed["trials"].get(nct, {})
        feed["trials"][nct] = {**it, "open": True,
                               "first_seen": old.get("first_seen", today),
                               "last_seen": today}


def run(baseline=False, now=None, fetch_pubmed=pubmed_items, fetch_ctgov=ctgov_items):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    feed = load_feed()
    if baseline or feed is None:
        feed = empty_feed(today)
        baseline = True
    lookback = BASELINE_LOOKBACK_DAYS if baseline else DAILY_LOOKBACK_DAYS

    failed = False
    for name, fetcher, merge in (
            ("pubmed", lambda: fetch_pubmed(lookback), merge_papers),
            ("ctgov", fetch_ctgov, merge_trials)):
        try:
            items = fetcher()
            merge(feed, items, today)
            feed["sources"][name] = {"ok": True, "checked": now.isoformat(timespec="seconds"),
                                     "error": ""}
        except Exception as e:  # noqa: BLE001 - keep old data, record failure
            failed = True
            prev = feed["sources"].get(name, {})
            feed["sources"][name] = {"ok": False, "checked": prev.get("checked", ""),
                                     "error": str(e)[:300],
                                     "failed_at": now.isoformat(timespec="seconds")}
            print(f"FAILED {name}: {e}", file=sys.stderr)

    feed["updated"] = now.isoformat(timespec="seconds")
    save_feed(feed)
    print(f"papers={len(feed['papers'])} trials_open="
          f"{sum(1 for t in feed['trials'].values() if t.get('open'))} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    a = ap.parse_args()
    sys.exit(run(baseline=a.baseline))
