"""지식 베이스 검색 도구: 문서 조각, 사례 카드, 부품 스펙 카드.

지금은 TF-IDF(로컬, 키 불필요)로 검색합니다. 나중에 임베딩 + 벡터 DB(Pinecone 등)로
바꿀 때는 TfidfIndex만 교체하면 됩니다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ..config import get_settings

# 한국어 문의 → 영어 문서 검색어 보강 (TF-IDF는 언어를 넘나들지 못함)
KO_EN_TERMS = {
    "발진": "oscillation ringing instability phase margin compensation",
    "링잉": "ringing oscillation",
    "불안정": "instability oscillation",
    "리플": "ripple output capacitor esr",
    "노이즈": "noise ripple probe ground",
    "잡음": "noise ripple",
    "발열": "thermal junction temperature power loss theta",
    "뜨거": "thermal junction temperature power loss",
    "과열": "thermal junction temperature",
    "출력 전압": "output voltage vref divider",
    "전압이 낮": "output voltage low current limit",
    "기동": "startup soft-start uvlo enable inrush",
    "켜지지": "startup enable uvlo",
    "안 켜": "startup enable uvlo",
    "통신": "i2c communication",
    "풀업": "pull-up resistor rise time",
    "nack": "i2c pull-up address rise time",
    "정격": "absolute maximum ratings overstress",
    "타버": "overstress absolute maximum damage",
    "파손": "damage overstress",
}

_cache: dict[str, tuple[str, Any]] = {}


def clear_cache() -> None:
    _cache.clear()


def expand_query(query: str) -> str:
    lowered = query.lower()
    extra = [en for ko, en in KO_EN_TERMS.items() if ko in lowered]
    return " ".join([query, *extra])


class TfidfIndex:
    """레코드 목록을 TF-IDF로 색인해 코사인 유사도로 검색합니다."""

    def __init__(self, records: list[dict[str, Any]], text_fn: Callable[[dict[str, Any]], str]):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.records = records
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"(?u)\b\w[\w\-]*\b")
        self.matrix = self.vectorizer.fit_transform([text_fn(r) for r in records]) if records else None

    def search(
        self, query: str, k: int = 5, keep: Callable[[dict[str, Any]], bool] | None = None
    ) -> list[tuple[dict[str, Any], float]]:
        if self.matrix is None:
            return []
        scores = (self.matrix @ self.vectorizer.transform([query]).T).toarray().ravel()
        results: list[tuple[dict[str, Any], float]] = []
        for idx in scores.argsort()[::-1]:
            if scores[idx] <= 0:
                break
            record = self.records[idx]
            if keep and not keep(record):
                continue
            results.append((record, float(scores[idx])))
            if len(results) >= k:
                break
        return results


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _signature(paths: list[Path]) -> str:
    return "|".join(f"{p}:{p.stat().st_mtime_ns}" for p in paths if p.exists())


def _doc_index() -> TfidfIndex:
    settings = get_settings()
    if not settings.chunks_path.exists():  # 처음 실행이면 문서를 자동으로 색인
        from ..ingest import build_chunks

        build_chunks()
    key, signature = "docs", _signature([settings.chunks_path])
    cached = _cache.get(key)
    if cached and cached[0] == signature:
        return cached[1]
    index = TfidfIndex(_read_jsonl(settings.chunks_path), lambda r: f"{r['source']} {r['text']}")
    _cache[key] = (signature, index)
    return index


def _case_index() -> TfidfIndex:
    settings = get_settings()
    files = sorted(settings.cases_dir.glob("*.jsonl")) if settings.cases_dir.exists() else []
    key, signature = "cases", _signature(files)
    cached = _cache.get(key)
    if cached and cached[0] == signature:
        return cached[1]
    records = [record for path in files for record in _read_jsonl(path)]
    fields = ("title", "part_number", "symptom", "cause", "fix")
    index = TfidfIndex(records, lambda r: " ".join(str(r.get(f, "")) for f in fields))
    _cache[key] = (signature, index)
    return index


def search_docs(query: str, part_number: str = "", k: int = 5) -> dict[str, Any]:
    """데이터시트·에라타·앱노트 조각을 검색합니다.

    part_number를 주면 그 부품의 문서와 일반 문서(GENERAL)만 봅니다.
    """
    part = (part_number or "").upper()

    def keep(record: dict[str, Any]) -> bool:
        return record.get("part_number", "") in ("", part)

    hits = _doc_index().search(expand_query(query), k=k, keep=keep if part else None)
    return {
        "results": [
            {
                "source": r["source"],
                "part_number": r.get("part_number", ""),
                "doc_type": r.get("doc_type", ""),
                "snippet": r["text"][:500],
                "score": round(score, 4),
            }
            for r, score in hits
        ]
    }


def search_cases(query: str, k: int = 3) -> dict[str, Any]:
    """해결된 과거 사례 카드(data/cases/*.jsonl)를 검색합니다."""
    hits = _case_index().search(expand_query(query), k=k)
    return {
        "results": [
            {
                "source": f"case:{r.get('id', '?')}",
                "title": r.get("title", ""),
                "cause": r.get("cause", ""),
                "fix": r.get("fix", ""),
                "link": r.get("link", ""),
                "score": round(score, 4),
            }
            for r, score in hits
        ]
    }


def _load_cards() -> list[dict[str, Any]]:
    settings = get_settings()
    cards = []
    for path in sorted(settings.parts_dir.glob("*.y*ml")):
        card = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        card.setdefault("part_number", path.stem)
        card["_source"] = f"parts/{path.name}"
        cards.append(card)
    return cards


def get_part_card(part_number: str) -> dict[str, Any]:
    """부품 스펙 카드(data/parts/<부품>.yaml)를 찾습니다.

    주문 번호(예: TPS54331DR)처럼 뒤에 접미사가 붙어도 가장 길게 일치하는 카드를 돌려줍니다.
    """
    wanted = (part_number or "").upper()
    if not wanted:
        return {"found": False, "card": None}
    best: dict[str, Any] | None = None
    for card in _load_cards():
        name = str(card["part_number"]).upper()
        if wanted == name:
            return {"found": True, "card": card}
        if wanted.startswith(name) and (best is None or len(name) > len(str(best["part_number"]))):
            best = card
    return {"found": best is not None, "card": best}


def list_parts() -> dict[str, Any]:
    """등록된 부품 스펙 카드 목록."""
    return {"parts": [str(c["part_number"]) for c in _load_cards()]}


KNOWLEDGE_TOOLS = {fn.__name__: fn for fn in (search_docs, search_cases, get_part_card, list_parts)}
