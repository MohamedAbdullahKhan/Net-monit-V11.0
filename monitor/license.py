# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah | Abdullah-InfoXtek.com
# Contact: abuabdullah.be@outlook.com
# =============================================================================
"""
license.py  --  Net-monit V11.0 Licensing Engine (asymmetric signatures)

V11.0 change: licence keys are ED25519 SIGNATURES. This file contains only the PUBLIC key, so it can
CHECK a key but can never CREATE one. Before V11.0 a shared HMAC secret lived in this file and in the
generator, so anyone who could read this file could issue themselves a key. That secret no longer exists
anywhere in the product. The PRIVATE key exists only in the owner-only License Key Generator.

Key format:   NM1-XXXXX-XXXXX-...-XXX   (127 characters with dashes, 106 without: "NM1" + 103 Crockford-base32 characters, in
              groups of five; case, dashes, spaces and look-alike letters I/L/O are forgiven on entry)
Signed bytes: b"NETMONIT-LICENSE-V1|" + UPPER(device_id) + b"|" + UPPER(activation_code)
Signature:    Ed25519 over those bytes (64 bytes) -> base32 as above.

Keys from V10.9 and earlier (XXXXX-XXXXX, HMAC) are NOT accepted: they can be forged by anyone who has
ever seen the old secret. Such an install shows "key from an older version -- request a new key".

Trial: 30 days. After expiry without a valid key: restricted mode.
"""

import hashlib, os, platform, socket, time, uuid, json, logging, re, base64

log = logging.getLogger("netmonit.license")

# ============================================================
# PUBLIC verification key (raw 32-byte Ed25519 key, base64). Safe to publish; it cannot sign.
# Replace it with `LicenseKeyGenerator.py --rotate-keys --app-dir <this app>` (never edit by hand).
# ============================================================
_PUBLIC_KEY_B64 = "BFbxVQJa7uh/ynI9vCmqqm8zzgCvcrCyU48eJohwHN8="  # LICENSE-PUBLIC-KEY

_MSG_PREFIX = b"NETMONIT-LICENSE-V1|"
_KEY_PREFIX = "NM1"
_CROCKFORD  = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_KEY_CHARS  = 103                       # ceil(512 bits / 5)

STATE_VALID   = "valid"
STATE_TRIAL   = "trial"
STATE_EXPIRED = "expired"
STATE_INVALID = "invalid"


# ============================================================
# DEVICE FINGERPRINTING
# ============================================================

def _safe(fn, default=""):
    try:
        return str(fn())
    except Exception:
        return default


def _get_mac():
    mac_int = uuid.getnode()
    if (mac_int >> 40) & 1:
        return "000000000000"
    return "%012x" % mac_int


def _get_cpu_id():
    if platform.system() == "Windows":
        try:
            import subprocess
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "ProcessorId"], timeout=4
            ).decode(errors="ignore")
            for line in out.splitlines():
                line = line.strip()
                if line and line != "ProcessorId":
                    return line
        except Exception:
            pass
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "Serial" in line or "Hardware" in line:
                        return line.split(":")[-1].strip()
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[-1].strip()
        except Exception:
            pass
    return platform.processor() or "unknown-cpu"


def get_device_id() -> str:
    """16-char uppercase hex. Stable: hostname + MAC + OS + CPU."""
    parts = [
        _safe(socket.gethostname),
        _get_mac(),
        platform.system(),
        platform.machine(),
        _get_cpu_id(),
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()[:16]


def get_activation_code() -> str:
    """16-char uppercase hex. Stable: Python version + install path + node UUID."""
    install_path = _safe(lambda: os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    ))
    parts = [
        platform.python_version(),
        install_path,
        str(uuid.UUID(int=uuid.getnode())),
        platform.node(),
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()[:16]


# ============================================================
# KEY CHECK  (verification only -- there is deliberately no signing code in this file)
# ============================================================

def _normalise_key_text(key: str) -> str:
    """Upper-case, drop everything that is not a letter/digit, forgive Crockford look-alikes."""
    k = re.sub(r"[^0-9A-Za-z]", "", str(key or "")).upper()
    return k.replace("I", "1").replace("L", "1").replace("O", "0")


def is_legacy_key_format(key: str) -> bool:
    """True for the pre-V11.0 'XXXXX-XXXXX' keys (10 characters, no NM1 prefix)."""
    return bool(re.fullmatch(r"[0-9A-Za-z]{5}-?[0-9A-Za-z]{5}", str(key or "").strip()))


def parse_license_key(key: str):
    """key text -> the 64 signature bytes, or None if it is not a well-formed V11 key."""
    k = _normalise_key_text(key)
    if not k.startswith(_KEY_PREFIX):
        return None
    body = k[len(_KEY_PREFIX):]
    if len(body) != _KEY_CHARS or any(c not in _CROCKFORD for c in body):
        return None
    n = 0
    for c in body:
        n = n * 32 + _CROCKFORD.index(c)
    if n >= 1 << 512:                      # canonical form only: the 3 spare top bits must be zero
        return None
    return n.to_bytes(64, "big")


def _public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUBLIC_KEY_B64))


