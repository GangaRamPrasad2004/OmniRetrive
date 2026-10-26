"""
Tests for Feature 7: OCR and difficult PDF extraction pipeline.

All tests use mocks — no real PDF files or Tesseract binary required.
"""

import io
import types
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to build fake LlamaIndex Document objects
# ---------------------------------------------------------------------------

def _make_doc(text: str, page_label: str = None):
    """Create a minimal fake LlamaIndex Document with .text and .metadata."""
    doc = MagicMock()
    doc.text = text
    doc.metadata = {"page_label": page_label} if page_label else {}
    return doc


def _make_docs(texts):
    """Create a list of fake documents from a list of strings."""
    return [_make_doc(t) for t in texts]


# ---------------------------------------------------------------------------
# 1. Text PDF — no OCR needed
# ---------------------------------------------------------------------------

def test_text_pdf_extraction_no_ocr(tmp_path):
    """Pages with plenty of text should use extraction_method='text' and ocr_fallback=False."""
    fake_pdf = tmp_path / "text.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content")

    page_texts = [
        "This is page one with plenty of text to satisfy the threshold easily.",
        "This is page two with enough readable content to not trigger OCR.",
    ]

    with patch("data_loader.PDFReader") as MockReader, \
         patch("data_loader.OCR_AVAILABLE", False):

        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs(page_texts)

        import data_loader
        chunks = data_loader.load_and_chunk_pdf_with_metadata(str(fake_pdf))

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk["extraction_method"] == "text", f"Expected 'text', got {chunk['extraction_method']}"
        assert chunk["ocr_fallback"] is False
        assert chunk["ocr_attempted"] is False


# ---------------------------------------------------------------------------
# 2. Scanned page — OCR unavailable → placeholder chunk
# ---------------------------------------------------------------------------

def test_scanned_page_detected_and_ocr_skipped_if_unavailable(tmp_path):
    """Scanned pages with OCR_AVAILABLE=False should produce placeholder chunks with extraction_method='none'."""
    fake_pdf = tmp_path / "scanned.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    # All pages are blank (scanned)
    page_texts = ["", "", ""]

    with patch("data_loader.PDFReader") as MockReader, \
         patch("data_loader.OCR_AVAILABLE", False):

        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs(page_texts)

        import data_loader
        chunks = data_loader.load_and_chunk_pdf_with_metadata(str(fake_pdf))

    # Every chunk must be a placeholder with extraction_method='none'
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk["extraction_method"] == "none", f"Expected 'none', got {chunk['extraction_method']}"
        assert chunk["ocr_attempted"] is False
        assert chunk["ocr_fallback"] is False


# ---------------------------------------------------------------------------
# 3. Scanned page — OCR available → OCR chunk produced
# ---------------------------------------------------------------------------

def test_scanned_page_ocr_fallback_when_available(tmp_path):
    """When OCR is available, scanned pages should yield ocr_fallback=True chunks."""
    fake_pdf = tmp_path / "scanned_ocr.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    fake_image = MagicMock()  # pretend PIL image

    with patch("data_loader.PDFReader") as MockReader, \
         patch("data_loader.OCR_AVAILABLE", True), \
         patch("pdf2image.convert_from_path", return_value=[fake_image]) as mock_convert, \
         patch("pytesseract.image_to_string", return_value="Extracted OCR text from page") as mock_ocr:

        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs([""])  # 1 scanned page

        import data_loader
        chunks = data_loader.load_and_chunk_pdf_with_metadata(str(fake_pdf))

    assert len(chunks) > 0
    ocr_chunks = [c for c in chunks if c["extraction_method"] == "ocr"]
    assert len(ocr_chunks) > 0, "Expected at least one OCR chunk"
    for chunk in ocr_chunks:
        assert chunk["ocr_fallback"] is True
        assert chunk["ocr_attempted"] is True
        assert "Extracted OCR text from page" in chunk["text"]


# ---------------------------------------------------------------------------
# 4. Corrupted PDF raises ExtractionError
# ---------------------------------------------------------------------------

def test_corrupted_pdf_raises_extraction_error(tmp_path):
    """When PDFReader.load_data raises, ExtractionError should be propagated."""
    fake_pdf = tmp_path / "corrupt.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 corrupt")

    with patch("data_loader.PDFReader") as MockReader:
        instance = MockReader.return_value
        instance.load_data.side_effect = RuntimeError("corrupt")

        import data_loader
        with pytest.raises(data_loader.ExtractionError):
            data_loader.load_and_chunk_pdf_with_metadata(str(fake_pdf))


# ---------------------------------------------------------------------------
# 5. detect_extraction_quality — text PDF
# ---------------------------------------------------------------------------

