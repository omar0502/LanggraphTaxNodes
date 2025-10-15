# LangGraph Tax Validation Agent

A lightweight **tax validation microservice** that runs a LangGraph workflow of controls (mandatory fields, POS, VAT checks, rate/recoverability, and invoice comparison) with a simple **Human-in-the-Loop** remediation and a **REST API**.

## Endpoints
- `POST /validate` → machine-readable report
- `POST /explain` → human summary + full report
- `POST /batch` → validate multiple items
- Swagger UI: http://127.0.0.1:8000/docs

## Run locally

```bash
# (Windows) in a terminal
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# start API
uvicorn api:app --reload --port 8000
