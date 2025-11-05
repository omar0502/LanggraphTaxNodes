# pip install xmltodict

from __future__ import annotations
from typing import Any, Dict

def _get(d: Dict[str, Any], path: list[str], default=None):
    """
    Safe nested get supporting namespaced tags: tries exact key,
    then falls back to matching the suffix after ':'.
    """
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        if key in cur:
            cur = cur[key]
            continue
        # fall back to namespaceless match
        plain = key.split(":")[-1]
        matches = [k for k in cur.keys() if k.split(":")[-1] == plain]
        if matches:
            cur = cur[matches[0]]
        else:
            return default
    return cur

def _to_number(v, default=0.0) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict) and "#text" in v:
        return _to_number(v["#text"], default)
    try:
        return float(str(v).strip())
    except Exception:
        return default

def map_xml_to_tx(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map a UBL/Peppol-like Invoice XML (already parsed via xmltodict) into our tx schema.
    Adjust paths if your XML uses different tags.
    """
    inv = doc.get("Invoice") or doc.get("ns:Invoice") or doc

    issue_date = _get(inv, ["cbc:IssueDate"]) or _get(inv, ["IssueDate"])
    currency = _get(inv, ["cbc:DocumentCurrencyCode"]) or _get(inv, ["DocumentCurrencyCode"]) or "EUR"

    tax_total = _get(inv, ["TaxTotal"]) or {}
    legal_monetary = _get(inv, ["LegalMonetaryTotal"]) or {}

    net_amount = _to_number(_get(legal_monetary, ["TaxExclusiveAmount"]))
    supplier_tax = _to_number(_get(tax_total, ["TaxAmount"]))

    supplier_id = (
        _get(inv, ["AccountingSupplierParty","Party","PartyLegalEntity","CompanyID"]) or
        _get(inv, ["AccountingSupplierParty","SupplierID"]) or
        "SUPP-UNKNOWN"
    )

    # Defaults for demo – adjust if you want to derive from address nodes
    return {
        "entity_id": "DE01",
        "country": "DE",
        "doc_date": issue_date,
        "currency": currency,
        "supplier_id": supplier_id,
        "net_amount": net_amount,
        "supplier_tax": supplier_tax,
        "ship_to_country": "DE",
        "supplier_country": "DE",
    }