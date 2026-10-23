"""
synthetic.py - generate fake US-style eCommerce tax results from two engines.

EVERYTHING HERE IS INVENTED. States (XA..XH), jurisdictions, rates, tax codes
and taxability rules are fictional and do not reflect any real tax law. The
point is to plant known, realistic kinds of differences between a "legacy"
and a "new" engine so the comparator can be tested against an answer key.

Planted differences (each perturbed line gets exactly one cause):
  TAXABILITY_MAPPING  two kids-apparel SKUs are mapped to the wrong tax code in
                      the new engine; it only changes tax in states where kids
                      apparel is exempt (XA, XE)
  SHIPPING_TAXABILITY new engine taxes shipping in XG; legacy does not
  RATE                new engine has a different rate for XC COUNTY 2
  JURISDICTION        for some orders the new engine resolves an extra special
                      district for the same address (rooftop vs. ZIP-level)
  TAXABLE_BASIS       new engine receives a different taxable amount (discount
                      allocated differently by the integration)
  ROUNDING            one-cent rounding differences
  MISSING_IN_NEW      line never reached the new engine
  UNEXPLAINED         tax changed with no visible reason (should go to a human)

Run:  python -m parallel_run.synthetic --orders 400 --seed 7 --out samples/parallel_run
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path
from typing import Dict, List, Optional

COLUMNS = [
    "engine", "order_id", "line_id", "doc_date", "ship_to_state", "ship_to_zip",
    "line_type", "sku", "tax_code", "line_amount", "taxable_amount",
    "jurisdiction_level", "jurisdiction_name", "rate", "tax_amount",
]

# ---- fictional world ---------------------------------------------------------
STATES = {
    #      state rate, shipping taxable, kids apparel exempt
    "XA": (0.0600, False, True),
    "XB": (0.0625, True, False),
    "XC": (0.0450, True, False),
    "XD": (0.0500, False, False),
    "XE": (0.0575, True, True),
    "XF": (0.0650, True, False),
    "XG": (0.0400, False, False),
    "XH": (0.0550, False, False),
}
SHIPPING_STATE_NEW_ENGINE_TAXES = "XG"
RATE_CHANGE_COUNTY = "XC COUNTY 2"
RATE_CHANGE_DELTA = 0.0025
EXTRA_DISTRICT_RATE = 0.0050
JURISDICTION_STATES = {"XB", "XD", "XF", "XH"}

CATALOG = {
    # sku: (category, legacy tax code, price)
    "SKU-F01": ("FOOTWEAR", "LEG-FTW-01", 120.00),
    "SKU-F02": ("FOOTWEAR", "LEG-FTW-01", 89.99),
    "SKU-F03": ("FOOTWEAR", "LEG-FTW-01", 180.00),
    "SKU-A01": ("APPAREL", "LEG-APP-01", 35.00),
    "SKU-A02": ("APPAREL", "LEG-APP-01", 64.99),
    "SKU-A03": ("APPAREL", "LEG-APP-01", 29.99),
    "SKU-K01": ("KIDS_APPAREL", "LEG-KID-01", 24.99),
    "SKU-K02": ("KIDS_APPAREL", "LEG-KID-01", 39.99),
    "SKU-K03": ("KIDS_APPAREL", "LEG-KID-01", 19.99),
    "SKU-C01": ("ACCESSORIES", "LEG-ACC-01", 22.00),
    "SKU-C02": ("ACCESSORIES", "LEG-ACC-01", 45.00),
    "SKU-G01": ("GIFT_CARD", "LEG-GC-01", 50.00),
}
CROSSWALK = {
    "LEG-FTW-01": "NEW-FOOTWEAR",
    "LEG-APP-01": "NEW-APPAREL",
    "LEG-KID-01": "NEW-KIDS-APPAREL",
    "LEG-ACC-01": "NEW-ACCESSORIES",
    "LEG-GC-01": "NEW-GIFT-CARD",
    "LEG-SHIP-01": "NEW-SHIPPING",
}
NEW_CODE_CATEGORY = {
    "NEW-FOOTWEAR": "FOOTWEAR", "NEW-APPAREL": "APPAREL", "NEW-KIDS-APPAREL": "KIDS_APPAREL",
    "NEW-ACCESSORIES": "ACCESSORIES", "NEW-GIFT-CARD": "GIFT_CARD", "NEW-SHIPPING": "SHIPPING",
}
LEGACY_CODE_CATEGORY = {k: NEW_CODE_CATEGORY[v] for k, v in CROSSWALK.items()}
MISMAPPED_IN_NEW_ENGINE = {"SKU-K01": "NEW-APPAREL", "SKU-K02": "NEW-APPAREL"}


def _build_geography(rng: random.Random) -> Dict[str, dict]:
    """zip -> {state, county, city, district?, rates}"""
    zips: Dict[str, dict] = {}
    for state in STATES:
        for c in (1, 2):
            county = f"{state} COUNTY {c}"
            county_rate = rng.choice([0.0050, 0.0075, 0.0100])
            for t in (1, 2):
                city = f"{state} CITY {c}{t}"
                city_rate = rng.choice([0.0100, 0.0125, 0.0150, 0.0200])
                for z in (1, 2):
                    zip_code = f"{state}{c}{t}{z}"
                    district = f"{state} DISTRICT {c}{t}" if z == 2 and t == 1 else None
                    zips[zip_code] = {
                        "state": state, "county": county, "county_rate": county_rate,
                        "city": city, "city_rate": city_rate,
                        "district": district, "district_rate": 0.0025 if district else None,
                    }
    return zips


def _taxable(state: str, category: str) -> bool:
    _, ship_taxable, kids_exempt = STATES[state]
    if category == "GIFT_CARD":
        return False
    if category == "SHIPPING":
        return ship_taxable
    if category == "KIDS_APPAREL":
        return not kids_exempt
    return True


def _jurisdictions(geo: dict, county_rate_override: Optional[float] = None, extra_district: bool = False):
    state = geo["state"]
    rows = [
        ("STATE", f"{state} STATE", STATES[state][0]),
        ("COUNTY", geo["county"], county_rate_override if county_rate_override is not None else geo["county_rate"]),
        ("CITY", geo["city"], geo["city_rate"]),
    ]
    if geo["district"]:
        rows.append(("DISTRICT", geo["district"], geo["district_rate"]))
    if extra_district:
        rows.append(("DISTRICT", f"{state} SPECIAL DISTRICT 9", EXTRA_DISTRICT_RATE))
    return rows


def _price(engine: str, base: dict, tax_code: str, taxable: bool, juris, taxable_amount: Optional[float] = None) -> List[dict]:
    amount = base["line_amount"]
    taxable_amt = (taxable_amount if taxable_amount is not None else amount) if taxable else 0.0
    rows = []
    for level, name, rate in juris:
        rows.append({
            **base, "engine": engine, "tax_code": tax_code,
            "taxable_amount": round(taxable_amt, 2), "jurisdiction_level": level,
            "jurisdiction_name": name, "rate": round(rate, 6),
            "tax_amount": round(taxable_amt * rate, 2),
        })
    return rows


def generate(orders: int = 400, seed: int = 7):
    """Return (legacy_rows, new_rows, answer_key, crosswalk_rows)."""
    rng = random.Random(seed)
    geography = _build_geography(rng)
    zip_codes = sorted(geography)
    skus = sorted(CATALOG)

    legacy: List[dict] = []
    new: List[dict] = []
    answer: List[dict] = []

    for n in range(1, orders + 1):
        order_id = f"ORD-{n:05d}"
        zip_code = rng.choice(zip_codes)
        geo = geography[zip_code]
        state = geo["state"]
        doc_date = f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"

        order_lines = []
        for _ in range(rng.randint(1, 3)):
            sku = rng.choice(skus)
            category, leg_code, price = CATALOG[sku]
            qty = rng.randint(1, 3)
            order_lines.append(("PRODUCT", sku, category, leg_code, round(price * qty, 2)))
        if rng.random() < 0.8:
            order_lines.append(("SHIPPING", "SHIPPING", "SHIPPING", "LEG-SHIP-01", rng.choice([7.95, 9.95, 12.50])))

        jurisdiction_order = state in JURISDICTION_STATES and rng.random() < 0.05

        for i, (line_type, sku, category, leg_code, amount) in enumerate(order_lines, start=1):
            line_id = str(i)
            base = {
                "order_id": order_id, "line_id": line_id, "doc_date": doc_date,
                "ship_to_state": state, "ship_to_zip": zip_code, "line_type": line_type,
                "sku": sku, "line_amount": amount,
            }
            leg_taxable = _taxable(state, category)
            leg_rows = _price("LEGACY", base, leg_code, leg_taxable, _jurisdictions(geo))
            legacy.extend(leg_rows)
            legacy_tax = round(sum(r["tax_amount"] for r in leg_rows), 2)

            # ---- new engine, with exactly one planted cause per line ----
            new_code = MISMAPPED_IN_NEW_ENGINE.get(sku, CROSSWALK[leg_code])
            new_category = NEW_CODE_CATEGORY[new_code]
            new_taxable = _taxable(state, new_category)
            county_override = None
            cause = None

            if new_taxable != leg_taxable and sku in MISMAPPED_IN_NEW_ENGINE:
                cause = "TAXABILITY_MAPPING"
            elif line_type == "SHIPPING" and state == SHIPPING_STATE_NEW_ENGINE_TAXES:
                new_taxable = True
                cause = "SHIPPING_TAXABILITY"
            elif geo["county"] == RATE_CHANGE_COUNTY and leg_taxable:
                county_override = geo["county_rate"] + RATE_CHANGE_DELTA
                cause = "RATE"
            elif jurisdiction_order and leg_taxable:
                cause = "JURISDICTION"

            juris = _jurisdictions(geo, county_override, extra_district=(cause == "JURISDICTION"))
            new_rows = _price("NEW", base, new_code, new_taxable, juris)

            eligible_for_line_noise = (
                cause is None and not jurisdiction_order and line_type == "PRODUCT"
                and legacy_tax > 0 and geo["county"] != RATE_CHANGE_COUNTY
            )
            if eligible_for_line_noise:
                roll = rng.random()
                if roll < 0.03:
                    cause = "ROUNDING"
                    last = new_rows[-1]
                    last["tax_amount"] = round(last["tax_amount"] + (0.01 if rng.random() < 0.5 else -0.01), 2)
                elif roll < 0.05:
                    cause = "TAXABLE_BASIS"
                    discounted = round(amount * 0.95, 2)
                    new_rows = _price("NEW", base, new_code, new_taxable, juris, taxable_amount=discounted)
                elif roll < 0.06:
                    cause = "MISSING_IN_NEW"
                    new_rows = []
                elif roll < 0.065:
                    cause = "UNEXPLAINED"
                    new_rows[0]["tax_amount"] = round(new_rows[0]["tax_amount"] + 1.37, 2)

            new.extend(new_rows)
            new_tax = round(sum(r["tax_amount"] for r in new_rows), 2)
            differs = (not new_rows) or abs(new_tax - legacy_tax) > 0.0001
            if differs:
                if cause is None:  # generator self-check: every difference must be planted
                    raise AssertionError(f"unplanned difference on {order_id}/{line_id}")
                answer.append({"order_id": order_id, "line_id": line_id, "expected_root_cause": cause})

    crosswalk_rows = [{"legacy_tax_code": k, "new_tax_code": v} for k, v in CROSSWALK.items()]
    return legacy, new, answer, crosswalk_rows


def _write(path: Path, rows: List[dict], columns: List[str]):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in columns})


def write_samples(out_dir: Path, orders: int = 400, seed: int = 7) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    legacy, new, answer, crosswalk = generate(orders, seed)
    paths = {
        "legacy": out_dir / "legacy_engine.csv",
        "new": out_dir / "new_engine.csv",
        "crosswalk": out_dir / "tax_code_crosswalk.csv",
        "answer_key": out_dir / "answer_key.csv",
    }
    _write(paths["legacy"], legacy, COLUMNS)
    _write(paths["new"], new, COLUMNS)
    _write(paths["crosswalk"], crosswalk, ["legacy_tax_code", "new_tax_code"])
    _write(paths["answer_key"], answer, ["order_id", "line_id", "expected_root_cause"])
    return paths


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generate synthetic two-engine tax results")
    p.add_argument("--orders", type=int, default=400)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="samples/parallel_run")
    a = p.parse_args()
    paths = write_samples(Path(a.out), a.orders, a.seed)
    for k, v in paths.items():
        print(f"{k:11s} -> {v}")
