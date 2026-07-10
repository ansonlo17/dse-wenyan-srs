"""Plain text / markdown parsing into paragraphs."""

from __future__ import annotations

import re


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ")
    # strip BOM
    text = text.lstrip("\ufeff")
    return text.strip()


def split_paragraphs(text: str) -> list[str]:
    """Split into paragraphs by blank lines, then by long single blocks."""
    text = clean_text(text)
    if not text:
        return []

    # Prefer blank-line separation
    chunks = re.split(r"\n\s*\n+", text)
    paragraphs: list[str] = []
    for chunk in chunks:
        lines = []
        for line in chunk.split("\n"):
            s = line.strip()
            # drop page-number-only lines
            if re.fullmatch(r"\d{1,4}", s):
                continue
            if s:
                lines.append(s)
        if not lines:
            continue
        block = "".join(lines) if _looks_chinese_block(lines) else "\n".join(lines)
        block = re.sub(r"[ \t]+", " ", block).strip()
        if block:
            # further split very long blocks by sentence end + length
            paragraphs.extend(_maybe_split_long(block))
    return paragraphs


def _looks_chinese_block(lines: list[str]) -> bool:
    sample = "".join(lines)[:80]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(3, len(sample) // 3)


def _maybe_split_long(block: str, max_len: int = 180) -> list[str]:
    if len(block) <= max_len:
        return [block]
    parts = re.split(r"(?<=[。！？；])", block)
    out: list[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) > max_len and buf:
            out.append(buf.strip())
            buf = p
        else:
            buf += p
    if buf.strip():
        out.append(buf.strip())
    return out or [block]


def parse_text_string(text: str) -> list[str]:
    return split_paragraphs(text)


def parse_text_bytes(data: bytes, filename: str = "") -> list[str]:
    for enc in ("utf-8", "utf-8-sig", "big5", "gb18030"):
        try:
            return parse_text_string(data.decode(enc))
        except UnicodeDecodeError:
            continue
    return parse_text_string(data.decode("utf-8", errors="replace"))
