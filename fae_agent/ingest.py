"""문서(PDF·MD·TXT)를 검색용 조각(chunk)으로 나눠 data/processed/chunks.jsonl에 저장합니다.

파일 이름 규칙: <부품번호>_<문서종류>[_설명].<확장자>
  예) TPS54331_datasheet.pdf, TPS54331_errata.pdf, GENERAL_appnote_i2c-pullup.pdf
  부품과 무관한 일반 문서는 부품번호 자리에 GENERAL을 씁니다.
읽는 폴더: data/raw/docs (직접 받은 문서, 깃에 올리지 않음) + data/samples/docs (샘플)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import get_settings

SUPPORTED = {".pdf", ".md", ".txt"}
CHUNK_CHARS = 1200
OVERLAP = 150


def parse_filename(path: Path) -> tuple[str, str]:
    """파일 이름에서 (부품번호, 문서종류)를 읽습니다. GENERAL은 부품번호 ''로 둡니다."""
    parts = path.stem.split("_")
    part = parts[0].upper()
    if part == "GENERAL":
        part = ""
    doc_type = parts[1].lower() if len(parts) > 1 else "doc"
    return part, doc_type


def _pdf_sections(path: Path) -> list[tuple[str, str]]:
    import pdfplumber

    sections = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                sections.append((f"p.{number}", text))
    return sections


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    """'## 제목' 단위로 나눕니다. 근거 인용 때 §제목으로 표시됩니다."""
    sections: list[tuple[str, str]] = []
    title, buffer = "intro", []
    for line in text.splitlines():
        if line.startswith("## "):
            if "".join(buffer).strip():
                sections.append((f"§{title}", "\n".join(buffer)))
            title, buffer = line[3:].strip(), []
        else:
            buffer.append(line)
    if "".join(buffer).strip():
        sections.append((f"§{title}", "\n".join(buffer)))
    return sections


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def iter_files(dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for folder in dirs:
        if folder.exists():
            files += sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
    return files


def build_chunks() -> dict[str, Any]:
    """문서를 읽어 chunks.jsonl을 새로 만듭니다."""
    settings = get_settings()
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    files = iter_files(settings.docs_dirs)
    for path in files:
        part, doc_type = parse_filename(path)
        try:
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                sections = _pdf_sections(path)
            elif suffix == ".md":
                sections = _markdown_sections(path.read_text(encoding="utf-8"))
            else:
                sections = [("p.1", path.read_text(encoding="utf-8"))]
        except Exception as exc:  # 한 파일이 깨져도 나머지는 계속 처리
            errors.append(f"{path.name}: {exc}")
            continue
        for locator, text in sections:
            for i, piece in enumerate(split_text(text)):
                records.append(
                    {
                        "id": f"{path.name}#{locator}#{i}",
                        "source": f"{path.name} {locator}",
                        "part_number": part,
                        "doc_type": doc_type,
                        "text": piece,
                    }
                )
    settings.chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.chunks_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"files": len(files), "chunks": len(records), "errors": errors, "path": str(settings.chunks_path)}
