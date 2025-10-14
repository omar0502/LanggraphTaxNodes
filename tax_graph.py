# tax_graph.py
# pip install langgraph langchain-core pydantic

from __future__ import annotations
from typing import TypedDict, Dict, Any, Optional, Literal, List
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from datetime import date

# ---------- State ----------
class TaxState(TypedDict, total=False):
    tx: Dict[str, Any]
    ctx: Dict[str, Any]
    results: Dict[str, Any]
    last_check_passed: bool
    pos_region: Optional[Literal["DOMESTIC","EU","NON_EU"]]
    confidence: float
    awaiting_human: bool
    path: List[str]
    last_failed_node: Optional[str]
    messages: List[Dict[str, str]]

def ok(state: TaxState, node: str, payload: Any = True) -> TaxState:
    state["results"] = {**state.get("results", {}), node: payload}
    passed = payload.get("passed") if isinstance(payload, dict) and "passed" in payload else bool(payload)
    state["last_check_passed"] = bool(passed)
    state["path"] = [*state.get("path", []), node]
    return state

# ---------- Nodes ----------
def node_reporting_entity(state: TaxState): return ok(state, "1_reporting_entity", True)
def node_reporting_country(state: TaxState): return ok(state, "2_reporting_country", True)

def node_legal_and_mandatory_fields(state: TaxState):
    required = ["entity_id", "doc_date", "currency", "supplier_id", "net_amount"]
    tx = state.get("tx", {})
    missing = [k for k in required if not tx.get(k)]
    return ok(state, "legal_mandatory_fields", {"passed": len(missing)==0, "missing": missing})

def node_taxability_analysis(state: TaxState): return ok(state, "3_taxability_analysis", True)
def node_product_service_taxability(state: TaxState): return ok(state, "3a_product_service_taxability", True)
def node_gl_taxability(state: TaxState): return ok(state, "3b_gl_taxability", True)
def node_supplier_vat_verification(state: TaxState): return ok(state, "4_supplier_vat_verification", True)
def node_master_data(state: TaxState): return ok(state, "4a_master_data", True)

