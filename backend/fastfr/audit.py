from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog


def record_audit(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        detail_json=json.dumps(detail or {}, separators=(",", ":"), default=str),
    )
    db.add(row)
    return row

