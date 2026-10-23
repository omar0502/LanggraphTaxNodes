![tests](https://github.com/omar0502/LanggraphTaxNodes/actions/workflows/tests.yml/badge.svg)

# LangGraph Tax Validation Agent

Two tools for indirect tax teams, built on **LangGraph**, **DuckDB** and **FastAPI**:

1. **Invoice validator** - runs a single invoice through a graph of tax controls.
2. **Parallel-run comparator** - compares a legacy tax engine's results with a new engine's results line by line (the core test during a tax engine migration) and explains every difference.

Every check is deterministic. When a check fails, the graph stops and flags the invoice `NEEDS_REVIEW` with the exact control and reason. It never fills in, guesses, or changes transaction data.

## 1. Invoice validator

### What it checks today

| Control | Status |
|---|---|
| Legal / mandatory fields (entity, date, currency, supplier, net amount, supplier tax) | Implemented |
| Place of supply (domestic / intra-EU / non-EU) with branch routing | Implemented |
| Invoice comparison: recalculated tax vs. supplier tax, within tolerance | Implemented |
| Missing rate for the reporting country | Fails (no silent 0%) |
| Reporting entity, taxability, supplier VAT ID, master data, reverse charge, recoverability | Placeholder (reported as `NOT_IMPLEMENTED`) |

Placeholders are listed in every response under `checks_not_implemented`, so a report never claims more than it checked.

### Flow

```
START -> reporting entity -> reporting country -> mandatory fields -> taxability (3, 3a, 3b)
      -> supplier VAT -> master data -> place of supply
            |- DOMESTIC -> reverse charge  -|
            |- EU       -> intra-community -|-> recoverability & rate (6, 6a, 6b) -> invoice comparison -> END
            |- NON_EU   -> foreign         -|
Any failed control -> needs review -> END
```

## 2. Parallel-run comparator

During a tax engine migration, the same transactions are priced by the old and the new engine and every difference has to be explained before go-live. This tool does that in two stages:

1. **Bulk match in DuckDB.** Every line is matched by order and line, totals are compared, and anything within tolerance is cleared. This is plain SQL, so it scales to millions of lines.
2. **Root-cause graph in LangGraph.** Only the lines that differ go through a chain of deterministic checks. The first check that explains the difference wins, and the path is kept as audit evidence.

| Root cause | What it means |
|---|---|
| `MISSING_IN_NEW` / `MISSING_IN_LEGACY` | Line only exists in one engine's results |
| `TAXABILITY_MAPPING` | SKU mapped to a different tax code than the crosswalk says, and it changed taxable/exempt |
| `SHIPPING_TAXABILITY` | One engine taxes shipping in that state, the other doesn't |
| `TAXABILITY_RULE` | Same tax code, different taxable/exempt outcome |
| `TAXABLE_BASIS` | Different taxable amount (e.g. discount allocation) |
| `JURISDICTION` | Different jurisdictions for the same address (e.g. rooftop vs. ZIP-level) |
| `RATE` | Same jurisdiction, different rate |
| `ROUNDING` | Cent-level difference with everything else equal |
| `UNEXPLAINED` | No rule explains it - goes to a human |

Each exception comes back with evidence and a recommended next step. A separate **mapping check** flags SKUs whose tax code doesn't match the crosswalk even where it hasn't changed tax yet, because those will cause differences in other states.

### Try it

```bash
python -m parallel_run.cli
```

Output on the bundled sample data:

```
Lines compared : 1,106
Matched        : 937  (84.7%)
Exceptions     : 169
Tax legacy/new : 6,633.03 / 6,816.78  (net +183.75)

Root cause              Lines   Share      Net $
RATE                       43     25%      10.72
SHIPPING_TAXABILITY        40     24%      25.09
TAXABILITY_MAPPING         37     22%     197.07
JURISDICTION               21     12%      10.97
ROUNDING                   13      8%      -0.03
TAXABLE_BASIS               7      4%      -1.71
MISSING_IN_NEW              5      3%     -62.47
UNEXPLAINED                 3      2%       4.11

Mapping check: 2 SKU(s) mapped differently than the crosswalk, on 127 lines; tax changed on 37 of them so far.
```

It also writes `exceptions.csv` with every exception, its evidence and recommended action. Use your own files with `--legacy`, `--new`, `--crosswalk`, and `--tolerance`.

### Input format

One row per order line per jurisdiction. Required columns: `order_id, line_id, ship_to_state, line_type, sku, tax_code, taxable_amount, jurisdiction_level, jurisdiction_name, rate, tax_amount`. The optional crosswalk has `legacy_tax_code, new_tax_code`.

### Synthetic data and the answer key

`samples/parallel_run/` holds **fully synthetic** data from `parallel_run/synthetic.py`. States (XA-XH), jurisdictions, rates, tax codes and taxability rules are invented and do not reflect any real tax law. The generator plants known differences and writes `answer_key.csv`. The tests check that the comparator finds every planted difference with the right root cause and raises no false alarms, on the bundled data and on freshly generated data with different seeds.

Regenerate with:

```bash
python -m parallel_run.synthetic --orders 400 --seed 7 --out samples/parallel_run
```

## Run locally (Windows)

Requires Python 3.10+.

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

uvicorn api:app --reload --port 8000
```

Open http://127.0.0.1:8000/docs for the Swagger UI.

Quick demos without the API:

```bash
python tax_graph.py          # invoice validator
python -m parallel_run.cli   # parallel-run comparator
```

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/validate` | Validate one invoice (JSON), returns the full report |
| POST | `/explain` | Same, plus a plain-English summary |
| POST | `/batch` | Validate `{"items": [...]}`, returns pass / needs-review counts |
| POST | `/validate/xml` | Upload a UBL / Peppol-style invoice XML |
| GET | `/compare/demo` | Run the comparator on the bundled synthetic data |
| POST | `/compare` | Upload `legacy_file`, `new_file` and optional `crosswalk_file` CSVs |

Example request: `samples/sample_invoice.json`. A failing example: `samples/sample_invoice_mismatch.json`. Example XML: `samples/sample_invoice.xml`.

### Example response (abridged)

```json
{
  "passed": false,
  "status": "NEEDS_REVIEW",
  "failed_at": "7_invoice_comparison",
  "reasons": ["supplier under-charged tax by 40.00"],
  "calc_tax": 190.0,
  "supplier_tax": 150.0,
  "variance": -40.0,
  "rate": 0.19,
  "pos_region": "DOMESTIC",
  "checks_not_implemented": ["1_reporting_entity", "..."]
}
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests run automatically on every push via GitHub Actions (`.github/workflows/tests.yml`).

## Calling it from a browser front end

Set the allowed origins before starting the API (comma-separated):

```bash
set ALLOWED_ORIGINS=https://your-app.lovable.app,http://localhost:5173
uvicorn api:app --port 8000
```

## Roadmap

1. **US sales tax rule pack** - jurisdiction-level tax (state, county, city, district), product taxability codes, shipping taxability, exemptions, returns.
2. ~~**Tax engine parallel-run comparator**~~ - done (see section 2).
3. **Engine-to-ledger reconciliation** - calculated tax vs. tax posted in the ERP, by document and by jurisdiction/period.
4. **Real human-in-the-loop** - LangGraph `interrupt()` with a checkpointer and a resume endpoint for reviewer decisions.
5. **LLM assist layer** - plain-English variance summaries, ticket drafts, taxability mapping suggestions with human approval. The LLM never does the tax math.
6. **Review console UI** built in Lovable on top of this API.

## Note on data

All rate tables and sample files are illustrative demo data. Pass your own `rate_table` in `ctx`. Do not commit real transaction data to this repo.
