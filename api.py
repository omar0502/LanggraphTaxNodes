# api.py
# pip install fastapi uvicorn

from fastapi import FastAPI, Body
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from tax_graph import run_tax_validation, explain_result

app = FastAPI(title="Tax Validation Agent API", version="0.2.0")

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
