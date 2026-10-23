"""
root_cause.py - LangGraph classifier that explains WHY a line differs between engines.

Only lines that already differ are sent here. The graph runs deterministic checks
in a fixed order and stops at the first one that explains the variance. The path
it took is kept as audit evidence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

RECOMMENDED_ACTION = {
    "MISSING_IN_NEW": "Check that the integration sends every line type (e.g. gift cards, shipping) to the new engine.",
    "MISSING_IN_LEGACY": "Confirm whether the legacy extract is incomplete or the new engine is receiving extra lines.",
    "TAXABILITY_MAPPING": "Fix the SKU's tax code in the new engine's product mapping to match the crosswalk.",
    "SHIPPING_TAXABILITY": "Review shipping/freight taxability configuration for this state in the new engine.",
    "TAXABILITY_RULE": "Same tax code but different taxable/exempt outcome: review the new engine's taxability rule for this state.",
    "TAXABLE_BASIS": "Taxable amount differs: check discount allocation and what amount the integration sends.",
    "JURISDICTION": "Different jurisdictions for the same address: compare address resolution (rooftop vs. ZIP-level geocoding).",
    "RATE": "Same jurisdiction, different rate: check rate content versions and effective dates.",
    "ROUNDING": "Cent-level difference: confirm rounding method (line vs. jurisdiction vs. invoice); usually accepted with sign-off.",
    "UNEXPLAINED": "No rule explains this difference: needs manual investigation.",
}

CHECK_ORDER = [
    "check_missing", "check_mapping", "check_shipping", "check_taxability_rule",
    "check_taxable_basis", "check_jurisdiction", "check_rate", "check_rounding",
]


class CaseState(TypedDict, total=False):
    case: Dict[str, Any]
    root_cause: Optional[str]
    evidence: Optional[str]
    notes: List[str]
    path: List[str]


def _step(state: CaseState, node: str, cause: Optional[str] = None, evidence: Optional[str] = None,
          note: Optional[str] = None) -> Dict[str, Any]:
    update: Dict[str, Any] = {"path": [*state.get("path", []), node]}
    if note:
        update["notes"] = [*state.get("notes", []), note]
    if cause:
        update["root_cause"] = cause
        update["evidence"] = evidence
    return update


def _is_taxed(side: Dict[str, Any]) -> bool:
    return float(side.get("taxable_amount") or 0) > 0


def _juris_map(side: Dict[str, Any]) -> Dict[str, float]:
    return {f"{j['level']}|{j['name']}": float(j["rate"]) for j in side.get("jurisdictions", [])}


# ---------- checks ----------
def check_missing(state: CaseState):
    c = state["case"]
    if c.get("new") is None:
        return _step(state, "check_missing", "MISSING_IN_NEW", "line exists in legacy results but not in new engine results")
    if c.get("legacy") is None:
        return _step(state, "check_missing", "MISSING_IN_LEGACY", "line exists in new engine results but not in legacy results")
    return _step(state, "check_missing")


def check_mapping(state: CaseState):
    c = state["case"]
    leg, new = c["legacy"], c["new"]
    expected = c.get("expected_new_code")
    if expected is None:
        if c.get("crosswalk_loaded"):
            return _step(state, "check_mapping", note=f"legacy code {leg['tax_code']} has no crosswalk entry")
        return _step(state, "check_mapping")
    if new["tax_code"] == expected:
        return _step(state, "check_mapping")
    status_changed = _is_taxed(leg) != _is_taxed(new)
    msg = f"legacy code {leg['tax_code']} should map to {expected}, new engine used {new['tax_code']}"
    if status_changed:
        taxed = "taxed" if _is_taxed(new) else "exempt"
        return _step(state, "check_mapping", "TAXABILITY_MAPPING", f"{msg}; line is {taxed} in the new engine")
    return _step(state, "check_mapping", note=f"{msg} (no taxability impact on this line)")


def check_shipping(state: CaseState):
    c = state["case"]
    if c["line_type"] == "SHIPPING" and _is_taxed(c["legacy"]) != _is_taxed(c["new"]):
        who = "new engine taxes" if _is_taxed(c["new"]) else "legacy engine taxes"
        return _step(state, "check_shipping", "SHIPPING_TAXABILITY", f"shipping in {c['state']}: {who} it, the other does not")
    return _step(state, "check_shipping")


def check_taxability_rule(state: CaseState):
    c = state["case"]
    if _is_taxed(c["legacy"]) != _is_taxed(c["new"]):
        who = "taxable in new, exempt in legacy" if _is_taxed(c["new"]) else "exempt in new, taxable in legacy"
        return _step(state, "check_taxability_rule", "TAXABILITY_RULE", f"same tax code but {who} for {c['state']}")
    return _step(state, "check_taxability_rule")


def check_taxable_basis(state: CaseState):
    c = state["case"]
    la, na = float(c["legacy"]["taxable_amount"]), float(c["new"]["taxable_amount"])
    if abs(la - na) > 0.005:
        return _step(state, "check_taxable_basis", "TAXABLE_BASIS", f"taxable amount {la:.2f} in legacy vs {na:.2f} in new")
    return _step(state, "check_taxable_basis")


def check_jurisdiction(state: CaseState):
    c = state["case"]
    lj, nj = _juris_map(c["legacy"]), _juris_map(c["new"])
    only_new, only_leg = sorted(set(nj) - set(lj)), sorted(set(lj) - set(nj))
    if only_new or only_leg:
        parts = []
        if only_new:
            parts.append("only in new: " + ", ".join(k.split("|")[1] for k in only_new))
        if only_leg:
            parts.append("only in legacy: " + ", ".join(k.split("|")[1] for k in only_leg))
        return _step(state, "check_jurisdiction", "JURISDICTION", "; ".join(parts))
    return _step(state, "check_jurisdiction")


def check_rate(state: CaseState):
    c = state["case"]
    lj, nj = _juris_map(c["legacy"]), _juris_map(c["new"])
    diffs = [f"{k.split('|')[1]} {lj[k]:.4%} vs {nj[k]:.4%}" for k in sorted(lj) if abs(lj[k] - nj[k]) > 1e-9]
    if diffs:
        return _step(state, "check_rate", "RATE", "rate differs (legacy vs new): " + "; ".join(diffs))
    return _step(state, "check_rate")


def check_rounding(state: CaseState):
    c = state["case"]
    if abs(c["variance"]) <= c.get("rounding_threshold", 0.02) + 1e-9:
        return _step(state, "check_rounding", "ROUNDING", f"same jurisdictions, rates and basis; difference of {c['variance']:+.2f}")
    return _step(state, "check_rounding")


def unexplained(state: CaseState):
    return _step(state, "unexplained", "UNEXPLAINED",
                 f"same jurisdictions, rates and basis but tax differs by {state['case']['variance']:+.2f}")


# ---------- graph ----------
def _next_or_end(next_node: str):
    def route(state: CaseState) -> Literal["done", "next"]:
        return "done" if state.get("root_cause") else "next"
    return route


def build_graph():
    g = StateGraph(CaseState)
    funcs = {
        "check_missing": check_missing, "check_mapping": check_mapping, "check_shipping": check_shipping,
        "check_taxability_rule": check_taxability_rule, "check_taxable_basis": check_taxable_basis,
        "check_jurisdiction": check_jurisdiction, "check_rate": check_rate, "check_rounding": check_rounding,
        "unexplained": unexplained,
    }
    for name, fn in funcs.items():
        g.add_node(name, fn)
    g.add_edge(START, CHECK_ORDER[0])
    chain = CHECK_ORDER + ["unexplained"]
    for current, nxt in zip(chain, chain[1:]):
        g.add_conditional_edges(current, _next_or_end(nxt), {"done": END, "next": nxt})
    g.add_edge("unexplained", END)
    return g.compile()


graph = build_graph()


def classify(case: Dict[str, Any]) -> Dict[str, Any]:
    final = graph.invoke({"case": case, "root_cause": None, "evidence": None, "notes": [], "path": []})
    cause = final["root_cause"]
    return {
        "root_cause": cause,
        "evidence": final["evidence"],
        "recommended_action": RECOMMENDED_ACTION[cause],
        "notes": final.get("notes", []),
        "path": final.get("path", []),
    }
