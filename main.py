# pip install langgraph langchain-core pydantic

from __future__ import annotations
from typing import TypedDict, Dict, Any, Optional, Literal, List
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command

# ---------- State ----------
class TaxState(TypedDict, total=False):
    tx: Dict[str, Any]                 # transaction payload
    ctx: Dict[str, Any]                # config / reference data
    results: Dict[str, Any]            # node outputs
    last_check_passed: bool            # last control result (bool)
    pos_region: Optional[Literal["DOMESTIC","EU","NON_EU"]]
    confidence: float
    awaiting_human: bool
    path: List[str]                    # execution trace
    last_failed_node: Optional[str]    # for resuming after HITL


def ok(state: TaxState, node: str, payload: Any = True) -> TaxState:
    """Record node output, mark pass/fail, and append to execution path."""
    state["results"] = {**state.get("results", {}), node: payload}
    # if the node returned {'passed': bool, ...}, honor that; else bool(payload)
    passed = payload.get("passed") if isinstance(payload, dict) and "passed" in payload else bool(payload)
    state["last_check_passed"] = bool(passed)
    state["path"] = [*state.get("path", []), node]
    return state


# ---------- Nodes (1:1 with your slide) ----------
def node_reporting_entity(state: TaxState):
    # 1. Reporting Entity
    # TODO: validate tx['entity_id'] exists & is in scope
    return ok(state, "1_reporting_entity", True)


def node_reporting_country(state: TaxState):
    # 2. Reporting Country
    # TODO: infer/reporting_country from entity; compare to tx.country
    return ok(state, "2_reporting_country", True)


def node_legal_and_mandatory_fields(state: TaxState):
    # Legal & Mandatory Field Validation
    required = ["entity_id", "doc_date", "currency", "supplier_id", "net_amount"]
    tx = state.get("tx", {})
    missing = [k for k in required if not tx.get(k)]
    passed = len(missing) == 0
    return ok(state, "legal_mandatory_fields", {"passed": passed, "missing": missing})


def node_taxability_analysis(state: TaxState):
    # 3. Taxability Analysis
    return ok(state, "3_taxability_analysis", True)


def node_product_service_taxability(state: TaxState):
    # 3a. Product/Service Taxability Analysis
    return ok(state, "3a_product_service_taxability", True)


def node_gl_taxability(state: TaxState):
    # 3b. GL Taxability Analysis
    return ok(state, "3b_gl_taxability", True)


def node_supplier_vat_verification(state: TaxState):
    # 4. Supplier VAT Number Verification (stubbed)
    passed = True
    return ok(state, "4_supplier_vat_verification", passed)


def node_master_data(state: TaxState):
    # 4a. Master Data enrichment (stub)
    return ok(state, "4a_master_data", True)


