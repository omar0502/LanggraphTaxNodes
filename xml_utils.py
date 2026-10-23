"""
xml_utils.py - map a UBL / Peppol-style Invoice (parsed with xmltodict) into the tx schema.

Anything the XML does not contain comes back as None, so the validation
graph flags it instead of the mapper quietly filling in a value.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _get(d: Any, path: List[str], default: Any = None) -> Any:
    """Nested get that ignores namespace prefixes (cbc:, cac:, ns0:, ...)."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        if key in cur:
            cur = cur[key]
            continue
        plain = key.split(":")[-1]
        matches = [k for k in cur.keys() if k.split(":")[-1] == plain]
        if not matches:
            return default
        cur = cur[matches[0]]
    return cur


def _text(v: Any) -> Optional[str]:
    """xmltodict gives {'@attr': ..., '#text': ...} when a tag has attributes."""
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("#text")
    if isinstance(v, list):  # repeated tag - take the first
        return _text(v[0]) if v else None
    s = str(v).strip() if v is not None else ""
    return s or None


def _to_number(v: Any) -> Optional[float]:
    s = _text(v)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _first(v: Any) -> Any:
    return v[0] if isinstance(v, list) and v else v


def _country(party_or_location: Any) -> Optional[str]:
    for path in (
        ["PostalAddress", "Country", "IdentificationCode"],
        ["Address", "Country", "IdentificationCode"],
        ["Party", "PostalAddress", "Country", "IdentificationCode"],
    ):
        code = _text(_get(party_or_location, path))
        if code:
            return code.upper()
    return None


def map_xml_to_tx(doc: Dict[str, Any]) -> Dict[str, Any]:
    root_key = next((k for k in doc if k.split(":")[-1] == "Invoice"), None)
    inv = doc[root_key] if root_key else doc

    supplier_party = _get(inv, ["AccountingSupplierParty", "Party"]) or {}
    customer_party = _get(inv, ["AccountingCustomerParty", "Party"]) or {}
    delivery = _first(_get(inv, ["Delivery"])) or {}

    supplier_country = _country(supplier_party)
    customer_country = _country(customer_party)
    ship_to_country = _country(_get(delivery, ["DeliveryLocation"]) or {}) or customer_country

    tax_total = _first(_get(inv, ["TaxTotal"])) or {}
    monetary = _get(inv, ["LegalMonetaryTotal"]) or {}

    return {
        "entity_id": _text(_get(customer_party, ["PartyLegalEntity", "CompanyID"])),
        "country": customer_country,
        "doc_date": _text(_get(inv, ["IssueDate"])),
        "currency": _text(_get(inv, ["DocumentCurrencyCode"])),
        "supplier_id": _text(_get(supplier_party, ["PartyLegalEntity", "CompanyID"]))
        or _text(_get(supplier_party, ["PartyTaxScheme", "CompanyID"])),
        "net_amount": _to_number(_get(monetary, ["TaxExclusiveAmount"])),
        "supplier_tax": _to_number(_get(tax_total, ["TaxAmount"])),
        "ship_to_country": ship_to_country,
        "supplier_country": supplier_country,
    }
