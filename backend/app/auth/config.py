import os
from functools import lru_cache
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


AUTH_ACCESS_TOKEN_MINUTES = max(1, int(os.getenv("AUTH_ACCESS_TOKEN_MINUTES", "60")))
AUTH_SESSION_HOURS = max(1, int(os.getenv("AUTH_SESSION_HOURS", "24")))
AUTH_MAX_FAILED_ATTEMPTS = max(1, int(os.getenv("AUTH_MAX_FAILED_ATTEMPTS", "5")))
AUTH_LOCK_MINUTES = max(1, int(os.getenv("AUTH_LOCK_MINUTES", "15")))
AUTH_MIN_PASSWORD_LENGTH = max(6, int(os.getenv("AUTH_MIN_PASSWORD_LENGTH", "6")))
AUTH_TEMP_PASSWORD_LENGTH = max(16, int(os.getenv("AUTH_TEMP_PASSWORD_LENGTH", "16")))
AUTH_JWT_ALGORITHM = os.getenv("AUTH_JWT_ALGORITHM", "HS256").strip() or "HS256"
AUTH_JWT_SECRET = os.getenv("AUTH_JWT_SECRET", "").strip()
AUTH_REFRESH_COOKIE_NAME = os.getenv("AUTH_REFRESH_COOKIE_NAME", "fyp_refresh_token").strip() or "fyp_refresh_token"
AUTH_COOKIE_SECURE = _env_bool("AUTH_COOKIE_SECURE", False)
AUTH_COOKIE_SAMESITE = (os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax")
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN", "").strip() or None

# Temporary fixed-account authentication mode.
FIXED_AUTH_ENABLED = _env_bool("FIXED_AUTH_ENABLED", False)
FIXED_ADMIN_EMAIL = os.getenv("FIXED_ADMIN_EMAIL", "").strip().lower()
FIXED_ADMIN_NAME = os.getenv("FIXED_ADMIN_NAME", "Administrator").strip() or "Administrator"
FIXED_ADMIN_PASSWORD_HASH = os.getenv("FIXED_ADMIN_PASSWORD_HASH", "").strip()

def require_jwt_secret() -> str:
    if len(AUTH_JWT_SECRET) < 32:
        raise RuntimeError(
            "AUTH_JWT_SECRET must be configured with at least 32 characters before authentication can be used."
        )
    return AUTH_JWT_SECRET


@lru_cache(maxsize=1)
def database_url() -> str:
    explicit = os.getenv("AUTH_DATABASE_URL", "").strip()
    if explicit:
        return explicit

    host = os.getenv("AUTH_DB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("AUTH_DB_PORT", "3306"))
    name = os.getenv("AUTH_DB_NAME", "fyp_auth").strip() or "fyp_auth"
    user = os.getenv("AUTH_DB_USER", "root").strip() or "root"
    password = os.getenv("AUTH_DB_PASSWORD", "")
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@"
        f"{host}:{port}/{quote_plus(name)}?charset=utf8mb4"
    )
