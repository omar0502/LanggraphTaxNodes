"""
cli.py - run the parallel-run comparison from a terminal.

    python -m parallel_run.cli                       (uses the bundled sample files)
    python -m parallel_run.cli --legacy a.csv --new b.csv --crosswalk c.csv --out exceptions.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .compare import compare_engines

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "parallel_run"


def main() -> None:
    p = argparse.ArgumentParser(description="Compare legacy vs. new tax engine results")
    p.add_argument("--legacy", default=str(SAMPLES / "legacy_engine.csv"))
    p.add_argument("--new", default=str(SAMPLES / "new_engine.csv"))
    p.add_argument("--crosswalk", default=str(SAMPLES / "tax_code_crosswalk.csv"))
    p.add_argument("--tolerance", type=float, default=0.0, help="auto-clear differences up to this amount per line")
    p.add_argument("--out", default="exceptions.csv", help="where to write the exception list")
    a = p.parse_args()

    result = compare_engines(a.legacy, a.new, a.crosswalk or None, tolerance=a.tolerance)
    s = result["summary"]

    print(f"\nLines compared : {s['lines_compared']:,}")
    print(f"Matched        : {s['lines_matched']:,}  ({s['match_rate']:.1%})")
    print(f"Exceptions     : {s['exceptions']:,}")
    print(f"Tax legacy/new : {s['legacy_tax_total']:,.2f} / {s['new_tax_total']:,.2f}  (net {s['net_variance']:+,.2f})\n")
    print(f"{'Root cause':22s}{'Lines':>7s}{'Share':>8s}{'Net $':>11s}")
    for cause, c in s["by_root_cause"].items():
        print(f"{cause:22s}{c['lines']:>7d}{c['share_of_exceptions']:>8.0%}{c['net_variance']:>11,.2f}")
    if s["mapping_check"] and s["mapping_check"]["skus_mapped_differently_than_crosswalk"]:
        m = s["mapping_check"]
        print(f"\nMapping check: {len(m['skus_mapped_differently_than_crosswalk'])} SKU(s) mapped differently than the crosswalk, "
              f"on {m['lines_on_those_skus']} lines; tax changed on {m['lines_where_it_changed_tax']} of them so far.")

    cols = ["order_id", "line_id", "state", "line_type", "sku", "legacy_tax_code", "new_tax_code",
            "legacy_tax", "new_tax", "variance", "root_cause", "evidence", "recommended_action"]
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(result["exceptions"])
    print(f"\nException list written to {a.out}")


if __name__ == "__main__":
    main()
