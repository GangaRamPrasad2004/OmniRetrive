import pytest
from unittest import mock
from data_loader import load_and_chunk_pdf_with_metadata, ExtractionError

# Helper to create a mock page object
def mock_page(text):
    page = mock.Mock()
    page.text = text
    page.metadata = {}
    return page

@mock.patch('data_loader.PDFReader')
def test_text_pdf_extraction_no_ocr(mock_pdf_reader, tmp_path):
    pdf_path = tmp_path / "text.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test fixture")
    # Simulate a PDF with two pages with sufficient text
    mock_reader_instance = mock.Mock()
    mock_reader_instance.pages = [mock_page('This is some text on page 1.' * 10),
                                 mock_page('Page 2 also has plenty of text.' * 10)]
    mock_reader_instance.load_data.return_value = mock_reader_instance.pages
    mock_pdf_reader.return_value = mock_reader_instance

    chunks = load_and_chunk_pdf_with_metadata(str(pdf_path))
    # All chunks should have extraction_method "text"
    for chunk in chunks:
        assert chunk.get('extraction_method') == 'text'
        assert not chunk.get('ocr_fallback')
        assert not chunk.get('ocr_attempted')

@mock.patch('data_loader.PDFReader')
@mock.patch('data_loader.OCR_AVAILABLE', False)
def test_scanned_page_no_ocr(mock_pdf_reader, tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test fixture")
    # Simulate scanned PDF pages with empty text
    mock_reader_instance = mock.Mock()
    mock_reader_instance.pages = [mock_page(''), mock_page('')]
    mock_reader_instance.load_data.return_value = mock_reader_instance.pages
    mock_pdf_reader.return_value = mock_reader_instance

    chunks = load_and_chunk_pdf_with_metadata(str(pdf_path))
    for chunk in chunks:
        assert chunk.get('extraction_method') == 'none'
        assert not chunk.get('ocr_fallback')
        assert not chunk.get('ocr_attempted')

@mock.patch('data_loader.PDFReader')
@mock.patch('data_loader.OCR_AVAILABLE', True)
@mock.patch('pdf2image.convert_from_path')
@mock.patch('pytesseract.image_to_string')
def test_scanned_page_ocr_fallback(mock_ocr, mock_convert, mock_pdf_reader, tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test fixture")
    # PDFReader returns empty text, OCR is available
    mock_reader_instance = mock.Mock()
    mock_reader_instance.pages = [mock_page('')]
    mock_reader_instance.load_data.return_value = mock_reader_instance.pages
    mock_pdf_reader.return_value = mock_reader_instance
    mock_convert.return_value = [mock.Mock()]  # dummy PIL image
    mock_ocr.return_value = 'OCR extracted text'

    chunks = load_and_chunk_pdf_with_metadata(str(pdf_path))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.get('extraction_method') == 'ocr'
    assert chunk.get('ocr_fallback') is True
    assert chunk.get('ocr_attempted') is True
    assert 'OCR extracted text' in chunk['text']

@mock.patch('data_loader.PDFReader')
def test_corrupted_pdf_raises_error(mock_pdf_reader, tmp_path):
    pdf_path = tmp_path / "bad.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test fixture")
    mock_pdf_reader.side_effect = RuntimeError('corrupt')
    with pytest.raises(ExtractionError):
        load_and_chunk_pdf_with_metadata(str(pdf_path))
