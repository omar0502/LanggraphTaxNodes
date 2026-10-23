"""
tax_graph.py - LangGraph workflow of indirect tax controls for a single invoice.

Design rules:
- Every check is deterministic. Nothing here guesses, defaults, or invents values.
- A failed check stops the run and flags it NEEDS_REVIEW with the reason.
- Checks that are not built yet say so (status NOT_IMPLEMENTED) instead of pretending to pass.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

DEFAULT_TOLERANCE = 0.01
DEFAULT_RATE_TABLE: Dict[str, float] = {"DE": 0.19}  # demo only - pass your own rate_table in ctx

REQUIRED_FIELDS = ["entity_id", "doc_date", "currency", "supplier_id", "net_amount", "supplier_tax"]

EU_COUNTRIES = set(
    "AT BE BG HR CY CZ DK EE FI FR DE GR EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)


# ---------- State ----------
class TaxState(TypedDict, total=False):
    tx: Dict[str, Any]
    ctx: Dict[str, Any]
    results: Dict[str, Any]
    path: List[str]
    last_check_passed: bool
    last_failed_node: Optional[str]
    pos_region: Optional[Literal["DOMESTIC", "EU", "NON_EU"]]
    awaiting_human: bool


def record(state: TaxState, node: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Store a check result and return the state update.

    The failure is recorded here, inside the node. LangGraph does not save
    changes a router function makes to state, so routers only read.
    """
    passed = bool(payload.get("passed", False))
    update: Dict[str, Any] = {
        "results": {**state.get("results", {}), node: payload},
        "path": [*state.get("path", []), node],
        "last_check_passed": passed,
    }
    if not passed:
        update["last_failed_node"] = node
    return update


def stub(state: TaxState, node: str) -> Dict[str, Any]:
    """Placeholder for a control that is not built yet. Visible in the report."""
    return record(state, node, {"passed": True, "status": "NOT_IMPLEMENTED"})


def _is_missing(value: Any) -> bool:
    # 0 is a valid amount (free items, credits); only None/blank counts as missing
    return value is None or (isinstance(value, str) and value.strip() == "")


# ---------- Nodes ----------
def node_reporting_entity(state: TaxState):
    return stub(state, "1_reporting_entity")


def node_reporting_country(state: TaxState):
    return stub(state, "2_reporting_country")


def node_legal_and_mandatory_fields(state: TaxState):
    tx = state.get("tx", {})
    missing = [k for k in REQUIRED_FIELDS if _is_missing(tx.get(k))]
    return record(state, "legal_mandatory_fields", {
        "passed": not missing,
        "status": "OK" if not missing else "FAILED",
        "missing": missing,
    })


def node_taxability_analysis(state: TaxState):
    return stub(state, "3_taxability_analysis")


def node_product_service_taxability(state: TaxState):
    return stub(state, "3a_product_service_taxability")


def node_gl_taxability(state: TaxState):
    return stub(state, "3b_gl_taxability")


def node_supplier_vat_verification(state: TaxState):
    return stub(state, "4_supplier_vat_verification")


def node_master_data(state: TaxState):
    return stub(state, "4a_master_data")


def node_place_of_supply(state: TaxState):
    tx = state.get("tx", {})
    ship_to, supplier = tx.get("ship_to_country"), tx.get("supplier_country")
    if _is_missing(ship_to) or _is_missing(supplier):
        update = record(state, "5_place_of_supply", {
            "passed": False,
            "status": "FAILED",
            "reason": "ship_to_country and supplier_country are both required to determine place of supply",
        })
        update["pos_region"] = None
        return update

    if ship_to == supplier:
        region = "DOMESTIC"
    elif ship_to in EU_COUNTRIES and supplier in EU_COUNTRIES:
        region = "EU"
    else:
        region = "NON_EU"
    update = record(state, "5_place_of_supply", {"passed": True, "status": "OK", "region": region})
    update["pos_region"] = region
    return update


def node_reverse_charge(state: TaxState):
    return stub(state, "5a_reverse_charge_verification")


def node_eu_intracommunity(state: TaxState):
    return stub(state, "5b_eu_intracommunity")


def node_non_eu_foreign(state: TaxState):
    return stub(state, "5c_non_eu_foreign")