def test_detect_extraction_quality_text_pdf(tmp_path):
    """Text-rich PDF should report pages_with_text == pages_total and ocr_recommended=False."""
    fake_pdf = tmp_path / "text_quality.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    page_texts = [
        "A" * 200,  # well above 50-char threshold
        "B" * 150,
        "C" * 100,
    ]

    with patch("data_loader.PDFReader") as MockReader:
        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs(page_texts)

        import data_loader
        result = data_loader.detect_extraction_quality(str(fake_pdf))

    assert result["pages_total"] == 3
    assert result["pages_with_text"] == 3
    assert result["pages_scanned"] == 0
    assert result["ocr_recommended"] is False
    assert "ocr_available" in result
    assert "file_size_bytes" in result


# ---------------------------------------------------------------------------
# 6. detect_extraction_quality — scanned PDF
# ---------------------------------------------------------------------------

def test_detect_extraction_quality_scanned_pdf(tmp_path):
    """All-scanned PDF should report pages_scanned == 3 and ocr_recommended=True."""
    fake_pdf = tmp_path / "scanned_quality.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    page_texts = ["", "", ""]  # all below threshold

    with patch("data_loader.PDFReader") as MockReader:
        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs(page_texts)

        import data_loader
        result = data_loader.detect_extraction_quality(str(fake_pdf))

    assert result["pages_total"] == 3
    assert result["pages_scanned"] == 3
    assert result["pages_with_text"] == 0
    assert result["ocr_recommended"] is True


# ---------------------------------------------------------------------------
# 7. validate_pdf_file accepts scanned PDF (extract_text returns empty string)
# ---------------------------------------------------------------------------

def test_validate_pdf_accepts_scanned_pdf():
    """A PDF where extract_text() returns '' (not raises) must pass validation."""
    import file_utils

    # Build minimal valid-looking PDF bytes
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"xref\n0 2\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n9\n%%EOF"
    )

    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""  # scanned page — returns empty string, no exception

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page]

    with patch("file_utils.pypdf.PdfReader", return_value=mock_reader), \
         patch("file_utils.filetype.guess", return_value=None):

        is_valid, msg, page_count = file_utils.validate_pdf_file("test.pdf", pdf_bytes)

    assert is_valid is True, f"Expected valid scanned PDF, got error: {msg}"
    assert page_count == 1


# ---------------------------------------------------------------------------
# 8. Mixed PDF — some pages text, some scanned → chunks for all pages
# ---------------------------------------------------------------------------

def test_partial_extraction_mixed_pdf(tmp_path):
    """Mixed PDFs should yield text chunks for text pages and placeholders for scanned ones."""
    fake_pdf = tmp_path / "mixed.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    page_texts = [
        "This page has enough text content to exceed the fifty character threshold easily.",
        "",          # scanned
        "Another page with readable text content, definitely more than fifty characters here.",
        "",          # scanned
    ]

    with patch("data_loader.PDFReader") as MockReader, \
         patch("data_loader.OCR_AVAILABLE", False):

        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs(page_texts)

        import data_loader
        chunks = data_loader.load_and_chunk_pdf_with_metadata(str(fake_pdf))

    text_chunks = [c for c in chunks if c["extraction_method"] == "text"]
    empty_chunks = [c for c in chunks if c["extraction_method"] == "none"]

    assert len(text_chunks) > 0, "Expected text chunks from text pages"
    assert len(empty_chunks) > 0, "Expected placeholder chunks from scanned pages"

    # Ensure overall pages_text_extracted and pages_empty would be non-zero
    # (verified indirectly via chunk counts)
    total = len(text_chunks) + len(empty_chunks)
    assert total > 0


# ---------------------------------------------------------------------------
# 9. max_pages parameter limits pages processed
# ---------------------------------------------------------------------------

def test_large_pdf_max_pages_limit(tmp_path):
    """max_pages=3 on a 10-page PDF should only produce chunks from the first 3 pages."""
    fake_pdf = tmp_path / "large.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    # 10 pages, all with text
    page_texts = [f"Page {i} has enough text content to pass the fifty character threshold here." for i in range(10)]

    with patch("data_loader.PDFReader") as MockReader, \
         patch("data_loader.OCR_AVAILABLE", False):

        instance = MockReader.return_value
        instance.load_data.return_value = _make_docs(page_texts)

        import data_loader
        chunks = data_loader.load_and_chunk_pdf_with_metadata(str(fake_pdf), max_pages=3)

    page_numbers = {c["page_number"] for c in chunks}
    # Only pages 1, 2, 3 should appear
    assert page_numbers.issubset({1, 2, 3}), f"Got unexpected pages: {page_numbers}"
    assert len(chunks) > 0
