import hashlib
import io
import os
import re
from typing import Tuple
import pypdf
import filetype

DEFAULT_MAX_FILE_SIZE = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50")) * 1024 * 1024  # 50 MB default


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal or invalid characters."""
    # Strip paths
    clean = os.path.basename(filename)
    # Remove null bytes and dangerous chars
    clean = re.sub(r'[\x00-\x1f\\/:"*?<>|]', "_", clean)
    clean = clean.strip()
    return clean or "document.pdf"


def compute_sha256(content: bytes) -> str:
    """Calculate SHA-256 hash of file bytes for deduplication."""
    return hashlib.sha256(content).hexdigest()


def validate_pdf_file(
    filename: str,
    content: bytes,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
) -> Tuple[bool, str, int]:
    """
    Validate PDF file integrity, extension, size, magic bytes, and readability.

    Returns:
        (is_valid, error_message, page_count)
    """
    # 1. Check non-empty
    if not content or len(content) == 0:
        return False, "Uploaded file is empty (0 bytes).", 0

    # 2. Check file size
    if len(content) > max_size:
        max_mb = max_size / (1024 * 1024)
        actual_mb = len(content) / (1024 * 1024)
        return (
            False,
            f"File size ({actual_mb:.2f} MB) exceeds maximum allowed limit ({max_mb:.0f} MB).",
            0,
        )

    # 3. Check filename extension
    sanitized = sanitize_filename(filename)
    if not sanitized.lower().endswith(".pdf"):
        return False, f"Invalid file extension. Expected '.pdf', got '{os.path.splitext(sanitized)[1]}'.", 0

    # 4. Check magic bytes / file signature
    # Valid PDFs begin with '%PDF-' within the first 1024 bytes
    header = content[:1024]
    if b"%PDF-" not in header:
        return False, "Invalid PDF header: File does not contain standard '%PDF-' signature.", 0

    # 5. Check MIME type via filetype detection
    kind = filetype.guess(content[:2048])
    if kind is not None and kind.mime != "application/pdf":
        return False, f"File content does not match PDF MIME type (detected: {kind.mime}).", 0

    # 6. Check PDF structure and readability via pypdf
    try:
        stream = io.BytesIO(content)
        reader = pypdf.PdfReader(stream)

        # Check if encrypted
        if reader.is_encrypted:
            try:
                # Try decrypting with empty password
                decrypt_success = reader.decrypt("")
                if decrypt_success == 0:
                    return False, "Password-protected or encrypted PDFs are not supported.", 0
            except Exception:
                return False, "Password-protected or encrypted PDFs are not supported.", 0

        page_count = len(reader.pages)
        if page_count == 0:
            return False, "PDF contains 0 pages or no readable content.", 0

        # Attempt to inspect the first page to ensure streams are not corrupted
        try:
            _ = reader.pages[0].extract_text()
        except Exception as e:
            # If text extraction on first page fails completely due to corruption
            return False, f"PDF appears corrupted or unreadable: {str(e)}", 0

        return True, "", page_count

    except pypdf.errors.PdfReadError as e:
        return False, f"Unreadable PDF document: {str(e)}", 0
    except Exception as e:
        return False, f"Failed to validate PDF structure: {str(e)}", 0
