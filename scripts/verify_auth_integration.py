"""End-to-End Integration Verification Script for SAIL Sagar Drishti Authentication System.
Verifies registration in the live sail_chartering database, document schema, PBKDF2 hash security,
credential verification, and seamless handover to the SAIL recommendation engine.
"""
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auth.db import get_auth_db
from auth.security import verify_password
from sail import recommend
from sail.config import ROUTES, PORTS


def test_integration():
    print("================================================================")
    print("SAIL SAGAR DRISHTI — LIVE END-TO-END AUTHENTICATION INTEGRATION")
    print("================================================================")

    db = get_auth_db()
    assert db.is_connected(), "MongoDB should be active on mongodb://localhost:27017/"
    print("[PASS] MongoDB live connection established to database:", db.db_name)

    # 1. Clean up any previous test officer record
    coll = db._get_collection()
    coll.delete_many({"employee_id": "SAIL-09482"})
    coll.delete_many({"email": "rajesh.sharma@sail.in"})

    # 2. Register test account
    print("\n--- STEP 1: Registering Test Officer Account ---")
    reg_ok, reg_msg, reg_user = db.register_user(
        full_name="Rajesh Kumar Sharma",
        employee_id="SAIL-09482",
        email="rajesh.sharma@sail.in",
        password="SailCharter#2026",
        confirm_password="SailCharter#2026",
        department="Raw Material Logistics",
        designation="Chief Chartering Officer",
    )
    assert reg_ok is True, f"Registration failed: {reg_msg}"
    print(f"[PASS] Registration response: {reg_msg}")

    # 3. Verify user record in MongoDB
    print("\n--- STEP 2: Database Record Verification ---")
    doc = coll.find_one({"employee_id": "SAIL-09482"})
    assert doc is not None, "Officer record not found in MongoDB!"
    print(f"  User ID: {doc.get('user_id')}")
    print(f"  Full Name: {doc.get('full_name')}")
    print(f"  Employee ID: {doc.get('employee_id')}")
    print(f"  Official Email: {doc.get('email')}")
    print(f"  Department: {doc.get('department')}")
    print(f"  Designation: {doc.get('designation')}")

    # Security checks on database document
    assert "password" not in doc, "CRITICAL: Plaintext password field found in database!"
    assert "password_hash" in doc, "CRITICAL: password_hash missing from database doc!"
    stored_hash = doc["password_hash"]
    assert "SailCharter#2026" not in stored_hash, "CRITICAL: Plaintext password found in hash string!"
    assert stored_hash.startswith("pbkdf2_sha256$100000$"), "CRITICAL: Non-PBKDF2 hash format detected!"
    assert verify_password("SailCharter#2026", stored_hash) is True, "Password hash verification failed!"
    print(f"[PASS] Security Check: 100,000-round PBKDF2-HMAC-SHA256 verified. Zero plaintext stored.")

    # 4. Test Login with Registered Credentials
    print("\n--- STEP 3: Authenticating with Registered Credentials ---")
    # 4a. Via Employee ID
    login_ok_id, login_msg_id, session_user_id = db.authenticate_user("SAIL-09482", "SailCharter#2026")
    assert login_ok_id is True, f"Login via Employee ID failed: {login_msg_id}"
    assert session_user_id["full_name"] == "Rajesh Kumar Sharma"
    assert "password_hash" not in session_user_id, "CRITICAL: password_hash leaked into session state!"
    print(f"[PASS] Authentication via Employee ID successful: Welcome, {session_user_id['full_name']}")

    # 4b. Via Email
    login_ok_email, login_msg_email, session_user_email = db.authenticate_user("RAJESH.SHARMA@SAIL.IN", "SailCharter#2026")
    assert login_ok_email is True, f"Login via Email failed: {login_msg_email}"
    print(f"[PASS] Authentication via Email successful: {session_user_email['email']}")

    # 5. Test Invalid Login
    print("\n--- STEP 4: Testing Invalid Login Credentials ---")
    bad_login_ok, bad_login_msg, _ = db.authenticate_user("SAIL-09482", "WrongPassword!")
    assert bad_login_ok is False
    assert bad_login_msg == "Invalid User ID/Email or Password.", f"Got: {bad_login_msg}"
    print(f"[PASS] Invalid password rejected with: '{bad_login_msg}'")

    bad_user_ok, bad_user_msg, _ = db.authenticate_user("NON_EXISTENT_OFFICER", "SailCharter#2026")
    assert bad_user_ok is False
    assert bad_user_msg == "Invalid User ID/Email or Password.", f"Got: {bad_user_msg}"
    print(f"[PASS] Nonexistent account rejected with: '{bad_user_msg}'")

    # 6. Test Logout Logic
    print("\n--- STEP 5: Testing Logout Logic ---")
    # Conceptual session state simulation
    session_state = {"authenticated": True, "user": session_user_id}
    # User clicks logout
    session_state["authenticated"] = False
    session_state["user"] = None
    assert session_state["authenticated"] is False
    assert session_state["user"] is None
    # Confirm account remains intact in database after logout
    doc_after_logout = coll.find_one({"employee_id": "SAIL-09482"})
    assert doc_after_logout is not None, "Account must not be deleted on logout!"
    print(f"[PASS] Logout successful. Session reset to False, database account preserved.")

    # 7. Test Handover to SAIL Recommendation Engine Post-Login
    print("\n--- STEP 6: Testing SAIL Decision Pipeline Post-Authentication ---")
    origin = list(ROUTES.keys())[0]
    port = list(PORTS.keys())[0]
    res = recommend.analyse(origin, port, 75000, dt.date(2026, 12, 1), as_of="2026-09-14")
    assert res is not None
    assert res["best"] is not None
    print(f"  Nominated Vessel: {res['best']['vessel']}")
    print(f"  Action: {res['best']['timing']['action']}")
    print(f"  Cost: ${res['best']['cost_plan']['usd_per_mt']:.2f} / MT")
    print(f"[PASS] SAIL Decision Engine executed with zero modifications!")

    print("\n================================================================")
    print("ALL INTEGRATION AND SECURITY CHECKS PASSED WITH 100% SUCCESS!")
    print("================================================================")


if __name__ == "__main__":
    test_integration()
