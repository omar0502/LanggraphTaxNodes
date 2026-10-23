"""
compare.py - bulk comparison of two tax engines' results with DuckDB.

Every line is matched in SQL. Only lines that differ are sent to the
LangGraph root-cause classifier, so this scales to millions of lines.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import duckdb

from .root_cause import RECOMMENDED_ACTION, classify

PathLike = Union[str, Path]

REQUIRED_COLUMNS = [
    "order_id", "line_id", "ship_to_state", "line_type", "sku", "tax_code",
    "taxable_amount", "jurisdiction_level", "jurisdiction_name", "rate", "tax_amount",
]
TEXT_TYPES = {c: "VARCHAR" for c in ["order_id", "line_id", "ship_to_state", "ship_to_zip", "line_type",
                                     "sku", "tax_code", "jurisdiction_level", "jurisdiction_name"]}


class InputError(ValueError):
    """Raised when an input file is missing required columns or can't be read."""


def _load(con: duckdb.DuckDBPyConnection, path: PathLike, table: str, label: str) -> None:
    try:
        rel = con.read_csv(str(path), header=True, dtype=TEXT_TYPES)
    except Exception as exc:
        raise InputError(f"could not read {label} file: {exc}") from exc
    missing = [c for c in REQUIRED_COLUMNS if c not in rel.columns]
    if missing:
        raise InputError(f"{label} file is missing columns: {', '.join(missing)}")
    rel.create(table)