def _signed_message(device_id: str, activation_code: str) -> bytes:
    return _MSG_PREFIX + device_id.strip().upper().encode("utf-8") + b"|" + activation_code.strip().upper().encode("utf-8")


def validate_license_key(key: str, device_id: str, activation_code: str) -> bool:
    sig = parse_license_key(key)
    if sig is None:
        return False
    try:
        _public_key().verify(sig, _signed_message(device_id, activation_code))
        return True
    except Exception:                      # InvalidSignature, or a bad/missing public key: never "valid"
        return False


# ============================================================
# LICENSE MANAGER
# ============================================================

TRIAL_DAYS = 30


class LicenseManager:
    def __init__(self):
        self._state           = None
        self._record          = None
        self._device_id       = None
        self._activation_code = None

    def _load(self):
        from . import database as db
        self._device_id       = get_device_id()
        self._activation_code = get_activation_code()
        self._record          = db.get_license_record()

        if self._record is None:
            db.set_license_record(
                device_id       = self._device_id,
                activation_code = self._activation_code,
                license_key     = "",
                trial_start     = time.time(),
                activated       = 0,
            )
            self._record = db.get_license_record()

        self._state = self._compute_state()
        log.info("License state: %s  device_id=%s", self._state, self._device_id)
        return self._state

    def _compute_state(self) -> str:
        r = self._record
        if r.get("activated"):
            if validate_license_key(
                r.get("license_key", ""),
                self._device_id,
                self._activation_code,
            ):
                return STATE_VALID
            return STATE_INVALID
        trial_start  = r.get("trial_start", time.time())
        elapsed_days = (time.time() - trial_start) / 86400
        return STATE_TRIAL if elapsed_days <= TRIAL_DAYS else STATE_EXPIRED

    def refresh(self):
        self._load()

    @property
    def state(self) -> str:
        if self._state is None:
            self._load()
        return self._state

    @property
    def device_id(self) -> str:
        if self._device_id is None:
            self._load()
        return self._device_id

    @property
    def activation_code(self) -> str:
        if self._activation_code is None:
            self._load()
        return self._activation_code

    @property
    def trial_days_remaining(self) -> int:
        if self._record is None:
            self._load()
        ts      = self._record.get("trial_start", time.time())
        elapsed = (time.time() - ts) / 86400
        return max(0, int(TRIAL_DAYS - elapsed))

    @property
    def trial_start_date(self) -> str:
        if self._record is None:
            self._load()
        import datetime
        return datetime.datetime.fromtimestamp(
            self._record.get("trial_start", time.time())
        ).strftime("%Y-%m-%d")

    @property
    def is_full_access(self) -> bool:
        return self.state in (STATE_VALID, STATE_TRIAL)

    @property
    def is_restricted(self) -> bool:
        return self.state in (STATE_EXPIRED, STATE_INVALID)

    def activate(self, license_key: str) -> dict:
        from . import database as db
        self._device_id       = get_device_id()
        self._activation_code = get_activation_code()
        if validate_license_key(license_key, self._device_id, self._activation_code):
            db.set_license_activated(_normalise_key_text(license_key))
            self._record = db.get_license_record()
            self._state  = STATE_VALID
            log.info("License activated for device %s", self._device_id)
            return {"ok": True, "message": "License activated successfully. Full access granted."}
        log.warning("Invalid key attempt (%d characters entered)", len(str(license_key or "")))
        if is_legacy_key_format(license_key):
            return {"ok": False, "legacy": True, "message":
                    "This is an old-format key (XXXXX-XXXXX) from before V11.0. Those keys are no longer accepted. "
                    "Ask your vendor for a new V11.0 key: send them the Device ID and Activation Code shown on this page."}
        return {"ok": False, "message":
                "Invalid license key. Check that you pasted the WHOLE key (it starts with NM1- and is about 127 characters with dashes) "
                "and that it was made for THIS Device ID and Activation Code."}

    def get_status_dict(self) -> dict:
        return {
            "state":                self.state,
            "device_id":            self.device_id,
            "activation_code":      self.activation_code,
            "trial_days_remaining": self.trial_days_remaining,
            "trial_start":          self.trial_start_date,
            "is_full_access":       self.is_full_access,
            "is_restricted":        self.is_restricted,
            "trial_total_days":     TRIAL_DAYS,
            "key_legacy":           bool(self._record and self._record.get("activated")
                                         and is_legacy_key_format(self._record.get("license_key", ""))),
        }


license_manager = LicenseManager()
