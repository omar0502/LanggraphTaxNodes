import json
from pathlib import Path

from fastapi.testclient import TestClient

from api import app

client = TestClient(app)
SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def load(name):
    return json.loads((SAMPLES / name).read_text())


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_validate_pass():
    r = client.post("/validate", json=load("sample_invoice.json"))
    assert r.status_code == 200
    assert r.json()["passed"] is True


def test_validate_mismatch_returns_200_with_review():
    r = client.post("/validate", json=load("sample_invoice_mismatch.json"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert body["failed_at"] == "7_invoice_comparison"


def test_batch_counts():
    items = [load("sample_invoice.json"), load("sample_invoice_mismatch.json")]
    body = client.post("/batch", json={"items": items}).json()
    assert body["passed_count"] == 1
    assert body["needs_review_count"] == 1


def test_explain():
    body = client.post("/explain", json=load("sample_invoice_mismatch.json")).json()
    assert "needs review" in body["summary"]


def test_xml_upload():
    with open(SAMPLES / "sample_invoice.xml", "rb") as f:
        r = client.post("/validate/xml", files={"file": ("sample_invoice.xml", f, "application/xml")})
    assert r.status_code == 200
    assert r.json()["passed"] is True


def test_bad_xml_is_400_not_500():
    r = client.post("/validate/xml", files={"file": ("bad.xml", b"<Invoice><oops>", "application/xml")})
    assert r.status_code == 400