def node_place_of_supply(state: TaxState):
    # 5. Place of Supply Analysis → set pos_region
    tx = state.get("tx", {})
    ship_to = tx.get("ship_to_country")
    supplier = tx.get("supplier_country")
    eu = set("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())

    if ship_to and supplier and ship_to == supplier:
        region = "DOMESTIC"
    elif ship_to in eu and supplier in eu:
        region = "EU"
    else:
        region = "NON_EU"

    state["pos_region"] = region
    return ok(state, "5_place_of_supply", {"region": region})


def node_reverse_charge_inout(state: TaxState):
    # 5a. Domestic or Reverse Charge verification (stub)
    return ok(state, "5a_reverse_charge_verification", True)


def node_eu_intracommunity(state: TaxState):
    # 5b. EU (Intracommunity) checks (stub)
    return ok(state, "5b_eu_intracommunity", True)


def node_non_eu_foreign(state: TaxState):
    # 5c. Non-EU (Foreign) checks (stub)
    return ok(state, "5c_non_eu_foreign", True)


def node_vat_recoverability_and_rate_block(state: TaxState):
    # 6. Container node
    return ok(state, "6_recoverability_rate_block", True)


def node_vat_rate_verification(state: TaxState):
    # 6a. VAT Rate Verification (stub)
    return ok(state, "6a_vat_rate_verification", True)


def node_vat_recoverability(state: TaxState):
    # 6b. VAT Recoverability (stub)
    return ok(state, "6b_vat_recoverability", True)


def node_invoice_comparison(state: TaxState):
    """
    7. Invoice Comparison (Taxability Prediction vs Supplier Invoice)
    """
    ctx = state.get("ctx", {})
    tx = state.get("tx", {})
    rate_table = ctx.get("rate_table", {"DE": 0.19, "ES": 0.21, "US-TX": 0.0825})
    country = tx.get("country") or tx.get("reporting_country")
    rate = rate_table.get(country, 0.0)

    net = float(tx.get("net_amount") or 0.0)
    calc_tax = round(net * rate, 2)
    supplier_tax = round(float(tx.get("supplier_tax") or 0.0), 2)
    mismatch = abs(calc_tax - supplier_tax) > float(ctx.get("tolerance", 0.02))

    # set confidence (toy heuristic)
    confidence = 0.98 if not mismatch else 0.6
    state["confidence"] = confidence

    return ok(
        state,
        "7_invoice_comparison",
        {"passed": not mismatch, "calc_tax": calc_tax, "supplier_tax": supplier_tax, "rate": rate, "confidence": confidence}
    )


def node_failed_controls_and_validations(state: TaxState):
    """
    8. Failed Controls & Validations — Human in the Loop
    Demo behavior:
      - auto-remediate common missing fields from ctx.defaults
      - jump back to the failing node (or legal_mandatory_fields if unknown)
    """
    state["awaiting_human"] = True
    tx = state.get("tx", {})
    defaults = state.get("ctx", {}).get("defaults", {
        "doc_date": "2025-10-13",
        "currency": "EUR",
        "supplier_id": "SIM-SUP",
        "net_amount": 1000.0,
        "supplier_tax": 190.0,  # aligns with DE 19% on 1000
        "ship_to_country": tx.get("country") or "DE",
        "supplier_country": tx.get("country") or "DE",
    })

    # Try to fix common failures if the last failure was missing mandatory fields
    last_fail = state.get("last_failed_node") or "legal_mandatory_fields"
    if last_fail == "legal_mandatory_fields":
        for k in ("doc_date", "currency", "supplier_id", "net_amount"):
            tx.setdefault(k, defaults.get(k))
        state["tx"] = tx

    state["awaiting_human"] = False
    state["results"]["8_failed_controls_hitl"] = {
        "approved": True,
        "comment": f"Remediated and resuming from {last_fail}."
    }
    # Jump back to the failed node (rerun the gate)
    return Command(update=state, goto=last_fail)


# ---------- Routers ----------
def pass_fail(state: TaxState) -> Literal["pass","fail"]:
    # Ensure path exists
    state.setdefault("path", [])
    # Use last node recorded in path, else last key in results
    last_key = state["path"][-1] if state["path"] else next(reversed(state.get("results", {})), None)
    v = state.get("results", {}).get(last_key)
    passed = v.get("passed") if isinstance(v, dict) and "passed" in v else bool(v)
    if not passed and last_key:
        state["last_failed_node"] = last_key
    return "pass" if passed else "fail"


def route_pos(state: TaxState) -> Literal["DOMESTIC","EU","NON_EU"]:
    return state.get("pos_region", "DOMESTIC")


# ---------- Graph wiring ----------
graph = StateGraph(TaxState)

# register nodes (names mirror slide labels)
graph.add_node("1_reporting_entity", node_reporting_entity)
graph.add_node("2_reporting_country", node_reporting_country)
graph.add_node("legal_mandatory_fields", node_legal_and_mandatory_fields)

graph.add_node("3_taxability_analysis", node_taxability_analysis)
graph.add_node("3a_product_service_taxability", node_product_service_taxability)
graph.add_node("3b_gl_taxability", node_gl_taxability)

graph.add_node("4_supplier_vat_verification", node_supplier_vat_verification)
graph.add_node("4a_master_data", node_master_data)

graph.add_node("5_place_of_supply", node_place_of_supply)
graph.add_node("5a_reverse_charge_verification", node_reverse_charge_inout)
graph.add_node("5b_eu_intracommunity", node_eu_intracommunity)
graph.add_node("5c_non_eu_foreign", node_non_eu_foreign)

graph.add_node("6_recoverability_rate_block", node_vat_recoverability_and_rate_block)
graph.add_node("6a_vat_rate_verification", node_vat_rate_verification)
graph.add_node("6b_vat_recoverability", node_vat_recoverability)

graph.add_node("7_invoice_comparison", node_invoice_comparison)
graph.add_node("8_failed_controls_hitl", node_failed_controls_and_validations)

# Start → 1
graph.add_edge(START, "1_reporting_entity")

# Linear edges with pass/fail checks after key controls
graph.add_edge("1_reporting_entity", "2_reporting_country")
graph.add_conditional_edges("2_reporting_country", pass_fail, {
    "pass": "legal_mandatory_fields",
    "fail": "8_failed_controls_hitl",
})
graph.add_conditional_edges("legal_mandatory_fields", pass_fail, {
    "pass": "3_taxability_analysis",
    "fail": "8_failed_controls_hitl",
})

# 3 → 3a → 3b
graph.add_edge("3_taxability_analysis", "3a_product_service_taxability")
graph.add_edge("3a_product_service_taxability", "3b_gl_taxability")
graph.add_edge("3b_gl_taxability", "4_supplier_vat_verification")

graph.add_conditional_edges("4_supplier_vat_verification", pass_fail, {
    "pass": "4a_master_data",
    "fail": "8_failed_controls_hitl",
})
graph.add_edge("4a_master_data", "5_place_of_supply")

# POS routing (5a/5b/5c)
graph.add_conditional_edges("5_place_of_supply", route_pos, {
    "DOMESTIC": "5a_reverse_charge_verification",
    "EU": "5b_eu_intracommunity",
    "NON_EU": "5c_non_eu_foreign",
})

# Converge into 6-block
graph.add_edge("5a_reverse_charge_verification", "6_recoverability_rate_block")
graph.add_edge("5b_eu_intracommunity", "6_recoverability_rate_block")
graph.add_edge("5c_non_eu_foreign", "6_recoverability_rate_block")

# 6-block → 6a → 6b → 7
graph.add_edge("6_recoverability_rate_block", "6a_vat_rate_verification")
graph.add_edge("6a_vat_rate_verification", "6b_vat_recoverability")
graph.add_edge("6b_vat_recoverability", "7_invoice_comparison")

# Final gate: if comparison fails, go to Human-in-the-Loop; else END
graph.add_conditional_edges("7_invoice_comparison", pass_fail, {
    "pass": END,
    "fail": "8_failed_controls_hitl",
})

# NOTE: no static edge from HITL to END — the node returns Command(goto=...)
app = graph.compile()


# ---------- Example run ----------
if __name__ == "__main__":
    # Intentionally missing fields to trigger HITL on first pass
    initial: TaxState = {
        "tx": {
            "id": "TX999",
            "entity_id": "DE01",
            "country": "DE",
            # 'doc_date', 'currency', 'supplier_id', 'net_amount' omitted on purpose
            "ship_to_country": "DE",
            "supplier_country": "DE",
            "supplier_tax": 190.0,  # used after remediation fills net_amount=1000
        },
        "ctx": {
            "defaults": {
                "doc_date": "2025-10-13",
                "currency": "EUR",
                "supplier_id": "SIM-SUP",
                "net_amount": 1000.0,
                "supplier_tax": 190.0,
            },
            "rate_table": {"DE": 0.19},
            "tolerance": 0.01,
        },
        "results": {},
        "confidence": 0.0,
        "awaiting_human": False,
        "path": [],
    }

    final_state = app.invoke(initial)
    print("Graph finished. Results:")
    for k, v in final_state["results"].items():
        print(f"- {k}: {v}")
    print("Confidence:", final_state.get("confidence"))
    print("PATH →", " -> ".join(final_state.get("path", [])))
