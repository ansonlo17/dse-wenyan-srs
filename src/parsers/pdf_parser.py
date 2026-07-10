"""PDF text extraction for classical Chinese source files."""

from __future__ import annotations

from .text_parser import split_paragraphs


def parse_pdf_bytes(data: bytes) -> tuple[list[str], str | None]:
    """
    Return (paragraphs, warning).
    warning is set when extraction looks empty / scanned.
    """
    text = ""
    errors: list[str] = []

    # Try pdfplumber first
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                pages.append(t)
            text = "\n\n".join(pages)
    except Exception as e:  # noqa: BLE001
        errors.append(f"pdfplumber: {e}")

    if len(text.strip()) < 20:
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            alt = "\n\n".join(pages)
            if len(alt.strip()) > len(text.strip()):
                text = alt
        except Exception as e:  # noqa: BLE001
            errors.append(f"pypdf: {e}")

    cleaned = text.strip()
    if len(cleaned) < 20:
        msg = (
            "此 PDF 幾乎抽不到文字（可能是掃描影像版）。"
            "請改上傳 TXT／Markdown，或先用其他工具 OCR 後再匯入。"
        )
        if errors:
            msg += "（" + "；".join(errors) + "）"
        return [], msg

    paragraphs = split_paragraphs(cleaned)
    if not paragraphs:
        return [], "已抽到文字，但無法切成段落。請改用純文字檔。"
    return paragraphs, None
