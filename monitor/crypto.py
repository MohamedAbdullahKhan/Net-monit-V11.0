# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah. All rights reserved.
# Contact: abuabdullah.be@outlook.com
# Unauthorised copying, modification or distribution of this software
# is strictly prohibited without prior written permission from the author.
# =============================================================================
"""
crypto.py -- Net-monit V11.0
Encrypts/decrypts device credentials stored in config.yaml.
Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
Key derived from a per-installation secret stored in data/secret.key.
If the key file does not exist it is generated on first run.
Encrypted values are stored as strings prefixed with "ENC:".
"""
import os
import base64
import logging
from pathlib import Path

log = logging.getLogger("monitor.crypto")
_warned_undecryptable = set()  # device-agnostic value cache, see decrypt() below

_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "secret.key"
_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None  # cryptography not installed — fall back to plaintext

    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        _KEY_PATH.chmod(0o600)

    _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns 'ENC:<base64>' or original if crypto unavailable."""
    if not plaintext:
        return plaintext
    if plaintext.startswith("ENC:"):
        return plaintext  # already encrypted
    f = _get_fernet()
    if f is None:
        return plaintext  # cryptography not installed
    token = f.encrypt(plaintext.encode())
    return "ENC:" + base64.urlsafe_b64encode(token).decode()


def decrypt(value: str) -> str:
    """Decrypt an 'ENC:...' string. Returns original if not encrypted."""
    if not value or not value.startswith("ENC:"):
        return value
    f = _get_fernet()
    if f is None:
        return value
    try:
        raw = base64.urlsafe_b64decode(value[4:])
        return f.decrypt(raw).decode()
    except Exception:
        # This is the exact symptom of copying config.yaml from a
        # DIFFERENT Net-monit installation: every install generates its
        # own unique data/secret.key on first run, so a credential
        # encrypted on one machine can mathematically never be decrypted
        # on another. Previously this failed completely silently -- the
        # device would just fail to authenticate with no indication why.
        # Warn once (not on every single poll) so it's actually
        # diagnosable instead of a mystery connection failure.
        fingerprint = value[:24]
        if fingerprint not in _warned_undecryptable:
            _warned_undecryptable.add(fingerprint)
            log.warning(
                "Could not decrypt a stored credential -- this usually means "
                "config.yaml (or just this device's entry) was copied in from "
                "a DIFFERENT Net-monit installation. Each install has its own "
                "unique encryption key, so credentials encrypted elsewhere can "
                "never be decrypted here. Fix: re-enter this device's "
                "password/credentials on this installation via the UI."
            )
        return value  # return as-is if decryption fails


def is_encrypted(value: str) -> bool:
    return bool(value and value.startswith("ENC:"))


# Credential fields to encrypt/decrypt per device method
CRED_FIELDS = {
    "powershell": [("powershell", "password")],
    "ssh":        [("ssh", "password")],
    "disk_usage": [("disk", "password")],
    "snmp":       [],
    "ping":       [],
}

# V8.2: services_watch / tasks_watch reuse the ssh/powershell credential
# blocks regardless of the device's PRIMARY method (e.g. a ping-monitored
# device can still watch a service over SSH). These are always checked in
# addition to whatever CRED_FIELDS[method] already covers, so a password
# used only for service/task watching never accidentally stays in
# plaintext just because it isn't gating the primary check.
_ALWAYS_CHECK_SECTIONS = [("ssh", "password"), ("powershell", "password")]


def _cred_fields_for(method: str) -> list:
    fields = list(CRED_FIELDS.get(method, []))
    for sec_field in _ALWAYS_CHECK_SECTIONS:
        if sec_field not in fields:
            fields.append(sec_field)
    return fields


def encrypt_device_creds(device: dict) -> dict:
    """Encrypt credential fields before saving to config.yaml."""
    d = dict(device)
    method = d.get("method", "")
    for section, field in _cred_fields_for(method):
        if section in d and isinstance(d[section], dict):
            sec = dict(d[section])
            if sec.get(field):
                sec[field] = encrypt(sec[field])
            d[section] = sec
    return d


def decrypt_device_creds(device: dict) -> dict:
    """Decrypt credential fields when loading from config.yaml for use by checkers."""
    d = dict(device)
    method = d.get("method", "")
    for section, field in _cred_fields_for(method):
        if section in d and isinstance(d[section], dict):
            sec = dict(d[section])
            if sec.get(field):
                sec[field] = decrypt(sec[field])
            d[section] = sec
    return d
