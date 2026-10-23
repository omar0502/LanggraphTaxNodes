import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import app
from parallel_run.compare import InputError, compare_engines
from parallel_run.root_cause import classify
from parallel_run.synthetic import write_samples

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "parallel_run"
client = TestClient(app)


def _score(result, answer_key_path):
    key = {(r["order_id"], r["line_id"]): r["expected_root_cause"]
           for r in csv.DictReader(open(answer_key_path))}
    got = {(e["order_id"], e["line_id"]): e["root_cause"] for e in result["exceptions"]}
    return key, got


def test_bundled_samples_match_answer_key_exactly():
    r = compare_engines(SAMPLES / "legacy_engine.csv", SAMPLES / "new_engine.csv", SAMPLES / "tax_code_crosswalk.csv")
    key, got = _score(r, SAMPLES / "answer_key.csv")
    assert got == key  # every planted difference found, right cause, no false alarms


@pytest.mark.parametrize("seed", [1, 11, 99])
def test_fresh_random_data_matches_answer_key(tmp_path, seed):
    p = write_samples(tmp_path, orders=500, seed=seed)
    r = compare_engines(p["legacy"], p["new"], p["crosswalk"])
    key, got = _score(r, p["answer_key"])
    assert got == key


def test_summary_adds_up():
    s = compare_engines(SAMPLES / "legacy_engine.csv", SAMPLES / "new_engine.csv", SAMPLES / "tax_code_crosswalk.csv")["summary"]
    assert s["lines_matched"] + s["exceptions"] == s["lines_compared"]
    assert sum(c["lines"] for c in s["by_root_cause"].values()) == s["exceptions"]
    assert round(sum(c["net_variance"] for c in s["by_root_cause"].values()), 2) == s["net_variance"]


def test_mapping_check_finds_mismapped_skus_even_without_tax_impact():
    m = compare_engines(SAMPLES / "legacy_engine.csv", SAMPLES / "new_engine.csv",
                        SAMPLES / "tax_code_crosswalk.csv")["summary"]["mapping_check"]
    assert {x["sku"] for x in m["skus_mapped_differently_than_crosswalk"]} == {"SKU-K01", "SKU-K02"}
    assert m["lines_on_those_skus"] > m["lines_where_it_changed_tax"] > 0


def test_tolerance_auto_clears_rounding():
    r = compare_engines(SAMPLES / "legacy_engine.csv", SAMPLES / "new_engine.csv",
                        SAMPLES / "tax_code_crosswalk.csv", tolerance=0.02)
    assert "ROUNDING" not in r["summary"]["by_root_cause"]


def test_missing_columns_raise_input_error(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("order_id,line_id\nA,1\n")
    with pytest.raises(InputError):
        compare_engines(bad, SAMPLES / "new_engine.csv")


def _side(code, taxable, juris):
    return {"tax_code": code, "taxable_amount": taxable,
            "jurisdictions": [{"level": l, "name": n, "rate": r, "tax": 0} for l, n, r in juris]}


def test_classifier_rate_case_by_hand():
    j = [("STATE", "XX STATE", 0.05), ("COUNTY", "XX COUNTY 1", 0.01)]
    j2 = [("STATE", "XX STATE", 0.05), ("COUNTY", "XX COUNTY 1", 0.0125)]
    case = {"order_id": "O1", "line_id": "1", "state": "XX", "line_type": "PRODUCT", "sku": "S",
            "legacy": _side("A", 100, j), "new": _side("B", 100, j2),
            "expected_new_code": "B", "crosswalk_loaded": True, "variance": 0.25}
    out = classify(case)
    assert out["root_cause"] == "RATE"
    assert out["path"][-1] == "check_rate"


def test_api_demo_endpoint():
    r = client.get("/compare/demo")
    assert r.status_code == 200
    assert r.json()["summary"]["exceptions"] > 0


def test_api_upload_endpoint():
    files = {
        "legacy_file": ("legacy.csv", (SAMPLES / "legacy_engine.csv").read_bytes(), "text/csv"),
        "new_file": ("new.csv", (SAMPLES / "new_engine.csv").read_bytes(), "text/csv"),
        "crosswalk_file": ("cw.csv", (SAMPLES / "tax_code_crosswalk.csv").read_bytes(), "text/csv"),
    }
    r = client.post("/compare", files=files)
    assert r.status_code == 200
    assert r.json()["summary"]["mapping_check"] is not None


def test_api_bad_upload_is_400():
    files = {
        "legacy_file": ("legacy.csv", b"order_id,line_id\nA,1\n", "text/csv"),
        "new_file": ("new.csv", (SAMPLES / "new_engine.csv").read_bytes(), "text/csv"),
    }
    assert client.post("/compare", files=files).status_code == 400
