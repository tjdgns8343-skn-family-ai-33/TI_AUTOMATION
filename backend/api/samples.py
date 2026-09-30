"""화면의 "예시 문의" 목록. 정답(ground_truth)은 내보내지 않는다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fae_agent.cli import load_complaint
from fae_agent.config import get_settings


def list_samples() -> list[dict[str, Any]]:
    data = get_settings().data_dir
    items: list[dict[str, Any]] = []
    for path in sorted((data / "samples").glob("*.txt")):
        complaint = load_complaint(Path(path))
        items.append(
            {
                "id": f"sample:{path.stem}",
                "group": "예시",
                "customer": complaint.customer,
                "subject": complaint.subject,
                "body": complaint.body,
            }
        )
    groups = {"eval_hard_cases": "구체적 문의", "eval_real_parts": "실제 부품 문의", "eval_new_parts": "새 부품 문의"}
    for stem, group in groups.items():
        path = data / "eval" / f"{stem}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                items.append(
                    {
                        "id": f"{stem}:{row['id']}",
                        "group": group,
                        "customer": row.get("customer", ""),
                        "subject": row.get("subject", ""),
                        "body": row["body"],
                    }
                )
    return items
