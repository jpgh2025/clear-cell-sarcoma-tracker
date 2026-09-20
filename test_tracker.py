"""Offline tests with fake responses in the real PubMed / ClinicalTrials.gov shapes."""
import datetime as dt
import json
import os
import tempfile

import tracker

WORLD = {"pmids": ["111", "222"], "trials": ["NCT0001"]}


def study(nct, title, conditions, countries):
    return {"protocolSection": {
        "identificationModule": {"nctId": nct, "briefTitle": title},
        "statusModule": {"overallStatus": "RECRUITING",
                         "studyFirstPostDateStruct": {"date": "2026-09-01"}},
        "conditionsModule": {"conditions": conditions},
        "designModule": {"phases": ["PHASE2"]},
        "contactsLocationsModule": {"locations": [{"country": c} for c in countries]}}}


def fake_fetch(url, params):
    if "esearch" in url:
        return {"esearchresult": {"idlist": list(WORLD["pmids"])}}
    if "esummary" in url:
        res = {}
        for pid in params["id"].split(","):
            title = {"555": "Treatment of childhood kidney tumors", "777": "Clear cell sarcoma: a review"}.get(pid, f"Paper {pid}")
            res[pid] = {"title": title, "fulljournalname": "J",
                        "pubtype": ["Clinical Trial, Phase II"] if pid == "333" else ["Journal Article"],
                        "sortpubdate": "2026/09/19 00:00",
                        "articleids": [{"idtype": "doi", "value": f"10.1/{pid}"}]}
        return {"result": res}
    if "clinicaltrials" in url:
        cond = params.get("query.cond", params.get("query.term"))
        if cond == "clear cell sarcoma":
            out = [study(n, "CCS trial", ["Clear Cell Sarcoma"], ["China"]) for n in WORLD["trials"]]
            out.append(study("NCT0KID", "Renal tumours", ["Wilms Tumor", "Clear Cell Sarcoma of the Kidney"], ["United States"]))
            out.append(study("NCT0NET", "Neuroendocrine registry", ["Neuroendocrine Tumors"], ["China"]))
            out.append(study("NCT0MIX", "Agnostic therapy in rare solid tumors", ["Solid Tumor", "Clear Cell Sarcoma"], []))
            out.append(study("NCT0REV", "Devimistat in relapsed soft tissue tumours", ["Sarcoma, Clear Cell"], []))
            return {"studies": out}
        if cond == "EWSR1":
            dup = [study("NCT0001", "dup", ["Sarcoma"], [])] if "NCT0001" in WORLD["trials"] else []
            return {"studies": dup + [study("NCT0002", "Ewing", ["Ewing Sarcoma"], [])]}
        if cond == "soft tissue sarcoma":
            return {"studies": [study("NCT0KRAS", "KRAS G12C lung cancer", ["Kirsten Rat Sarcoma Mutation"], ["China"]),
                                study("NCT0STS", "Anlotinib in STS", ["Soft Tissue Sarcoma"], ["China"])]}
        return {"studies": []}
    raise AssertionError(url)


def setup():
    d = tempfile.mkdtemp()
    tracker.FEED_FILE = os.path.join(d, "docs", "data", "feed.json")
    tracker.fetch_json = fake_fetch
    tracker.time.sleep = lambda s: None
    WORLD.update(pmids=["111", "222"], trials=["NCT0001"])


def day(n):
    return dt.datetime(2026, 9, n, 0, 7, tzinfo=dt.timezone.utc)


def feed():
    return json.load(open(tracker.FEED_FILE, encoding="utf-8"))


def test_baseline_builds_feed():
    setup()
    assert tracker.run(baseline=True, now=day(20)) == 0
    f = feed()
    assert set(f["papers"]) == {"111", "222"} and f["papers"]["111"]["doi"] == "10.1/111"
    assert f["baseline_date"] == "2026-09-20"
    assert f["trials"]["NCT0001"]["group"] == "ccs" and f["trials"]["NCT0001"]["china"]
    assert "NCT0KID" not in f["trials"], "kidney tumour trial must be filtered out"
    assert f["trials"]["NCT0002"]["group"] == "ewsr1"


def test_new_items_get_first_seen_and_old_keep_theirs():
    setup()
    tracker.run(baseline=True, now=day(20))
    WORLD["pmids"] = ["111", "222", "333"]
    tracker.run(now=day(21))
    f = feed()
    assert f["papers"]["333"]["first_seen"] == "2026-09-21" and f["papers"]["333"]["is_trial"]
    assert f["papers"]["111"]["first_seen"] == "2026-09-20"


def test_closed_trial_is_marked():
    setup()
    tracker.run(baseline=True, now=day(20))
    WORLD["trials"] = []
    tracker.run(now=day(22))
    t = feed()["trials"]["NCT0001"]
    assert t["open"] is False and t["closed_seen"] == "2026-09-22"


def test_failure_keeps_data_and_is_recorded():
    setup()
    tracker.run(baseline=True, now=day(20))

    def boom(days):
        raise RuntimeError("timeout")
    code = tracker.run(now=day(21), fetch_pubmed=boom)
    f = feed()
    assert code == 1 and f["sources"]["pubmed"]["ok"] is False
    assert "timeout" in f["sources"]["pubmed"]["error"]
    assert set(f["papers"]) == {"111", "222"}, "old papers must be kept"
    assert f["sources"]["ctgov"]["ok"] is True


def test_relevance_filters_and_focus_flags():
    setup()
    WORLD["pmids"] = ["111", "555", "777"]
    tracker.run(baseline=True, now=day(20))
    f = feed()
    assert "NCT0NET" not in f["trials"], "loosely matched trial must be dropped"
    assert f["trials"]["NCT0MIX"]["group"] == "ccs" and not f["trials"]["NCT0MIX"]["focus"]
    assert f["trials"]["NCT0001"]["focus"] is True  # "CCS" in the title counts as main topic
    assert "NCT0REV" in f["trials"], "reversed condition name must still match"
    assert "NCT0KRAS" not in f["trials"] and f["trials"]["NCT0STS"]["group"] == "sts_china"
    assert "555" not in f["papers"], "kidney-tumour paper must be dropped"
    assert f["papers"]["777"]["focus"] and not f["papers"]["111"]["focus"]


def test_site_data_path_is_inside_docs():
    assert tracker.FEED_FILE.replace("\\", "/").endswith("docs/data/feed.json")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
