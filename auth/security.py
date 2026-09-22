"""SAIL Sagar Drishti — Cryptographic & Validation Security Service.
Implements NIST/OWASP approved PBKDF2-HMAC-SHA256 password hashing with constant-time verification.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Optional


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 rounds and a cryptographically secure 16-byte salt.
    
    Format: pbkdf2_sha256$<iterations>$<salt>$<hex_digest>
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    salt = secrets.token_hex(16)
    iterations = 100000
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored PBKDF2-HMAC-SHA256 hash using constant-time comparison."""
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = parts[2]
        expected_hex = parts[3]
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return hmac.compare_digest(derived.hex(), expected_hex)
    except Exception:
        return False


def validate_email(email: str) -> bool:
    """Validate official email format."""
    if not isinstance(email, str):
        return False
    email = email.strip()
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return bool(re.match(pattern, email))


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Verify password satisfies reasonable security requirements:
    - At least 8 characters
    - Contains at least one alphabet letter
    - Contains at least one numerical digit
    """
    if not isinstance(password, str) or len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isalpha() for c in password):
        return False, "Password must contain at least one letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    return True, ""


def create_access_token(data: dict, expires_delta: Optional[dt.timedelta] = None) -> str:
    """Generate standard RFC 7519 compliant HMAC-SHA256 JWT access token.
    Uses JWT_SECRET_KEY / SECRET_KEY environment variable.
    """
    to_encode = data.copy()
    expire_minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    if expires_delta:
        expire = dt.datetime.now(dt.timezone.utc) + expires_delta
    else:
        expire = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=expire_minutes)
    to_encode["exp"] = int(expire.timestamp())

    secret_key = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or "sail-sagar-drishti-jwt-secret-key-2026"

    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        .rstrip(b"=")
        .decode("utf-8")
    )
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))
        .rstrip(b"=")
        .decode("utf-8")
    )

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("utf-8")

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> Optional[dict]:
    """Verify and decode standard RFC 7519 HMAC-SHA256 JWT access token."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        secret_key = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or "sail-sagar-drishti-jwt-secret-key-2026"
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode("utf-8")
        if not hmac.compare_digest(sig_b64, expected_sig_b64):
            return None
        rem = len(payload_b64) % 4
        padded = payload_b64 + ("=" * (4 - rem) if rem else "")
        payload_json = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        payload = json.loads(payload_json)
        if "exp" in payload:
            now_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
            if now_ts > payload["exp"]:
                return None
        return payload
    except Exception:
        return None