def node_place_of_supply(state: TaxState):
    tx = state.get("tx", {})
    ship_to, supplier = tx.get("ship_to_country"), tx.get("supplier_country")
    eu = set("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split())
    if ship_to and supplier and ship_to == supplier: region = "DOMESTIC"
    elif ship_to in eu and supplier in eu: region = "EU"
    else: region = "NON_EU"
    state["pos_region"] = region
    return ok(state, "5_place_of_supply", {"region": region})

def node_reverse_charge_inout(state: TaxState): return ok(state, "5a_reverse_charge_verification", True)
def node_eu_intracommunity(state: TaxState): return ok(state, "5b_eu_intracommunity", True)
def node_non_eu_foreign(state: TaxState): return ok(state, "5c_non_eu_foreign", True)
def node_vat_recoverability_and_rate_block(state: TaxState): return ok(state, "6_recoverability_rate_block", True)
def node_vat_rate_verification(state: TaxState): return ok(state, "6a_vat_rate_verification", True)
def node_vat_recoverability(state: TaxState): return ok(state, "6b_vat_recoverability", True)

def node_invoice_comparison(state: TaxState):
    ctx, tx = state.get("ctx", {}), state.get("tx", {})
    rate_table = ctx.get("rate_table", {"DE": 0.19})
    country = tx.get("country") or tx.get("reporting_country")
    rate = rate_table.get(country, 0.0)
    net = float(tx.get("net_amount") or 0.0)
    calc_tax = round(net * rate, 2)
    supplier_tax = round(float(tx.get("supplier_tax") or 0.0), 2)
    mismatch = abs(calc_tax - supplier_tax) > float(ctx.get("tolerance", 0.02))
    confidence = 0.98 if not mismatch else 0.6
    state["confidence"] = confidence
    return ok(state, "7_invoice_comparison", {"passed": not mismatch, "calc_tax": calc_tax, "supplier_tax": supplier_tax, "rate": rate, "confidence": confidence})

# ---------- HITL ----------
def node_failed_controls_and_validations(state: TaxState):
    state["awaiting_human"] = True
    tx = state.get("tx", {})
    defaults = state.get("ctx", {}).get("defaults", {
        "doc_date": str(date.today()),
        "currency": "EUR",
        "supplier_id": "SIM-SUP",
        "net_amount": 1000.0,
        "supplier_tax": 190.0,
        "ship_to_country": tx.get("country") or "DE",
        "supplier_country": tx.get("country") or "DE",
    })
    last_fail = state.get("last_failed_node") or "legal_mandatory_fields"
    changes = {}
    if last_fail == "legal_mandatory_fields":
        for k in ("doc_date","currency","supplier_id","net_amount"):
            if not tx.get(k):
                tx[k] = defaults.get(k); changes[k] = tx[k]
        state["tx"] = tx
    state["awaiting_human"] = False
    state["results"]["8_failed_controls_hitl"] = {"approved": True,"comment": f"Remediated and resuming from {last_fail}.","changes": changes}
    state["messages"] = [*state.get("messages", []), {"role":"assistant","content":f"HITL fixes for {last_fail}: {changes or 'no-op'}"}]
    return Command(update=state, goto=last_fail)

# ---------- Routers ----------
def pass_fail(state: TaxState) -> Literal["pass","fail"]:
    state.setdefault("path", [])
    last_key = state["path"][-1] if state["path"] else next(reversed(state.get("results", {})), None)
    v = state.get("results", {}).get(last_key)
    passed = v.get("passed") if isinstance(v, dict) and "passed" in v else bool(v)
    if not passed and last_key: state["last_failed_node"] = last_key
    return "pass" if passed else "fail"

def route_pos(state: TaxState) -> Literal["DOMESTIC","EU","NON_EU"]:
    return state.get("pos_region", "DOMESTIC")

# ---------- Graph wiring ----------
graph = StateGraph(TaxState)
for name, func in [
    ("1_reporting_entity", node_reporting_entity),
    ("2_reporting_country", node_reporting_country),
    ("legal_mandatory_fields", node_legal_and_mandatory_fields),
    ("3_taxability_analysis", node_taxability_analysis),
    ("3a_product_service_taxability", node_product_service_taxability),
    ("3b_gl_taxability", node_gl_taxability),
    ("4_supplier_vat_verification", node_supplier_vat_verification),
    ("4a_master_data", node_master_data),
    ("5_place_of_supply", node_place_of_supply),
    ("5a_reverse_charge_verification", node_reverse_charge_inout),
    ("5b_eu_intracommunity", node_eu_intracommunity),
    ("5c_non_eu_foreign", node_non_eu_foreign),
    ("6_recoverability_rate_block", node_vat_recoverability_and_rate_block),
    ("6a_vat_rate_verification", node_vat_rate_verification),
    ("6b_vat_recoverability", node_vat_recoverability),
    ("7_invoice_comparison", node_invoice_comparison),
    ("8_failed_controls_hitl", node_failed_controls_and_validations)
]: graph.add_node(name, func)

graph.add_edge(START, "1_reporting_entity")
graph.add_edge("1_reporting_entity","2_reporting_country")
graph.add_conditional_edges("2_reporting_country", pass_fail, {"pass":"legal_mandatory_fields","fail":"8_failed_controls_hitl"})
graph.add_conditional_edges("legal_mandatory_fields", pass_fail, {"pass":"3_taxability_analysis","fail":"8_failed_controls_hitl"})
graph.add_edge("3_taxability_analysis","3a_product_service_taxability")
graph.add_edge("3a_product_service_taxability","3b_gl_taxability")
graph.add_edge("3b_gl_taxability","4_supplier_vat_verification")
graph.add_conditional_edges("4_supplier_vat_verification", pass_fail, {"pass":"4a_master_data","fail":"8_failed_controls_hitl"})
graph.add_edge("4a_master_data","5_place_of_supply")
graph.add_conditional_edges("5_place_of_supply", route_pos, {"DOMESTIC":"5a_reverse_charge_verification","EU":"5b_eu_intracommunity","NON_EU":"5c_non_eu_foreign"})
for edge in ["5a_reverse_charge_verification","5b_eu_intracommunity","5c_non_eu_foreign"]:
    graph.add_edge(edge,"6_recoverability_rate_block")
graph.add_edge("6_recoverability_rate_block","6a_vat_rate_verification")
graph.add_edge("6a_vat_rate_verification","6b_vat_recoverability")
graph.add_edge("6b_vat_recoverability","7_invoice_comparison")
graph.add_conditional_edges("7_invoice_comparison", pass_fail, {"pass":END,"fail":"8_failed_controls_hitl"})
app = graph.compile()

# ---------- Public helper ----------
def run_tax_validation(tx: Dict[str,Any], ctx: Optional[Dict[str,Any]]=None)->Dict[str,Any]:
    ctx=ctx or {}
    init:TaxState={"tx":tx,"ctx":ctx,"results":{},"confidence":0.0,"awaiting_human":False,"path":[],"messages":[]}
    final=app.invoke(init)
    inv=final.get("results",{}).get("7_invoice_comparison",{})
    legal=final.get("results",{}).get("legal_mandatory_fields",{})
    pos=final.get("results",{}).get("5_place_of_supply",{})
    return {
        "passed":bool(inv.get("passed",False)),
        "confidence":float(final.get("confidence",0.0)),
        "missing_fields":legal.get("missing",[]),
        "calc_tax":inv.get("calc_tax"),
        "supplier_tax":inv.get("supplier_tax"),
        "rate":inv.get("rate"),
        "pos_region":pos.get("region"),
        "path":final.get("path",[]),
        "agent_messages":final.get("messages",[]),
        "results":final.get("results",{})
    }

def explain_result(report:Dict[str,Any])->str:
    """Summarize the validation outcome in plain English."""
    if not report: return "No report generated."
    passed=report.get("passed")
    conf=report.get("confidence",0)
    missing=report.get("missing_fields",[])
    calc=report.get("calc_tax"); supplier=report.get("supplier_tax")
    if passed:
        return f"✅ Invoice passed all tax validations with {conf*100:.0f}% confidence. Calculated tax {calc} matches supplier tax {supplier}. All mandatory fields present."
    else:
        msg=f"⚠️ Invoice failed validation with {conf*100:.0f}% confidence."
        if missing: msg+=f" Missing mandatory fields: {', '.join(missing)}."
        if calc and supplier and calc!=supplier: msg+=f" Calculated tax ({calc}) differs from supplier tax ({supplier})."
        return msg
if __name__ == "__main__":
    # quick demo run so "Run ▶" prints something in PyCharm
    demo_tx = {
        "entity_id": "DE01",
        "country": "DE",
        "net_amount": 1000,
        "supplier_tax": 190,
        "ship_to_country": "DE",
        "supplier_country": "DE",
        "doc_date": "2025-10-13",
        "currency": "EUR",
        "supplier_id": "S1",
    }
    demo_ctx = {"rate_table": {"DE": 0.19}, "tolerance": 0.01}

    report = run_tax_validation(demo_tx, demo_ctx)
    from pprint import pprint
    print("\n=== Demo run (tax_graph.py) ===")
    pprint(report)
    print("PATH →", " -> ".join(report.get("path", [])))
