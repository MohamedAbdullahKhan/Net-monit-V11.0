# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah. All rights reserved.
# Contact: abuabdullah.be@outlook.com
# Unauthorised copying, modification or distribution of this software
# is strictly prohibited without prior written permission from the author.
# =============================================================================
"""
auth.py  —  Net-monit V11.0
Two roles:
  admin — full access: dashboard + all config, devices, alerts, user mgmt
  user  — read-only dashboard only (status tiles, no host IPs, no config)

Default admin on first run:
  email : admin@email.com
  token : randomly generated per install (see FIRST_RUN_TOKEN_FILE below) --
          shown once in the console/log and in data/FIRST-RUN-ADMIN-TOKEN.txt,
          which is deleted automatically on first successful login
Both can be changed or deleted via the Admin Panel once logged in.
Access tokens stored as PBKDF2-HMAC-SHA256 (210k iterations, per-user salt) —
raw values never written to disk. Existing accounts on the older unsalted
SHA-256 format upgrade automatically on next successful login (see
attempt_login()). Email hashing (used for lookups, not secrecy) stays
plain SHA-256 deliberately.
Sessions: 32-byte random hex, server-side in-memory dict, 1-hour idle TTL.
"""
import hashlib, hmac, secrets, time, threading, os
from pathlib import Path
from . import database as db

AUTH_SESSION_TTL_SEC = 3600   # 1 hour idle

DEFAULT_ADMIN_EMAIL = "admin@email.com"
# V10.0: no longer a fixed token. A hardcoded credential shipped in source
# that goes out to every customer is a textbook default-credential
# vulnerability (CWE-798) -- anyone who's ever read this file knows the
# login for any install where the admin hasn't gotten around to changing
# it yet, which is exactly the kind of thing that doesn't get noticed
# until it's a problem. generate_token() (already used for every other
# token in the app) makes a fresh 12-char random one per install instead,
# shown ONCE at first boot. Never logged again after that.

# Where the one-time first-run token gets written. print()-only is NOT
# enough here: the installer registers the service to run under
# pythonw.exe specifically so no console window stays open (see
# install_service.py), which means stdout has nowhere to go when running
# as an actual installed service -- the normal, intended way this app
# runs day to day. A file next to the database (same data/ dir convention
# database.py already uses) is guaranteed discoverable regardless of how
# the process was started. Deleted automatically the first time this
# specific account successfully logs in -- see attempt_login() below --
# so the plaintext token doesn't linger on disk past the moment it's used.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FIRST_RUN_TOKEN_FILE = _DATA_DIR / "FIRST-RUN-ADMIN-TOKEN.txt"

_sessions = {}   # session_token -> {"email": str, "role": str, "last_seen": float}
_lock     = threading.Lock()


# ── hashing ────────────────────────────────────────────────────────────────
def _sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()

# V10.0: access tokens now hash with PBKDF2-HMAC-SHA256 (210k iterations,
# matching current OWASP guidance), not plain unsalted SHA-256. A fast
# general-purpose hash with no salt is below current best practice for
# anything that gates login -- if the database ever leaked, every token
# short enough to be human-typeable would be crackable offline in a
# fraction of a second per guess, whereas PBKDF2 at this iteration count
# costs real, deliberate time per guess. Only the ACCESS TOKEN gets this
# treatment. Email hashing (_sha256 above, used for lookups) and the
# short-lived, already rate-limited OTP/reset-verification hashes further
# down this file stay on plain SHA-256 deliberately -- those aren't
# long-lived credentials gating an account the way the access token is,
# and applying a slow KDF to every one of them would add real complexity
# for comparatively little benefit. This is a scoping choice, not an
# oversight -- flagged here so it reads as a decision, not a gap.
#
# No new dependency: hashlib.pbkdf2_hmac is stdlib, has been since
# Python 3.4. Format: "pbkdf2$<iterations>$<salt_hex>$<hash_hex>",
# self-describing so _verify_token_hash() can tell it apart from a
# legacy plain-SHA-256 hash (a bare 64-char hex string) with no separate
# schema/column needed -- existing rows just stay as bare hex until the
# account in question next logs in successfully, at which point
# attempt_login() re-hashes and persists the new format. No forced
# reset, no migration script, no downtime -- it phases in as people
# use the app.
_PBKDF2_ITERATIONS = 210_000

