"""SAIL Sagar Drishti — Database & User Repository Layer (MongoDB).
Handles secure persistent storage, indexing, and transactional credential verification.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import uuid
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import pymongo
from pymongo.errors import PyMongoError, DuplicateKeyError, ConnectionFailure, ServerSelectionTimeoutError

from .security import hash_password, verify_password, validate_password_strength, validate_email

logger = logging.getLogger(__name__)

# Service Unavailable fallback message
SERVICE_UNAVAILABLE_MSG = "Authentication service is temporarily unavailable. Please try again."


class AuthDatabase:
    """Enterprise MongoDB authentication repository with automatic indexing and connection pooling."""

    def __init__(self, uri: str | None = None, db_name: str | None = None, timeout_ms: int = 2500):
        self.uri = uri or os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "mongodb://localhost:27017/"
        self.db_name = db_name or os.getenv("MONGODB_DB_NAME") or os.getenv("MONGO_DB_NAME") or "sail_chartering"
        self.timeout_ms = timeout_ms
        self._client: pymongo.MongoClient | None = None
        self._db: pymongo.database.Database | None = None
        self._users: pymongo.collection.Collection | None = None
        self._indexes_initialized = False

    def _get_collection(self) -> pymongo.collection.Collection:
        """Lazily initialize connection and ensure uniqueness indexes exist."""
        if self._users is None:
            self._client = pymongo.MongoClient(
                self.uri,
                serverSelectionTimeoutMS=self.timeout_ms,
                connectTimeoutMS=self.timeout_ms,
            )
            self._db = self._client[self.db_name]
            self._users = self._db["users"]

        if not self._indexes_initialized:
            try:
                # Create unique index on employee_id and email
                self._users.create_index("employee_id", unique=True, sparse=True)
                self._users.create_index("email", unique=True, sparse=True)
                self._indexes_initialized = True
            except Exception:
                pass

        return self._users

    def is_connected(self) -> bool:
        """Health check verifying database liveness."""
        try:
            coll = self._get_collection()
            coll.database.command("ping")
            return True
        except Exception:
            return False

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
        """Register a new user in MongoDB with strict validation and password hashing.
        
        Returns:
            tuple of (success: bool, message: str, user_dict: dict | None)
        """
        # 1. Mandatory fields check
        full_name = (full_name or "").strip()
        employee_id = (employee_id or "").strip().upper()
        email = (email or "").strip().lower()
        department = (department or "").strip()
        designation = (designation or "").strip()

        if not full_name or not employee_id or not email or not password or not confirm_password:
            return False, "All mandatory fields must be filled.", None

        # 2. Email format validation
        if not validate_email(email):
            return False, "Please enter a valid email address.", None

        # 3. Password match validation
        if password != confirm_password:
            return False, "Passwords do not match.", None

        # 4. Password complexity validation
        pw_ok, pw_msg = validate_password_strength(password)
        if not pw_ok:
            return False, pw_msg, None

        try:
            coll = self._get_collection()

            # 5. Pre-check uniqueness for clean specific error messaging
            if coll.find_one({"email": email}):
                return False, "An account with this email already exists.", None
            if coll.find_one({"employee_id": employee_id}):
                return False, "An account with this Employee ID already exists.", None

            # 6. Cryptographically hash password (PBKDF2-HMAC-SHA256, 100k rounds)
            pwd_hash = hash_password(password)
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            user_id = f"usr_{uuid.uuid4().hex[:12]}"

            user_doc = {
                "user_id": user_id,
                "full_name": full_name,
                "employee_id": employee_id,
                "email": email,
                "password_hash": pwd_hash,
                "department": department or "Raw Material Logistics",
                "designation": designation or "Chartering Officer",
                "created_at": now_iso,
                "last_login": None,
            }

            coll.insert_one(user_doc)

            # Return safe representation with zero password info
            safe_user = {
                "user_id": user_id,
                "full_name": full_name,
                "employee_id": employee_id,
                "email": email,
                "department": user_doc["department"],
                "designation": user_doc["designation"],
                "created_at": now_iso,
            }
            return True, "Account created successfully. Please login.", safe_user

        except DuplicateKeyError as dke:
            err_str = str(dke).lower()
            if "employee_id" in err_str:
                return False, "An account with this Employee ID already exists.", None
            return False, "An account with this email already exists.", None

        except (PyMongoError, ConnectionFailure, ServerSelectionTimeoutError, OSError):
            return False, SERVICE_UNAVAILABLE_MSG, None
        except Exception:
            return False, SERVICE_UNAVAILABLE_MSG, None

    def authenticate_user(
        self,
        identifier: str,
        password: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Authenticate user against MongoDB credentials.
        
        Identifier can be either Employee ID (e.g. SAIL-1234) or Official Email.
        Does NOT reveal whether the email or user ID exists separately upon failure.
        """
        identifier = (identifier or "").strip()
        if not identifier or not password:
            return False, "Invalid User ID/Email or Password.", None

        norm_emp = identifier.upper()
        norm_email = identifier.lower()

        try:
            coll = self._get_collection()
            user = coll.find_one({
                "$or": [
                    {"employee_id": norm_emp},
                    {"email": norm_email},
                ]
            })

            # Check if user exists and password hash matches
            if not user or not verify_password(password, user.get("password_hash", "")):
                return False, "Invalid User ID/Email or Password.", None

            # Update last_login timestamp safely
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                coll.update_one({"_id": user["_id"]}, {"$set": {"last_login": now_iso}})
            except Exception:
                pass

            safe_user = {
                "user_id": user.get("user_id", str(user.get("_id"))),
                "full_name": user.get("full_name", "SAIL Officer"),
                "employee_id": user.get("employee_id", norm_emp),
                "email": user.get("email", norm_email),
                "department": user.get("department", "Raw Material Logistics"),
                "designation": user.get("designation", "Chartering Officer"),
                "last_login": now_iso,
            }
            return True, "Authentication successful.", safe_user

        except (PyMongoError, ConnectionFailure, ServerSelectionTimeoutError, OSError):
            return False, SERVICE_UNAVAILABLE_MSG, None
        except Exception:
            return False, SERVICE_UNAVAILABLE_MSG, None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        """Fetch safe user record by user_id."""
        try:
            coll = self._get_collection()
            user = coll.find_one({"user_id": user_id}, {"password_hash": 0, "_id": 0})
            return user
        except Exception:
            return None


# Global singleton instance
_GLOBAL_AUTH_DB: AuthDatabase | None = None


def get_auth_db() -> AuthDatabase:
    """Get or create singleton AuthDatabase client instance."""
    global _GLOBAL_AUTH_DB
    if _GLOBAL_AUTH_DB is None:
        _GLOBAL_AUTH_DB = AuthDatabase()
    return _GLOBAL_AUTH_DB