def _side(rows: List[tuple]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    return {
        "tax_code": rows[0][0],
        "taxable_amount": max(float(r[1] or 0) for r in rows),
        "tax": round(sum(float(r[5] or 0) for r in rows), 2),
        "jurisdictions": [{"level": r[2], "name": r[3], "rate": float(r[4] or 0), "tax": float(r[5] or 0)} for r in rows],
    }


def compare_engines(
    legacy_path: PathLike,
    new_path: PathLike,
    crosswalk_path: Optional[PathLike] = None,
    tolerance: float = 0.0,
    rounding_threshold: float = 0.02,
) -> Dict[str, Any]:
    con = duckdb.connect()
    _load(con, legacy_path, "legacy", "legacy engine")
    _load(con, new_path, "new_engine", "new engine")

    crosswalk: Dict[str, str] = {}
    if crosswalk_path:
        try:
            cw = con.read_csv(str(crosswalk_path), header=True, dtype={"legacy_tax_code": "VARCHAR", "new_tax_code": "VARCHAR"})
        except Exception as exc:
            raise InputError(f"could not read crosswalk file: {exc}") from exc
        if not {"legacy_tax_code", "new_tax_code"} <= set(cw.columns):
            raise InputError("crosswalk file needs columns legacy_tax_code, new_tax_code")
        crosswalk = dict(cw.fetchall())

    for src, dst in (("legacy", "l"), ("new_engine", "n")):
        con.execute(f"""
            CREATE TABLE {dst} AS
            SELECT order_id, line_id,
                   any_value(ship_to_state) AS state, any_value(line_type) AS line_type,
                   any_value(sku) AS sku, any_value(tax_code) AS tax_code,
                   max(taxable_amount) AS taxable, round(sum(tax_amount), 2) AS tax
            FROM {src} GROUP BY order_id, line_id
        """)

    con.execute("""
        CREATE TABLE cmp AS
        SELECT coalesce(l.order_id, n.order_id) AS order_id,
               coalesce(l.line_id, n.line_id)   AS line_id,
               coalesce(l.state, n.state)       AS state,
               coalesce(l.line_type, n.line_type) AS line_type,
               coalesce(l.sku, n.sku)           AS sku,
               l.tax_code AS legacy_code, n.tax_code AS new_code,
               l.tax AS legacy_tax, n.tax AS new_tax,
               round(coalesce(n.tax, 0) - coalesce(l.tax, 0), 2) AS variance,
               l.order_id IS NULL AS missing_legacy,
               n.order_id IS NULL AS missing_new
        FROM l FULL OUTER JOIN n ON l.order_id = n.order_id AND l.line_id = n.line_id
    """)
    con.execute("""
        CREATE TABLE exc AS SELECT * FROM cmp
        WHERE missing_legacy OR missing_new OR abs(variance) > ? + 1e-9
    """, [tolerance])

    totals = con.execute("""
        SELECT count(*), round(sum(coalesce(legacy_tax, 0)), 2), round(sum(coalesce(new_tax, 0)), 2)
        FROM cmp
    """).fetchone()
    lines_compared, legacy_total, new_total = int(totals[0]), float(totals[1] or 0), float(totals[2] or 0)

    detail: Dict[tuple, Dict[str, List[tuple]]] = defaultdict(lambda: {"legacy": [], "new": []})
    for side, table in (("legacy", "legacy"), ("new", "new_engine")):
        for row in con.execute(f"""
            SELECT t.order_id, t.line_id, t.tax_code, t.taxable_amount, t.jurisdiction_level,
                   t.jurisdiction_name, t.rate, t.tax_amount
            FROM {table} t JOIN exc e ON t.order_id = e.order_id AND t.line_id = e.line_id
            ORDER BY t.order_id, t.line_id, t.jurisdiction_level, t.jurisdiction_name
        """).fetchall():
            detail[(row[0], row[1])][side].append(row[2:])

    exceptions: List[Dict[str, Any]] = []
    exc_cols = [d[0] for d in con.execute("SELECT * FROM exc LIMIT 0").description]
    for row in con.execute("SELECT * FROM exc").fetchall():
        r = dict(zip(exc_cols, row))
        key = (r["order_id"], r["line_id"])
        legacy_side, new_side = _side(detail[key]["legacy"]), _side(detail[key]["new"])
        case = {
            "order_id": r["order_id"], "line_id": r["line_id"], "state": r["state"],
            "line_type": r["line_type"], "sku": r["sku"],
            "legacy": legacy_side, "new": new_side,
            "expected_new_code": crosswalk.get(legacy_side["tax_code"]) if legacy_side else None,
            "crosswalk_loaded": bool(crosswalk),
            "variance": float(r["variance"]),
            "rounding_threshold": rounding_threshold,
        }
        result = classify(case)
        exceptions.append({
            "order_id": r["order_id"], "line_id": r["line_id"], "state": r["state"],
            "line_type": r["line_type"], "sku": r["sku"],
            "legacy_tax_code": r["legacy_code"], "new_tax_code": r["new_code"],
            "legacy_tax": None if r["legacy_tax"] is None else float(r["legacy_tax"]),
            "new_tax": None if r["new_tax"] is None else float(r["new_tax"]),
            "variance": float(r["variance"]),
            **result,
        })
    exceptions.sort(key=lambda e: (-abs(e["variance"]), e["order_id"], e["line_id"]))

    by_cause: Dict[str, Dict[str, Any]] = {}
    by_state: Dict[str, Dict[str, Any]] = {}
    for e in exceptions:
        c = by_cause.setdefault(e["root_cause"], {"lines": 0, "net_variance": 0.0, "abs_variance": 0.0,
                                                   "recommended_action": RECOMMENDED_ACTION[e["root_cause"]]})
        c["lines"] += 1
        c["net_variance"] += e["variance"]
        c["abs_variance"] += abs(e["variance"])
        s = by_state.setdefault(e["state"], {"lines": 0, "net_variance": 0.0})
        s["lines"] += 1
        s["net_variance"] += e["variance"]
    n_exc = len(exceptions)
    for c in by_cause.values():
        c["net_variance"] = round(c["net_variance"], 2)
        c["abs_variance"] = round(c["abs_variance"], 2)
        c["share_of_exceptions"] = round(c["lines"] / n_exc, 4) if n_exc else 0.0
    for s in by_state.values():
        s["net_variance"] = round(s["net_variance"], 2)

    mapping_check = None
    if crosswalk:
        rows = con.execute("SELECT DISTINCT sku, legacy_code, new_code FROM cmp WHERE legacy_code IS NOT NULL AND new_code IS NOT NULL").fetchall()
        mismatched = sorted({(sku, lc, nc) for sku, lc, nc in rows if crosswalk.get(lc) not in (None, nc)})
        unmapped = sorted({lc for _, lc, _ in rows if lc not in crosswalk})
        mismatched_skus = {m[0] for m in mismatched}
        lines_total = con.execute(
            "SELECT count(*) FROM cmp WHERE sku IN (SELECT unnest(?::VARCHAR[]))", [list(mismatched_skus)]
        ).fetchone()[0] if mismatched_skus else 0
        lines_with_impact = sum(1 for e in exceptions if e["root_cause"] == "TAXABILITY_MAPPING")
        mapping_check = {
            "skus_mapped_differently_than_crosswalk": [
                {"sku": s, "legacy_tax_code": lc, "new_tax_code": nc, "expected_new_tax_code": crosswalk[lc]}
                for s, lc, nc in mismatched
            ],
            "lines_on_those_skus": int(lines_total),
            "lines_where_it_changed_tax": lines_with_impact,
            "legacy_codes_missing_from_crosswalk": unmapped,
        }

    con.close()
    summary = {
        "lines_compared": lines_compared,
        "lines_matched": lines_compared - n_exc,
        "exceptions": n_exc,
        "match_rate": round((lines_compared - n_exc) / lines_compared, 4) if lines_compared else 0.0,
        "legacy_tax_total": legacy_total,
        "new_tax_total": new_total,
        "net_variance": round(new_total - legacy_total, 2),
        "tolerance": tolerance,
        "by_root_cause": dict(sorted(by_cause.items(), key=lambda kv: -kv[1]["lines"])),
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1]["lines"])),
        "mapping_check": mapping_check,
    }
    return {"summary": summary, "exceptions": exceptions}
