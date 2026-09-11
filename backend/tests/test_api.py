import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.core.database import init_db

init_db()
client = TestClient(app)


def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unsupported_upload_is_controlled_error():
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("bad.txt", b"hello", "text/plain")},
        data={"document_type": "invoice"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_image_api_flow_with_services_mocked(monkeypatch, tmp_path):
    # This verifies routing/orchestration/database shape without consuming Gemini quota.
    from app.api.routes import documents as routes
    from app.services import document_service as svc
    from app.services.ocr_service import OCRResult, PageText

    monkeypatch.setattr(svc, "run_ocr", lambda raw, content_type: OCRResult([PageText(1, "Invoice INV-1 Total 118", "ocr")], True))
    monkeypatch.setattr(svc, "extract_document", lambda ocr, document_type: {
        "invoice_number": {"value": "INV-1", "evidence": {"source_text": "Invoice INV-1", "page_number": 1}, "page_number": 1},
        "subtotal": {"value": 100, "evidence": {"source_text": "Subtotal 100", "page_number": 1}, "page_number": 1},
        "tax_amount": {"value": 18, "evidence": {"source_text": "Tax 18", "page_number": 1}, "page_number": 1},
        "total_amount": {"value": 118, "evidence": {"source_text": "Total 118", "page_number": 1}, "page_number": 1},
        "line_items": [], "tables": []
    })

    img = Image.new("RGB", (10, 10), "white")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("invoice.png", buf.getvalue(), "image/png")},
        data={"document_type": "invoice"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["document_name"] == "invoice.png"
    assert body["extracted_data"]["invoice_number"]["value"] == "INV-1"
