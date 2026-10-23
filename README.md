# LangGraph Tax Validation Agent

A tax validation microservice that runs an invoice through a **LangGraph** workflow of indirect tax controls and exposes it through a **FastAPI** REST API.

Every check is deterministic. When a check fails, the graph stops and flags the invoice `NEEDS_REVIEW` with the exact control and reason. It never fills in, guesses, or changes transaction data.

## What it checks today

| Control | Status |
|---|---|
| Legal / mandatory fields (entity, date, currency, supplier, net amount, supplier tax) | Implemented |
| Place of supply (domestic / intra-EU / non-EU) with branch routing | Implemented |
| Invoice comparison: recalculated tax vs. supplier tax, within tolerance | Implemented |
| Missing rate for the reporting country | Fails (no silent 0%) |
| Reporting entity, taxability, supplier VAT ID, master data, reverse charge, recoverability | Placeholder (reported as `NOT_IMPLEMENTED`) |

Placeholders are listed in every response under `checks_not_implemented`, so a report never claims more than it checked.

## Flow

```
START -> reporting entity -> reporting country -> mandatory fields -> taxability (3, 3a, 3b)
      -> supplier VAT -> master data -> place of supply
            |- DOMESTIC -> reverse charge  -|
            |- EU       -> intra-community -|-> recoverability & rate (6, 6a, 6b) -> invoice comparison -> END
            |- NON_EU   -> foreign         -|
Any failed control -> needs review -> END
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

Quick demo without the API:

```bash
python tax_graph.py
```

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/validate` | Validate one invoice (JSON), returns the full report |
| POST | `/explain` | Same, plus a plain-English summary |
| POST | `/batch` | Validate `{"items": [...]}`, returns pass / needs-review counts |
| POST | `/validate/xml` | Upload a UBL / Peppol-style invoice XML |

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
2. **Tax engine parallel-run comparator** - compare legacy vs. new engine results line by line in bulk (DuckDB), send only variances through the graph to classify root cause (address/jurisdiction, taxability mapping, shipping, rate, rounding, exemption).
3. **Engine-to-ledger reconciliation** - calculated tax vs. tax posted in the ERP, by document and by jurisdiction/period.
4. **Real human-in-the-loop** - LangGraph `interrupt()` with a checkpointer and a resume endpoint for reviewer decisions.
5. **LLM assist layer** - plain-English variance summaries, ticket drafts, taxability mapping suggestions with human approval. The LLM never does the tax math.
6. **Review console UI** built in Lovable on top of this API.

## Note on data

Rate tables and samples here are illustrative demo data. Pass your own `rate_table` in `ctx`. Do not commit real transaction data to this repo.
