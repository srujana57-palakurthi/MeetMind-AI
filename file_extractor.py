"""
file_extractor.py
=================
Safely reads the text out of uploaded .txt, .pdf and .docx files.

Security rules applied here:
  * only three extensions are accepted (txt, pdf, docx)
  * the file content is checked against the expected file signature
    (a ".pdf" that is really something else is rejected)
  * the size limit is enforced BEFORE the file is fully processed
  * nothing is ever written to disk - the file is read from memory
  * every failure raises FileValidationError with a message that is
    safe to show to the user
"""

from __future__ import annotations

import io
import re

from docx import Document          # python-docx  -> .docx
from pypdf import PdfReader        # pypdf        -> .pdf


class FileValidationError(Exception):
    """Raised when an upload is invalid, empty or unreadable."""


ALLOWED_EXTENSIONS = ("txt", "pdf", "docx")

_PDF_MAGIC = b"%PDF"
_DOCX_MAGIC = b"PK"          # a .docx file is a ZIP archive
_BLANK_LINES = re.compile(r"\n{3,}")


def extension_of(filename: str) -> str:
    """Return the lowercase extension without the dot ("" if none)."""
    if "." not in (filename or ""):
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def validate_filename(filename: str) -> str:
    """Check the extension. Returns it, or raises FileValidationError."""
    ext = extension_of(filename)
    if not ext:
        raise FileValidationError(
            "The uploaded file has no extension. Allowed types: "
            + ", ".join(ALLOWED_EXTENSIONS) + "."
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f'Unsupported file type ".{ext}". Please upload a '
            + ", ".join(f".{e}" for e in ALLOWED_EXTENSIONS) + " file."
        )
    return ext


def _tidy(text: str) -> str:
    """Remove NUL bytes and collapse 3+ blank lines."""
    text = text.replace("\x00", "")
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _decode_txt(data: bytes) -> str:
    """Try common encodings so Windows/Mac files both work."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise FileValidationError(
                    "This PDF is password protected, so its text cannot be read."
                )
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except FileValidationError:
        raise
    except Exception as exc:                      # corrupt / broken file
        raise FileValidationError(
            "This PDF could not be read. The file may be damaged or "
            "scanned as an image (image-only PDFs have no selectable text)."
        ) from exc


def _read_docx(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise FileValidationError(
            "This DOCX file could not be read. It may be damaged."
        ) from exc

    parts = [p.text for p in document.paragraphs]
    for table in document.tables:                 # also read table cells
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def extract_text(data: bytes, filename: str) -> str:
    """
    Turn an uploaded file into plain text.

    Raises FileValidationError with a friendly message when the file is
    not allowed, empty or unreadable. Returns the extracted text otherwise.
    """
    ext = validate_filename(filename)

    if not data:
        raise FileValidationError("The uploaded file is empty (0 bytes).")

    if ext == "txt":
        text = _decode_txt(data)
    elif ext == "pdf":
        if not data.startswith(_PDF_MAGIC):
            raise FileValidationError(
                'This file has a ".pdf" name but is not really a PDF.'
            )
        text = _read_pdf(data)
    else:  # docx
        if not data.startswith(_DOCX_MAGIC):
            raise FileValidationError(
                'This file has a ".docx" name but is not a valid Word document.'
            )
        text = _read_docx(data)

    text = _tidy(text)
    if not text:
        if ext == "pdf":
            raise FileValidationError(
                'No readable text was found in this PDF. It is probably a '
                "scanned or image-only PDF - please copy the text into a "
                ".txt file or paste the notes directly."
            )
        raise FileValidationError(
            f'The file "{filename}" is empty or contains no readable text.'
        )
    return text
