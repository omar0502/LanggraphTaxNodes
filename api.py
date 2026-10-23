"""
api.py - REST API for the tax validation graph.

Run:  uvicorn api:app --reload --port 8000
Docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import xmltodict
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from tax_graph import DEFAULT_RATE_TABLE, DEFAULT_TOLERANCE, explain_result, run_tax_validation
from xml_utils import map_xml_to_tx

app = FastAPI(title="Tax Validation Agent API", version="0.4.0")

# Browser front ends (e.g. a Lovable app) need CORS. Comma-separated list in env var.
_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ValidateRequest(BaseModel):
    tx: Dict[str, Any] = Field(..., description="Transaction payload (invoice/document)")
    ctx: Optional[Dict[str, Any]] = Field(default=None, description="Optional: rate_table, tolerance")


class BatchRequest(BaseModel):
    items: List[ValidateRequest]


class ValidateResponse(BaseModel):
    passed: bool
    status: str
    failed_at: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    calc_tax: Optional[float] = None
    supplier_tax: Optional[float] = None
    variance: Optional[float] = None
    rate: Optional[float] = None
    pos_region: Optional[str] = None
    path: List[str] = Field(default_factory=list)
    checks_not_implemented: List[str] = Field(default_factory=list)
    results: Dict[str, Any] = Field(default_factory=dict)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest):
    return run_tax_validation(req.tx, req.ctx or {})


@app.post("/batch")
def batch(req: BatchRequest):
    results = [run_tax_validation(i.tx, i.ctx or {}) for i in req.items]
    passed = sum(1 for r in results if r["passed"])
    return {"results": results, "passed_count": passed, "needs_review_count": len(results) - passed}


@app.post("/explain")
def explain(req: ValidateRequest):
    report = run_tax_validation(req.tx, req.ctx or {})
    return {"summary": explain_result(report), "report": report}


@app.post("/validate/xml", response_model=ValidateResponse)
async def validate_xml(file: UploadFile = File(...)):
    content = await file.read()
    try:
        doc = xmltodict.parse(content)
    except Exception as exc:  # malformed XML is the caller's problem, not a server crash
        raise HTTPException(status_code=400, detail=f"Could not parse XML: {exc}") from exc
    tx = map_xml_to_tx(doc)
    ctx = {"rate_table": DEFAULT_RATE_TABLE, "tolerance": DEFAULT_TOLERANCE}
    return run_tax_validation(tx, ctx)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
