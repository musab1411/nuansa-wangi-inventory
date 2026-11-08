"""
Autentikasi API key per cabang + admin.

Aturan:
    X-API-Key: <kunci cabang>   -> hanya boleh transaksi untuk cabang itu
    X-API-Key: <ADMIN_API_KEY>  -> boleh melihat semua cabang + dashboard

Kunci mentah tidak pernah disimpan di database; yang disimpan hanya
SHA-256 hex-nya (kolom branches.api_key_hash).
"""
import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from . import db
from .metrics import METRICS

logger = logging.getLogger("inventory.auth")

AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "true").strip().lower() not in (
    "0", "false", "no", "off",
)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

# Skema ini membuat tombol "Authorize" muncul di Swagger (/docs).
api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Kunci cabang (mis. nw-demo-branch-a-...) atau kunci admin.",
)


@dataclass(frozen=True)
class AuthContext:
    branch_id: Optional[str]
    is_admin: bool
    key_id: str  # potongan hash untuk log/rate limit, bukan kunci mentah


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fail(status: int, code: str, message: str) -> None:
    if status == 401:
        METRICS.inc("nw_auth_failures_total", reason=code)
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def require_auth(
    x_api_key: Optional[str] = Security(api_key_header),
) -> AuthContext:
    """Dependency FastAPI: validasi header X-API-Key."""
    if not AUTH_REQUIRED:
        return AuthContext(branch_id=None, is_admin=True, key_id="auth-off")

    if not x_api_key:
        _fail(401, "API_KEY_REQUIRED", "Header X-API-Key wajib diisi.")

    raw = x_api_key.strip()

    # Admin memakai kunci dari environment, bukan dari tabel branches.
    if ADMIN_API_KEY and hmac.compare_digest(raw, ADMIN_API_KEY):
        return AuthContext(branch_id=None, is_admin=True, key_id="admin")

    key_hash = hash_key(raw)
    branch_id = db.branch_by_api_key(key_hash)
    if branch_id is None:
        _fail(
            401,
            "API_KEY_INVALID",
            "API key tidak dikenal, atau cabang sedang dinonaktifkan.",
        )

    return AuthContext(branch_id=branch_id, is_admin=False, key_id=key_hash[:12])


def require_admin(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    """Dependency untuk endpoint khusus admin/dashboard."""
    if not ctx.is_admin:
        _fail(403, "ADMIN_REQUIRED", "Endpoint ini hanya untuk admin/dashboard.")
    return ctx
