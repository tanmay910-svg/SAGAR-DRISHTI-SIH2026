"""SAIL Sagar Drishti — Authentication & Identity Management Layer.
Provides secure user registration, credential verification, and session management.
"""
from .security import hash_password, verify_password, validate_password_strength, validate_email
from .db import get_auth_db, AuthDatabase

__all__ = [
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "validate_email",
    "get_auth_db",
    "AuthDatabase",
]