def node_recoverability_rate_block(state: TaxState):
    return stub(state, "6_recoverability_rate_block")


def node_vat_rate_verification(state: TaxState):
    return stub(state, "6a_vat_rate_verification")


def node_vat_recoverability(state: TaxState):
    return stub(state, "6b_vat_recoverability")


def node_invoice_comparison(state: TaxState):
    ctx, tx = state.get("ctx", {}), state.get("tx", {})
    rate_table = ctx.get("rate_table") or DEFAULT_RATE_TABLE
    tolerance = float(ctx.get("tolerance", DEFAULT_TOLERANCE))
    country = tx.get("country") or tx.get("reporting_country")

    if country not in rate_table:
        return record(state, "7_invoice_comparison", {
            "passed": False,
            "status": "FAILED",
            "reason": f"no rate on file for country {country!r}",
            "calc_tax": None,
            "supplier_tax": tx.get("supplier_tax"),
            "rate": None,
        })

    rate = float(rate_table[country])
    net = float(tx["net_amount"])
    calc_tax = round(net * rate, 2)
    supplier_tax = round(float(tx["supplier_tax"]), 2)
    variance = round(supplier_tax - calc_tax, 2)
    passed = abs(variance) <= tolerance
    payload: Dict[str, Any] = {
        "passed": passed,
        "status": "OK" if passed else "FAILED",
        "calc_tax": calc_tax,
        "supplier_tax": supplier_tax,
        "variance": variance,
        "rate": rate,
        "tolerance": tolerance,
    }
    if not passed:
        direction = "over-charged" if variance > 0 else "under-charged"
        payload["reason"] = f"supplier {direction} tax by {abs(variance):.2f}"
    return record(state, "7_invoice_comparison", payload)


# ---------- Exception handling ----------
def node_needs_review(state: TaxState):
    """Stop and flag for a human. Never fills in or changes transaction data."""
    failed = state.get("last_failed_node")
    results = state.get("results", {})
    return {
        "awaiting_human": True,
        "results": {**results, "8_needs_review": {
            "status": "NEEDS_REVIEW",
            "failed_at": failed,
            "detail": results.get(failed),
        }},
    }


# ---------- Routers (read-only) ----------
def pass_fail(state: TaxState) -> Literal["pass", "fail"]:
    return "pass" if state.get("last_check_passed") else "fail"


def route_pos(state: TaxState) -> Literal["DOMESTIC", "EU", "NON_EU", "fail"]:
    if not state.get("last_check_passed"):
        return "fail"
    return state.get("pos_region") or "fail"


# ---------- Graph wiring ----------
def build_graph():
    g = StateGraph(TaxState)
    nodes = [
        ("1_reporting_entity", node_reporting_entity),
        ("2_reporting_country", node_reporting_country),
        ("legal_mandatory_fields", node_legal_and_mandatory_fields),
        ("3_taxability_analysis", node_taxability_analysis),
        ("3a_product_service_taxability", node_product_service_taxability),
        ("3b_gl_taxability", node_gl_taxability),
        ("4_supplier_vat_verification", node_supplier_vat_verification),
        ("4a_master_data", node_master_data),
        ("5_place_of_supply", node_place_of_supply),
        ("5a_reverse_charge_verification", node_reverse_charge),
        ("5b_eu_intracommunity", node_eu_intracommunity),
        ("5c_non_eu_foreign", node_non_eu_foreign),
        ("6_recoverability_rate_block", node_recoverability_rate_block),
        ("6a_vat_rate_verification", node_vat_rate_verification),
        ("6b_vat_recoverability", node_vat_recoverability),
        ("7_invoice_comparison", node_invoice_comparison),
        ("8_needs_review", node_needs_review),
    ]
    for name, func in nodes:
        g.add_node(name, func)

    review = "8_needs_review"
    g.add_edge(START, "1_reporting_entity")
    g.add_edge("1_reporting_entity", "2_reporting_country")
    g.add_conditional_edges("2_reporting_country", pass_fail, {"pass": "legal_mandatory_fields", "fail": review})
    g.add_conditional_edges("legal_mandatory_fields", pass_fail, {"pass": "3_taxability_analysis", "fail": review})
    g.add_edge("3_taxability_analysis", "3a_product_service_taxability")
    g.add_edge("3a_product_service_taxability", "3b_gl_taxability")
    g.add_edge("3b_gl_taxability", "4_supplier_vat_verification")
    g.add_conditional_edges("4_supplier_vat_verification", pass_fail, {"pass": "4a_master_data", "fail": review})
    g.add_edge("4a_master_data", "5_place_of_supply")
    g.add_conditional_edges("5_place_of_supply", route_pos, {
        "DOMESTIC": "5a_reverse_charge_verification",
        "EU": "5b_eu_intracommunity",
        "NON_EU": "5c_non_eu_foreign",
        "fail": review,
    })
    for branch in ("5a_reverse_charge_verification", "5b_eu_intracommunity", "5c_non_eu_foreign"):
        g.add_edge(branch, "6_recoverability_rate_block")
    g.add_edge("6_recoverability_rate_block", "6a_vat_rate_verification")
    g.add_edge("6a_vat_rate_verification", "6b_vat_recoverability")
    g.add_edge("6b_vat_recoverability", "7_invoice_comparison")
    g.add_conditional_edges("7_invoice_comparison", pass_fail, {"pass": END, "fail": review})
    g.add_edge(review, END)
    return g.compile()


