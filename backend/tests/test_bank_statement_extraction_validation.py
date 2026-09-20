import io
import os
import pytest
import pandas as pd
import pypdf
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.database import get_db
from backend.routers.bank_statement import BankTransactionRowV1

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM bank_mappings")
        conn.execute("DELETE FROM ledgers")
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('Bank Suspense Account', 'Suspense Accounts', 0)")
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('HDFC Bank', 'Bank Accounts', 0)")
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM bank_mappings")
        conn.execute("DELETE FROM ledgers")
        conn.commit()


def _minimal_pdf_bytes() -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _mock_response(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


def _mock_uploaded_file():
    f = MagicMock()
    f.name = "files/fake123"
    return f


# ---------------------------------------------------------------------------
# 1. Pydantic model unit tests
# ---------------------------------------------------------------------------

def test_row_model_coerces_valid_dict():
    row = BankTransactionRowV1.model_validate({
        "date": "2026-01-01", "narration": "AMAZON", "amount": "500.50", "type": "DEBIT"
    })
    assert row.amount == 500.50
    assert row.type == "DEBIT"


@pytest.mark.parametrize("missing_field", ["date", "narration", "amount", "type"])
def test_row_model_rejects_missing_field(missing_field):
    row = {"date": "2026-01-01", "narration": "AMAZON", "amount": 500, "type": "DEBIT"}
    del row[missing_field]
    with pytest.raises(ValidationError):
        BankTransactionRowV1.model_validate(row)


def test_row_model_rejects_non_numeric_amount():
    with pytest.raises(ValidationError):
        BankTransactionRowV1.model_validate({
            "date": "2026-01-01", "narration": "AMAZON", "amount": "N/A", "type": "DEBIT"
        })


# ---------------------------------------------------------------------------
# 2-5. PDF upload endpoint tests (Gemini call mocked)
# ---------------------------------------------------------------------------

def test_pdf_upload_skips_malformed_row_and_reports_count():
    pdf_bytes = _minimal_pdf_bytes()

    valid_and_bad_json = (
        '{"transactions": ['
        '{"date": "2026-01-01", "narration": "AMAZON SELLER", "amount": 100, "type": "DEBIT"}, '
        '{"date": "2026-01-02", "narration": "MISSING AMOUNT ROW", "type": "DEBIT"}'
        ']}'
    )

    with patch("backend.routers.bank_statement.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.files.upload.return_value = _mock_uploaded_file()
        mock_client.models.generate_content.return_value = _mock_response(valid_and_bad_json)

        os.environ["GEMINI_API_KEY"] = "fake_key"
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("statement.pdf", pdf_bytes, "application/pdf")}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transactions"]) == 1
    assert data["skipped_count"] == 1
    assert mock_client.models.generate_content.call_count == 1


def test_pdf_upload_retries_once_on_unparseable_response_then_succeeds():
    pdf_bytes = _minimal_pdf_bytes()
    good_json = '{"transactions": [{"date": "2026-01-01", "narration": "AMAZON SELLER", "amount": 100, "type": "DEBIT"}]}'

    with patch("backend.routers.bank_statement.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.files.upload.return_value = _mock_uploaded_file()
        mock_client.models.generate_content.side_effect = [
            _mock_response("this is not json at all {{{"),
            _mock_response(good_json),
        ]

        os.environ["GEMINI_API_KEY"] = "fake_key"
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("statement.pdf", pdf_bytes, "application/pdf")}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transactions"]) == 1
    assert data["skipped_count"] == 0
    assert mock_client.models.generate_content.call_count == 2


def test_pdf_upload_returns_400_when_both_attempts_unparseable():
    pdf_bytes = _minimal_pdf_bytes()

    with patch("backend.routers.bank_statement.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.files.upload.return_value = _mock_uploaded_file()
        mock_client.models.generate_content.side_effect = [
            _mock_response("garbage {{{"),
            _mock_response("still garbage {{{"),
        ]

        os.environ["GEMINI_API_KEY"] = "fake_key"
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("statement.pdf", pdf_bytes, "application/pdf")}
        )

    assert resp.status_code == 400
    assert mock_client.models.generate_content.call_count == 2


def test_pdf_upload_returns_400_when_every_row_invalid():
    pdf_bytes = _minimal_pdf_bytes()
    all_bad_json = '{"transactions": [{"narration": "NO DATE OR AMOUNT"}, {"date": "2026-01-02"}]}'

    with patch("backend.routers.bank_statement.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.files.upload.return_value = _mock_uploaded_file()
        mock_client.models.generate_content.return_value = _mock_response(all_bad_json)

        os.environ["GEMINI_API_KEY"] = "fake_key"
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("statement.pdf", pdf_bytes, "application/pdf")}
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 6. Excel path regression -- validation is a no-op for well-formed rows
# ---------------------------------------------------------------------------

def test_excel_upload_still_works_with_new_validation_step():
    df = pd.DataFrame([
        {"Date": "2026-01-01", "Narration": "AMAZON SELLER", "Withdrawal": 100, "Deposit": 0},
    ])

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        df.to_excel(f.name, index=False)
        f.seek(0)
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped_count"] == 0
    assert len(data["transactions"]) == 1
