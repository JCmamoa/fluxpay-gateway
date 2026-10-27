"""API dependencies: bearer authentication and shared state accessors."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import Settings
from ..errors import ApiError
from ..security import hash_api_key

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="MerchantApiKey")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Any:
    return request.app.state.db


def require_merchant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise ApiError(
            401,
            "invalid_api_key",
            "Missing or malformed Authorization header. Use 'Bearer <api_key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key_hash = hash_api_key(credentials.credentials)
    with request.app.state.db.connection() as conn:
        row = conn.execute(
            "SELECT id, name, created_at FROM merchants WHERE api_key_hash = ?",
            (key_hash,),
        ).fetchone()
    if row is None:
        raise ApiError(
            401,
            "invalid_api_key",
            "The provided API key is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return dict(row)