def _hash_token_pbkdf2(raw_token: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", raw_token.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"

def _verify_token_hash(raw_token: str, stored_hash: str) -> bool:
    """Constant-time verification against either format."""
    if stored_hash.startswith("pbkdf2$"):
        try:
            _, iter_s, salt_hex, hash_hex = stored_hash.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", raw_token.encode(), bytes.fromhex(salt_hex), int(iter_s))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except (ValueError, IndexError):
            return False
    return hmac.compare_digest(_sha256(raw_token), stored_hash)

# A syntactically-valid dummy of the new format, used by attempt_login()
# below to run a full (slow) verification even when no account matches
# the given email -- otherwise a nonexistent-email request returns
# almost instantly while a real-email-wrong-token request takes as long
# as a PBKDF2 round, and that timing difference alone is enough to
# enumerate which emails have accounts on this install.
_DUMMY_HASH = f"pbkdf2${_PBKDF2_ITERATIONS}${'0'*32}${'0'*64}"


# ── token generation ───────────────────────────────────────────────────────
def generate_token() -> str:
    """Random 12-char token: upper + lower + digit + special."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(12))


# ── user management ────────────────────────────────────────────────────────
def add_user(email: str, role: str, raw_token: str):
    """
    Hash the token and persist the user.
    V10.0: three roles now -- 'admin' (full access), 'supervisor' (can view
    every page, cannot change anything -- enforced by every mutation route
    already requiring _require_admin(), which a supervisor session fails
    same as a view-only one does), 'user' (kept as the internal value for
    what's labelled "View" in the UI -- dashboard + speed test only,
    unchanged behavior, no rename to avoid touching every existing
    account's stored role value).
    """
    if role not in ("admin", "supervisor", "user"):
        role = "user"
    db.add_user(email.lower().strip(), role, _hash_token_pbkdf2(raw_token))


def seed_default_admin_if_empty():
    """
    Creates the default admin on first run if no users exist at all, with
    a randomly generated token unique to this install (V10.0 -- see the
    comment above DEFAULT_ADMIN_EMAIL for why this is no longer a fixed
    value). Printed to the console/service log ONCE, at the moment of
    creation, and never again -- if it's lost, use the Admin Panel (once
    logged in some other way) or the "Forgot your token?" flow, same as
    for any other account. Change it immediately via the Admin Panel
    regardless; a token that's been sitting in a log file is still
    better than one published in source, but it's still worth rotating.
    """
    if db.get_all_users():
        return   # already seeded
    first_run_token = generate_token()
    add_user(DEFAULT_ADMIN_EMAIL, "admin", first_run_token)

    banner = "=" * 60
    console_msg = (
        f"\n{banner}\n"
        "  FIRST-RUN DEFAULT ADMIN CREATED\n"
        f"  Email : {DEFAULT_ADMIN_EMAIL}\n"
        f"  Token : {first_run_token}\n"
        "  This token is shown ONLY this one time -- it is not stored\n"
        "  anywhere in plaintext and will not appear in this log again.\n"
        "  Log in via the Admin eye-button (top-right), then change or\n"
        "  remove this account from the Admin Panel.\n"
        f"{banner}\n"
    )
    print(console_msg)  # visible when running in a foreground terminal

    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        FIRST_RUN_TOKEN_FILE.write_text(
            "Net-monit -- first-run admin credentials\n"
            "=========================================\n"
            f"Email : {DEFAULT_ADMIN_EMAIL}\n"
            f"Token : {first_run_token}\n\n"
            "This file is deleted automatically the first time you log in\n"
            "with this account. Change or remove the account from the\n"
            "Admin Panel once you're in -- don't leave this as your\n"
            "permanent admin credential.\n",
            encoding="utf-8",
        )
    except OSError:
        # Console output above is still the fallback if the data/ dir
        # somehow isn't writable at this exact moment -- don't let a
        # filesystem issue here block startup.
        pass


# ── authentication ─────────────────────────────────────────────────────────
def attempt_login(email: str, raw_token: str):
    """
    Returns (session_token, role) if valid, else (None, None).
    Accepts admin, supervisor, and user roles — callers decide what each can access.

    V10.0: looks up by email directly (db.get_user_by_email -- already
    existed, used elsewhere) and verifies the token in application code,
    rather than the old single-query "WHERE email_hash=? AND
    token_hash=?" -- that pattern only works when token_hash is a plain
    deterministic hash you can match with SQL "=". PBKDF2's per-user
    random salt means the same token hashes differently every time, so
    verification has to fetch the stored hash first and check against
    it, not search for it.
    """
    email = email.lower().strip()
    user = db.get_user_by_email(email)
    stored_hash = user["token_hash"] if user else None

    # Always run a full verification, even on a miss, against a dummy
    # hash of the same format/cost -- keeps this function's running time
    # roughly constant whether or not the email exists, so timing alone
    # can't be used to enumerate registered accounts. See _DUMMY_HASH's
    # comment above for why this matters specifically for PBKDF2 (a fast
    # early-return on "no such user" would be measurably quicker than a
    # 210k-iteration verification, in a way a fixed SHA-256 comparison
    # never was).
    ok = _verify_token_hash(raw_token, stored_hash or _DUMMY_HASH)
    if not user or not ok:
        return None, None

    # Lazy upgrade: this account just proved it knows its own token, so
    # if it's still on the legacy unsalted-SHA-256 format, re-hash with
    # PBKDF2 now and persist -- phases the stronger scheme in as people
    # log in, no forced reset, no separate migration step.
    if not stored_hash.startswith("pbkdf2$"):
        db.add_user(email, user["role"], _hash_token_pbkdf2(raw_token))

    sess = secrets.token_hex(32)
    with _lock:
        _sessions[sess] = {
            "email":     email,
            "role":      user["role"],
            "last_seen": time.time(),
        }

    if email == DEFAULT_ADMIN_EMAIL:
        try:
            FIRST_RUN_TOKEN_FILE.unlink(missing_ok=True)
        except OSError:
            pass  # not worth failing a successful login over

    return sess, user["role"]


def validate_session(session_token: str):
    """
    Returns the session dict {"email", "role"} if valid, else None.
    Refreshes last_seen on every call.
    """
    if not session_token:
        return None
    with _lock:
        entry = _sessions.get(session_token)
        if not entry:
            return None
        if time.time() - entry["last_seen"] > AUTH_SESSION_TTL_SEC:
            del _sessions[session_token]
            return None
        entry["last_seen"] = time.time()
        return dict(entry)


def logout(session_token: str):
    with _lock:
        _sessions.pop(session_token, None)


# ── V10.0: self-service token reset, OTP-based ("Forgot your token?") ──────
# Three stages, each a separate HTTP round-trip (see app.py's three routes):
#   1. request_token_reset(email)      -> emails a 6-digit code
#   2. verify_reset_otp(email, otp)    -> checks the code, issues a short-
#      lived "verified" marker (proves stage 1+2 happened, without the
#      user needing to re-enter the code for stage 3)
#   3. complete_token_reset(email, verified_token, new_raw_token) -> sets
#      the account's token to whatever the user chose or generated
#
# Replaces the V8.5 design, which emailed a clickable link containing a
# high-entropy token and auto-generated the replacement token on click --
# no OTP step, and no way to choose your own replacement. Both stages
# reuse the same token_reset_requests table (it's generic: email, a
# hashed single-use expiring token, nothing OTP-specific baked in) rather
# than adding new schema -- stage 1 stores a hash of the OTP, stage 2
# consumes that and stores a hash of a fresh "verified" token for stage 3
# to consume in turn.

OTP_TTL_SEC          = 10 * 60   # 10 minutes to enter the code
VERIFIED_TTL_SEC      = 10 * 60  # then 10 more minutes to actually pick a token
OTP_MAX_ATTEMPTS      = 5        # wrong-code attempts before forcing a fresh request

_otp_attempts = {}   # email -> (fail_count, first_attempt_ts) -- in-memory,
                      # same lifetime tradeoff as _sessions above: a
                      # restart clears it, which just means attempts reset
                      # to zero, not a security hole, since the OTP itself
                      # is still separately time-limited and single-use.

def request_token_reset(email: str):
    """
    Stage 1. Looks up `email`; if it belongs to an active user, generates
    a 6-digit OTP and returns it (app.py's route emails it, then
    discards the raw value -- only the hash is ever persisted). Returns
    None if the email doesn't match an active user.

    Deliberately silent on a miss rather than raising/erroring: app.py's
    route returns the exact same generic "if that email is registered,
    we've sent a code" response either way, so this function's return
    value alone can't be used to enumerate which emails are registered.
    """
    db.purge_expired_token_resets()
    user = db.get_user_by_email(email)
    if not user:
        return None
    email = email.lower().strip()
    _otp_attempts.pop(email, None)   # a fresh code resets any prior lockout
    otp = f"{secrets.randbelow(1_000_000):06d}"
    db.create_token_reset(email, _sha256(otp), ttl_sec=OTP_TTL_SEC)
    return otp


def verify_reset_otp(email: str, otp: str):
    """
    Stage 2. Checks the OTP against what stage 1 stored. On success,
    consumes it and issues a fresh high-entropy "verified" token (stored
    the same way, different purpose) that stage 3 needs -- this means the
    user doesn't have to keep the OTP around while they decide on a new
    access token, and it can't be replayed once used.
    Returns the raw verified-token string on success, None otherwise
    (wrong code, expired, already used, or too many recent failed
    attempts on this email).
    """
    email = email.lower().strip()
    fail_count, first_ts = _otp_attempts.get(email, (0, time.time()))
    if fail_count >= OTP_MAX_ATTEMPTS and time.time() - first_ts < OTP_TTL_SEC:
        return None   # locked out until the current code's own TTL expires
    req = db.get_valid_token_reset(_sha256(otp))
    if not req or req["email"].lower() != email:
        _otp_attempts[email] = (fail_count + 1, first_ts if fail_count else time.time())
        return None
    _otp_attempts.pop(email, None)
    db.mark_token_reset_used(req["id"])
    verified_token = secrets.token_urlsafe(32)
    db.create_token_reset(email, _sha256(verified_token), ttl_sec=VERIFIED_TTL_SEC)
    return verified_token


def complete_token_reset(email: str, verified_token: str, new_raw_token: str):
    """
    Stage 3. Given the verified-token stage 2 issued, sets the account's
    access token to exactly new_raw_token -- whatever the user typed, or
    whatever the frontend's "Generate" button pre-filled client-side
    (this function doesn't distinguish the two; generation is a frontend
    convenience, not a separate backend path). Returns True on success,
    False if the verified-token is invalid/expired/already used, the
    account no longer exists, or new_raw_token is too short.
    """
    email = email.lower().strip()
    req = db.get_valid_token_reset(_sha256(verified_token))
    if not req or req["email"].lower() != email:
        return False
    user = db.get_user_by_email(req["email"])
    if not user:
        return False   # account was deleted between verification and this step
    if len(new_raw_token or "") < 8:
        return False
    # Preserve the user's existing role exactly -- add_user() is an
    # upsert keyed on email, so this updates token_hash in place without
    # touching anything else about the account.
    add_user(req["email"], user["role"], new_raw_token)
    db.mark_token_reset_used(req["id"])
    # Any existing session(s) for this user were issued against the OLD
    # token; a compromised-token scenario is exactly what this flow
    # exists to recover from, so invalidate them rather than leaving a
    # stale session valid until its own 1-hour idle timeout.
    with _lock:
        stale = [s for s, v in _sessions.items() if v["email"] == req["email"].lower()]
        for s in stale:
            del _sessions[s]
    return True
