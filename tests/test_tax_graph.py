import pytest

from tax_graph import explain_result, run_tax_validation

BASE = {
    "entity_id": "DE01", "country": "DE", "net_amount": 1000, "supplier_tax": 190,
    "ship_to_country": "DE", "supplier_country": "DE", "doc_date": "2025-10-13",
    "currency": "EUR", "supplier_id": "S1",
}
CTX = {"rate_table": {"DE": 0.19, "FR": 0.20}, "tolerance": 0.01}


def test_clean_invoice_passes():
    r = run_tax_validation(BASE, CTX)
    assert r["passed"] is True
    assert r["status"] == "PASSED"
    assert r["calc_tax"] == 190.0
    assert r["pos_region"] == "DOMESTIC"
    assert r["path"][-1] == "7_invoice_comparison"


def test_tax_mismatch_is_flagged_not_crashed():
    r = run_tax_validation({**BASE, "supplier_tax": 150}, CTX)
    assert r["passed"] is False
    assert r["status"] == "NEEDS_REVIEW"
    assert r["failed_at"] == "7_invoice_comparison"
    assert r["variance"] == -40.0
    assert "under-charged" in r["reasons"][0]


def test_missing_fields_are_reported_never_filled_in():
    tx = {k: v for k, v in BASE.items() if k not in ("net_amount", "supplier_tax")}
    r = run_tax_validation(tx, CTX)
    assert r["status"] == "NEEDS_REVIEW"
    assert r["failed_at"] == "legal_mandatory_fields"
    assert set(r["missing_fields"]) == {"net_amount", "supplier_tax"}
    assert r["calc_tax"] is None  # graph stopped before doing any math


def test_caller_data_is_not_mutated():
    tx = {k: v for k, v in BASE.items() if k != "net_amount"}
    run_tax_validation(tx, CTX)
    assert "net_amount" not in tx


def test_country_without_rate_fails():
    r = run_tax_validation({**BASE, "country": "IT", "supplier_tax": 0}, CTX)
    assert r["status"] == "NEEDS_REVIEW"
    assert "no rate on file" in r["reasons"][0]


def test_zero_amount_is_valid():
    r = run_tax_validation({**BASE, "net_amount": 0, "supplier_tax": 0}, CTX)
    assert r["passed"] is True


def test_rounding_inside_tolerance_passes():
    r = run_tax_validation({**BASE, "net_amount": 99.99, "supplier_tax": 19.01}, CTX)
    assert r["calc_tax"] == 19.0
    assert r["passed"] is True


@pytest.mark.parametrize("ship_to,supplier,region,branch", [
    ("DE", "DE", "DOMESTIC", "5a_reverse_charge_verification"),
    ("DE", "FR", "EU", "5b_eu_intracommunity"),
    ("DE", "US", "NON_EU", "5c_non_eu_foreign"),
])
def test_place_of_supply_routing(ship_to, supplier, region, branch):
    r = run_tax_validation({**BASE, "ship_to_country": ship_to, "supplier_country": supplier}, CTX)
    assert r["pos_region"] == region
    assert branch in r["path"]


def test_missing_country_fails_place_of_supply():
    tx = {k: v for k, v in BASE.items() if k != "ship_to_country"}
    r = run_tax_validation(tx, CTX)
    assert r["failed_at"] == "5_place_of_supply"


def test_stubs_are_reported_honestly():
    r = run_tax_validation(BASE, CTX)
    assert "4_supplier_vat_verification" in r["checks_not_implemented"]
    assert "7_invoice_comparison" not in r["checks_not_implemented"]


def test_explain_mentions_reason():
    r = run_tax_validation({**BASE, "supplier_tax": 150}, CTX)
    assert "under-charged" in explain_result(r)