graph = build_graph()


# ---------- Public helpers ----------
def run_tax_validation(tx: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    init: TaxState = {
        "tx": dict(tx),  # copy so the caller's data is never mutated
        "ctx": ctx or {},
        "results": {},
        "path": [],
        "awaiting_human": False,
    }
    final = graph.invoke(init)
    results = final.get("results", {})
    inv = results.get("7_invoice_comparison", {})
    legal = results.get("legal_mandatory_fields", {})
    pos = results.get("5_place_of_supply", {})
    review = results.get("8_needs_review")

    failed_detail = (review or {}).get("detail") or {}
    reasons: List[str] = []
    if legal.get("missing"):
        reasons.append(f"missing mandatory fields: {', '.join(legal['missing'])}")
    if failed_detail.get("reason"):
        reasons.append(failed_detail["reason"])

    not_implemented = [k for k, v in results.items() if isinstance(v, dict) and v.get("status") == "NOT_IMPLEMENTED"]

    return {
        "passed": review is None and bool(inv.get("passed")),
        "status": "NEEDS_REVIEW" if review else "PASSED",
        "failed_at": (review or {}).get("failed_at"),
        "reasons": reasons,
        "missing_fields": legal.get("missing", []),
        "calc_tax": inv.get("calc_tax"),
        "supplier_tax": inv.get("supplier_tax"),
        "variance": inv.get("variance"),
        "rate": inv.get("rate"),
        "pos_region": pos.get("region"),
        "path": final.get("path", []),
        "checks_not_implemented": not_implemented,
        "results": results,
    }


def explain_result(report: Dict[str, Any]) -> str:
    """Plain-English summary of a validation report."""
    if not report:
        return "No report generated."
    if report.get("passed"):
        return (
            f"Invoice passed. Calculated tax {report.get('calc_tax')} matches supplier tax "
            f"{report.get('supplier_tax')} at rate {report.get('rate')}. All mandatory fields present."
        )
    msg = f"Invoice needs review (stopped at {report.get('failed_at')})."
    for reason in report.get("reasons", []):
        msg += f" {reason[0].upper()}{reason[1:]}."
    return msg


if __name__ == "__main__":
    from pprint import pprint

    demo_tx = {
        "entity_id": "DE01", "country": "DE", "net_amount": 1000, "supplier_tax": 190,
        "ship_to_country": "DE", "supplier_country": "DE", "doc_date": "2025-10-13",
        "currency": "EUR", "supplier_id": "S1",
    }
    for label, tx in [("clean invoice", demo_tx), ("under-charged by 40", {**demo_tx, "supplier_tax": 150})]:
        report = run_tax_validation(tx, {"rate_table": {"DE": 0.19}, "tolerance": 0.01})
        print(f"\n=== {label} ===")
        print(explain_result(report))
        print("PATH ->", " -> ".join(report["path"]))
