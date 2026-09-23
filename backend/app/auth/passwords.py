import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

from app.auth.config import AUTH_MIN_PASSWORD_LENGTH, AUTH_TEMP_PASSWORD_LENGTH


_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def validate_private_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < AUTH_MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at least {AUTH_MIN_PASSWORD_LENGTH} characters.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError):
        return False


def password_hash_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


def generate_temporary_password(length: int = AUTH_TEMP_PASSWORD_LENGTH) -> str:
    length = max(16, int(length))
    symbols = "!@#$%&*?"
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    alphabet = string.ascii_letters + string.digits + symbols
    required.extend(secrets.choice(alphabet) for _ in range(length - len(required)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)
