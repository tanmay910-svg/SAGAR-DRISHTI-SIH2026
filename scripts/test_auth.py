"""Automated test suite for SAIL Sagar Drishti Authentication & Security Layer.
Tests all 11 core requirements: registration, duplicate prevention, password matching,
hashing security, credential verification, and graceful database error handling.
"""
import os
import sys
import uuid
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auth.security import hash_password, verify_password, validate_password_strength, validate_email
from auth.db import AuthDatabase, SERVICE_UNAVAILABLE_MSG


def run_tests():
    print("==================================================")
    print("STARTING SAIL AUTHENTICATION & SECURITY TEST SUITE")
    print("==================================================")

    db = AuthDatabase(db_name="sail_chartering_test")
    assert db.is_connected(), "MongoDB should be reachable on localhost:27017"
    print("[PASS] MongoDB connection verified.")

    # Clean test collection
    test_coll = db._get_collection()
    test_coll.delete_many({})

    # Unique test IDs
    rand_id = uuid.uuid4().hex[:6].upper()
    test_emp = f"SAIL-{rand_id}"
    test_email = f"officer_{rand_id.lower()}@sail.in"
    test_pass = "SailSecure2026!"

    # ----------------------------------------------------
    # TEST 1: Register a new valid user
    # ----------------------------------------------------
    print("\n--- TEST 1: Register a new valid user ---")
    ok, msg, user = db.register_user(
        full_name="Vikramaditya Roy",
        employee_id=test_emp,
        email=test_email,
        password=test_pass,
        confirm_password=test_pass,
        department="Raw Material Logistics",
        designation="Senior Chartering Manager",
    )
    assert ok is True, f"Registration failed: {msg}"
    assert "Account created successfully" in msg, f"Unexpected message: {msg}"
    assert user is not None
    assert user["employee_id"] == test_emp
    assert user["email"] == test_email
    print(f"[PASS] TEST 1: {msg}")

    # Verify document in MongoDB
    doc = test_coll.find_one({"employee_id": test_emp})
    assert doc is not None, "User doc not found in MongoDB"
    assert "password" not in doc, "Plaintext 'password' key found in database doc!"
    assert "password_hash" in doc, "Missing password_hash in database doc!"
    assert doc["password_hash"].startswith("pbkdf2_sha256$100000$"), "Invalid password hash format"
    assert test_pass not in doc["password_hash"], "Plaintext password leaked in hash string!"
    print(f"[PASS] TEST 1 DB VERIFICATION: No plaintext password stored. Hash = {doc['password_hash'][:35]}...")

    # ----------------------------------------------------
    # TEST 2: Register same email again
    # ----------------------------------------------------
    print("\n--- TEST 2: Register same email again ---")
    ok2, msg2, _ = db.register_user(
        full_name="Another Officer",
        employee_id=f"SAIL-DIFF-{rand_id}",
        email=test_email,  # same email
        password=test_pass,
        confirm_password=test_pass,
    )
    assert ok2 is False, "Duplicate email registration should fail"
    assert msg2 == "An account with this email already exists.", f"Got: {msg2}"
    print(f"[PASS] TEST 2: {msg2}")

    # ----------------------------------------------------
    # TEST 3: Register same Employee ID again
    # ----------------------------------------------------
    print("\n--- TEST 3: Register same Employee ID again ---")
    ok3, msg3, _ = db.register_user(
        full_name="Third Officer",
        employee_id=test_emp,  # same employee ID
        email=f"diff_{rand_id}@sail.in",
        password=test_pass,
        confirm_password=test_pass,
    )
    assert ok3 is False, "Duplicate Employee ID registration should fail"
    assert msg3 == "An account with this Employee ID already exists.", f"Got: {msg3}"
    print(f"[PASS] TEST 3: {msg3}")

    # ----------------------------------------------------
    # TEST 4: Register with mismatched passwords
    # ----------------------------------------------------
    print("\n--- TEST 4: Register with mismatched passwords ---")
    ok4, msg4, _ = db.register_user(
        full_name="Fourth Officer",
        employee_id=f"SAIL-FOUR-{rand_id}",
        email=f"four_{rand_id}@sail.in",
        password="Password123",
        confirm_password="MismatchedPassword456",
    )
    assert ok4 is False, "Mismatched passwords should fail"
    assert msg4 == "Passwords do not match.", f"Got: {msg4}"
    print(f"[PASS] TEST 4: {msg4}")

    # ----------------------------------------------------
    # TEST 5: Login using correct credentials (both ID and Email)
    # ----------------------------------------------------
    print("\n--- TEST 5: Login using correct credentials ---")
    # 5a. Login via Employee ID
    ok5a, msg5a, user5a = db.authenticate_user(test_emp, test_pass)
    assert ok5a is True, f"Login via employee ID failed: {msg5a}"
    assert user5a["full_name"] == "Vikramaditya Roy"
    assert "password_hash" not in user5a, "Password hash leaked in user session dict!"
    print(f"[PASS] TEST 5a (Login via Employee ID): Welcome, {user5a['full_name']}")

    # 5b. Login via Email (case-insensitive)
    ok5b, msg5b, user5b = db.authenticate_user(test_email.upper(), test_pass)
    assert ok5b is True, f"Login via upper-cased email failed: {msg5b}"
    assert user5b["employee_id"] == test_emp
    print(f"[PASS] TEST 5b (Login via Email): {user5b['email']}")

    # ----------------------------------------------------
    # TEST 6: Login using incorrect password
    # ----------------------------------------------------
    print("\n--- TEST 6: Login using incorrect password ---")
    ok6, msg6, user6 = db.authenticate_user(test_emp, "WrongPassword999!")
    assert ok6 is False, "Login with wrong password should fail"
    assert msg6 == "Invalid User ID/Email or Password.", f"Got: {msg6}"
    assert user6 is None
    print(f"[PASS] TEST 6: {msg6}")

    # ----------------------------------------------------
    # TEST 7: Login using nonexistent account
    # ----------------------------------------------------
    print("\n--- TEST 7: Login using nonexistent account ---")
    ok7, msg7, user7 = db.authenticate_user("NON_EXISTENT_ID", "SomePassword123!")
    assert ok7 is False, "Login with non-existent account should fail"
    assert msg7 == "Invalid User ID/Email or Password.", f"Got: {msg7}"
    assert user7 is None
    print(f"[PASS] TEST 7: {msg7} (Zero account enumeration leakage)")

    # ----------------------------------------------------
    # TEST 8: Missing mandatory fields
    # ----------------------------------------------------
    print("\n--- TEST 8: Missing mandatory fields ---")
    ok8, msg8, _ = db.register_user("", test_emp, test_email, test_pass, test_pass)
    assert ok8 is False and msg8 == "All mandatory fields must be filled."
    print(f"[PASS] TEST 8: {msg8}")

    # ----------------------------------------------------
    # TEST 9: Password security check
    # ----------------------------------------------------
    print("\n--- TEST 9: Password security requirements ---")
    ok9, msg9, _ = db.register_user("Officer", f"SAIL-SHRT-{rand_id}", f"short_{rand_id}@sail.in", "short", "short")
    assert ok9 is False and "at least 8 characters" in msg9
    print(f"[PASS] TEST 9: {msg9}")

    # ----------------------------------------------------
    # TEST 10: Invalid email format check
    # ----------------------------------------------------
    print("\n--- TEST 10: Invalid email format check ---")
    ok10, msg10, _ = db.register_user("Officer", f"SAIL-EM-{rand_id}", "not-an-email", test_pass, test_pass)
    assert ok10 is False and "valid email address" in msg10
    print(f"[PASS] TEST 10: {msg10}")

    # ----------------------------------------------------
    # TEST 11: Database unavailable simulation
    # ----------------------------------------------------
    print("\n--- TEST 11: Database unavailable simulation ---")
    # Point to non-routable port with quick timeout
    offline_db = AuthDatabase(uri="mongodb://127.0.0.1:27999/", timeout_ms=300)
    ok11_reg, msg11_reg, _ = offline_db.register_user(
        "Offline Test", f"OFF-{rand_id}", f"off_{rand_id}@sail.in", test_pass, test_pass
    )
    assert ok11_reg is False
    assert msg11_reg == SERVICE_UNAVAILABLE_MSG, f"Got: {msg11_reg}"
    print(f"[PASS] TEST 11a (Offline Registration): {msg11_reg}")

    ok11_login, msg11_login, _ = offline_db.authenticate_user(test_emp, test_pass)
    assert ok11_login is False
    assert msg11_login == SERVICE_UNAVAILABLE_MSG, f"Got: {msg11_login}"
    print(f"[PASS] TEST 11b (Offline Login): {msg11_login}")

    # Clean up test collection
    test_coll.delete_many({})

    print("\n==================================================")
    print("ALL 11 AUTHENTICATION & SECURITY TESTS PASSED 100%!")
    print("==================================================")


if __name__ == "__main__":
    run_tests()
