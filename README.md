# LangGraph Tax Validation Agent

A lightweight **tax validation microservice** that executes a LangGraph workflow of controls (mandatory fields, POS, VAT checks, rate/recoverability, and invoice comparison), with a simple **Human-in-the-Loop** remediation and a **REST API**.

## ✨ What it does
- Validates a transaction/invoice JSON through a series of tax controls
- Auto-remediates common issues (demo HITL) and resumes the failed step
- Returns: pass/fail, confidence, calculated vs supplier tax, POS region, full trace
- API endpoints:
  - `POST /validate` → machine-readable report
  - `POST /explain` → human summary + full report
  - `POST /batch` → validate multiple items

## 🧱 Tech
- **LangGraph** for the stateful workflow
- **FastAPI** for the REST API
- Pure Python (no external LLM calls yet)

## 📦 Install

```bash
# Clone
git clone https://github.com/omar0502/LanggraphTaxNodes.git
cd LanggraphTaxNodes

# (Windows) create and activate venv
python -m venv .venv
.\.venv\Scripts\activate

# Install deps
pip install -r requirements.txt
