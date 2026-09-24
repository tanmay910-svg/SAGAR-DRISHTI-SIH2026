"""SAIL Sagar Drishti — Supabase Authentication Layer.

Authentication is handled by Supabase Auth instead of a self-hosted MongoDB
instance. The application only keeps the Supabase project URL and public
anon key in environment/Streamlit secrets; passwords are handled by Supabase.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import streamlit as st
except Exception:
    st = None

from supabase import create_client, Client

from .security import validate_password_strength, validate_email

logger = logging.getLogger(__name__)

SERVICE_UNAVAILABLE_MSG = "Authentication service is temporarily unavailable. Please try again."


def _secret(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value
    if st is not None:
        try:
            value = st.secrets.get(name)
            if value:
                return str(value)
        except Exception:
            pass
    return default


class AuthDatabase:
    """Compatibility wrapper around Supabase Auth.

    The existing application calls this class AuthDatabase, so the UI does not
    need to know which authentication provider is used.
    """

    def __init__(self) -> None:
        self.url = _secret("SUPABASE_URL").strip()
        self.anon_key = _secret("SUPABASE_ANON_KEY").strip()
        self._client: Client | None = None

    def _get_client(self) -> Client:
        if not self.url or not self.anon_key:
            raise RuntimeError("Supabase credentials are not configured.")
        if self._client is None:
            self._client = create_client(self.url, self.anon_key)
        return self._client

    def is_connected(self) -> bool:
        """Return whether Supabase credentials are configured and usable."""
        try:
            self._get_client()
            return True
        except Exception:
            return False

    @staticmethod
    def _safe_user(user: Any) -> dict[str, Any] | None:
        if not user:
            return None

        metadata = getattr(user, "user_metadata", None) or {}
        created_at = getattr(user, "created_at", None)
        return {
            "user_id": getattr(user, "id", ""),
            "full_name": metadata.get("full_name", "SAIL Officer"),
            "employee_id": metadata.get("employee_id", ""),
            "email": getattr(user, "email", ""),
            "department": metadata.get("department", "Raw Material Logistics"),
            "designation": metadata.get("designation", "Chartering Officer"),
            "created_at": created_at or dt.datetime.now(dt.timezone.utc).isoformat(),
            "last_login": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def register_user(
        self,
        full_name: str,
        employee_id: str,
        email: str,
        password: str,
        confirm_password: str,
        department: str = "",
        designation: str = "",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Register a user with Supabase email/password authentication."""
        full_name = (full_name or "").strip()
        employee_id = (employee_id or "").strip().upper()
        email = (email or "").strip().lower()
        department = (department or "").strip()
        designation = (designation or "").strip()

        if not full_name or not employee_id or not email or not password or not confirm_password:
            return False, "All mandatory fields must be filled.", None

        if not validate_email(email):
            return False, "Please enter a valid email address.", None

        if password != confirm_password:
            return False, "Passwords do not match.", None

        pw_ok, pw_msg = validate_password_strength(password)
        if not pw_ok:
            return False, pw_msg, None

        try:
            response = self._get_client().auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name,
                        "employee_id": employee_id,
                        "department": department or "Raw Material Logistics",
                        "designation": designation or "Chartering Officer",
                    }
                },
            })

            user = getattr(response, "user", None)
            session = getattr(response, "session", None)

            if not user:
                return False, "Account could not be created. Please try again.", None

            safe_user = self._safe_user(user)

            # For the SIH deployment, email confirmation should be disabled
            # in Supabase so registration can immediately return a session.
            if session:
                return True, "Account created successfully. Please login.", safe_user

            return (
                True,
                "Account created successfully. Please verify your email before login.",
                safe_user,
            )

        except Exception as exc:
            msg = str(exc).lower()
            if "already registered" in msg or "already exists" in msg:
                return False, "An account with this email already exists.", None
            if "password" in msg and ("weak" in msg or "short" in msg):
                return False, "Password does not meet the required strength.", None
            logger.warning("Supabase registration failed: %s", exc)
            return False, SERVICE_UNAVAILABLE_MSG, None

    def authenticate_user(
        self,
        identifier: str,
        password: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Authenticate by official email and password through Supabase Auth."""
        identifier = (identifier or "").strip()
        password = password or ""

        if not identifier or not password:
            return False, "Invalid Email or Password.", None

        # Supabase password authentication uses email/phone as the identifier.
        # Employee ID is retained as profile metadata for the authenticated user.
        if "@" not in identifier:
            return False, "Please login using the email address used during registration.", None

        try:
            response = self._get_client().auth.sign_in_with_password({
                "email": identifier.lower(),
                "password": password,
            })
            user = getattr(response, "user", None)

            if not user:
                return False, "Invalid Email or Password.", None

            return True, "Authentication successful.", self._safe_user(user)

        except Exception as exc:
            logger.warning("Supabase login failed: %s", exc)
            return False, "Invalid Email or Password.", None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        # User data is maintained by Supabase Auth; the current application
        # only needs the session user returned during authentication.
        return None


_GLOBAL_AUTH_DB: AuthDatabase | None = None


def get_auth_db() -> AuthDatabase:
    """Get or create the singleton Supabase authentication client."""
    global _GLOBAL_AUTH_DB
    if _GLOBAL_AUTH_DB is None:
        _GLOBAL_AUTH_DB = AuthDatabase()
    return _GLOBAL_AUTH_DB
