# whyhow_api/utilities/validation.py
"""Validation utilities (UUID-based)."""

from uuid import UUID
from fastapi import HTTPException

def safe_uuid(value: str) -> UUID:
    """
    将字符串解析为 UUID，不合法则抛 400。
    """
    try:
        return UUID(value)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from e
