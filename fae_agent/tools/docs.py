"""문서 도구: 승인 뒤 만들어진 문서를 조회하고 무결성을 확인하며, 임의 문서를 승인된 내용과 대조한다 (MCP 서버 이름: docs).

문서를 **만드는** 도구는 일부러 없다. 문서는 사람이 승인한 뒤 그래프의 documents 노드만 만든다 (승인 게이트를 우회하는 경로가 없게).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..config import get_settings

_TICKET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # 경로 이동(../)을 막는다
READABLE = ("customer_email.md", "customer_email.eml", "internal_report.md", "ticket_summary.json", "manifest.json")


def _folder(ticket_id: str):
    if not _TICKET.match(ticket_id or ""):
        return None
    folder = get_settings().outbox_dir / ticket_id
    return folder if folder.is_dir() else None


def _verify(folder) -> dict[str, Any]:
    """manifest의 해시와 실제 파일을 비교한다. 승인 뒤 파일이 바뀌었으면 verified=False."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"verified": False, "changed": [], "manifest": {}}
    changed = []
    for name, digest in (manifest.get("files") or {}).items():
        path = folder / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    return {"verified": not changed, "changed": changed, "manifest": manifest}


def list_documents(ticket_id: str = "") -> dict[str, Any]:
    """승인 뒤 만들어진 문서를 보여준다. ticket_id가 없으면 문서가 있는 티켓 목록, 있으면 그 티켓의 파일과 해시 검증 결과."""
    outbox = get_settings().outbox_dir
    if ticket_id:
        folder = _folder(ticket_id)
        if folder is None:
            return {"found": False, "ticket_id": ticket_id}
        check = _verify(folder)
        return {
            "found": True,
            "ticket_id": ticket_id,
            "files": sorted(p.name for p in folder.iterdir() if p.is_file()),
            "verified": check["verified"],
            "changed": check["changed"],
            "approval": check["manifest"].get("approval", {}),
            "generated_at": check["manifest"].get("generated_at", ""),
            "status": check["manifest"].get("status", ""),
        }
    tickets = []
    for folder in sorted(outbox.iterdir()) if outbox.is_dir() else []:
        if folder.is_dir() and (folder / "manifest.json").is_file():
            manifest = _verify(folder)["manifest"]
            tickets.append(
                {"ticket_id": folder.name, "generated_at": manifest.get("generated_at", ""), "approval": (manifest.get("approval") or {}).get("action", "")}
            )
    return {"tickets": tickets}


def read_document(ticket_id: str, name: str) -> dict[str, Any]:
    """티켓의 문서 하나를 읽는다 (customer_email.md, customer_email.eml, internal_report.md, ticket_summary.json, manifest.json). 읽기 전용."""
    folder = _folder(ticket_id)
    if folder is None:
        return {"found": False, "ticket_id": ticket_id}
    if name not in READABLE:
        return {"found": False, "ticket_id": ticket_id, "message": f"읽을 수 있는 문서: {', '.join(READABLE)}"}
    path = folder / name
    if not path.is_file():
        return {"found": False, "ticket_id": ticket_id, "name": name}
    return {"found": True, "ticket_id": ticket_id, "name": name, "content": path.read_text(encoding="utf-8"), "verified": _verify(folder)["verified"]}


def check_document_text(text: str, source: str, allowed: str = "") -> dict[str, Any]:
    """문서(text)에 승인된 내용(source)에 없는 숫자, 부품 번호, 약속·가격·납기 표현, 내부 파일·카드 표현, 채우지 않은 자리표시가 있는지 코드로 검사한다. 문장의 어색함은 보지 못한다."""
    from ..docs_check import check_document

    issues = check_document(text, source, allowed)
    return {"ok": not issues, "issues": issues}


DOCS_TOOLS = {fn.__name__: fn for fn in (list_documents, read_document, check_document_text)}
