# api.py
# pip install fastapi uvicorn xmltodict

import xmltodict
from fastapi import FastAPI, Body, File, UploadFile
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from tax_graph import run_tax_validation, explain_result
from xml_utils import map_xml_to_tx  # <-- NEW

app = FastAPI(title="Tax Validation Agent API", version="0.3.0")

# (optional) auto-redirect "/" -> "/docs"
from fastapi.responses import RedirectResponse
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")

class ValidateRequest(BaseModel):
    tx: Dict[str, Any] = Field(..., description="Transaction payload (invoice/document)")
    ctx: Optional[Dict[str, Any]] = Field(default=None, description="Optional context: rate_table, tolerance, defaults")

class ValidateResponse(BaseModel):
    passed: bool
    confidence: float
    missing_fields: List[str] = Field(default_factory=list)
    calc_tax: Optional[float] = None
    supplier_tax: Optional[float] = None
    rate: Optional[float] = None
    pos_region: Optional[str] = None
    path: List[str] = Field(default_factory=list)
    agent_messages: List[Dict[str, str]] = Field(default_factory=list)
    results: Dict[str, Any] = Field(default_factory=dict)

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest):
    return run_tax_validation(req.tx, req.ctx or {})

@app.post("/batch")
def batch(payload: Dict[str, List[ValidateRequest]] = Body(...)):
    items = payload.get("items", [])
    results = [run_tax_validation(i.tx, i.ctx or {}) for i in items]
    passed = sum(1 for r in results if r["passed"])
    return {"results": results, "passed_count": passed, "failed_count": len(results)-passed}

@app.post("/explain")
def explain(req: ValidateRequest):
    report = run_tax_validation(req.tx, req.ctx or {})
    summary = explain_result(report)
    return {"summary": summary, "report": report}

# -------- XML endpoint (NEW) --------
@app.post("/validate/xml", response_model=ValidateResponse)
async def validate_xml(file: UploadFile = File(...)):
    content = await file.read()
    doc = xmltodict.parse(content)
    tx = map_xml_to_tx(doc)
    ctx = {"rate_table": {"DE": 0.19}, "tolerance": 0.01}
    return run_tax_validation(tx, ctx)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)