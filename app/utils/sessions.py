from __future__ import annotations

from typing import Optional

from itsdangerous import URLSafeTimedSerializer

from app.config import settings

_signer = URLSafeTimedSerializer(settings.secret_key)
SESSION_COOKIE = "tf_contractor_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def create_session_token(contractor_id: str) -> str:
    return _signer.dumps(contractor_id, salt="contractor-session")


def decode_session_token(token: str) -> Optional[str]:
    try:
        return _signer.loads(token, salt="contractor-session", max_age=SESSION_MAX_AGE)
    except Exception:
        return None
