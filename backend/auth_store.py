"""
App login for Kuma FlowMap.

Copies Uptime Kuma's model: there is NO shipped default password. On first run the
page asks the operator to create their own admin account (username + password). The
password is stored hashed (PBKDF2-HMAC-SHA256, stdlib — no extra dependency) in
/app/data/auth.json on the writable volume. The plaintext password is never stored.

Sessions are stateless signed cookies: we encrypt {username, password-fingerprint}
with the same Fernet key config_store uses (which also gives us a built-in expiry via
Fernet's TTL). Changing the password changes the fingerprint, which instantly
invalidates every previously issued cookie.
"""

import base64
import hashlib
import hmac
import json
import os
import pathlib
import threading
import time

import config_store  # reuse the Fernet key + data dir

DATA_DIR = config_store.DATA_DIR
AUTH_FILE = DATA_DIR / "auth.json"

SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
_PBKDF2_ITERS = 200_000
_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# Password hashing (stdlib PBKDF2)
# --------------------------------------------------------------------------- #
def hash_password(pw: str, iterations: int = _PBKDF2_ITERS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode("ascii"), base64.b64encode(dk).decode("ascii")
    )


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Account storage
# --------------------------------------------------------------------------- #
def _read() -> dict:
    if not AUTH_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write(data: dict) -> None:
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except Exception:  # noqa: BLE001
        pass
    tmp.replace(AUTH_FILE)


def has_account() -> bool:
    with _lock:
        acct = _read()
        return bool(acct.get("username") and acct.get("pw_hash"))


def get_account():
    with _lock:
        acct = _read()
        if acct.get("username") and acct.get("pw_hash"):
            return acct
    return None


def create_account(username: str, password: str):
    with _lock:
        if has_account():
            return None
        acct = {
            "username": username.strip(),
            "pw_hash": hash_password(password),
            "created_at": time.time(),
        }
        _write(acct)
        return acct


def verify_login(username: str, password: str):
    acct = get_account()
    if not acct:
        return None
    if not hmac.compare_digest((username or "").strip(), acct["username"]):
        return None
    if not verify_password(password or "", acct["pw_hash"]):
        return None
    return acct


def change_password(username: str, current: str, new: str) -> bool:
    with _lock:
        acct = _read()
        if not (acct.get("username") and acct.get("pw_hash")):
            return False
        if username != acct["username"] or not verify_password(current or "", acct["pw_hash"]):
            return False
        acct["pw_hash"] = hash_password(new)
        _write(acct)
        return True


# --------------------------------------------------------------------------- #
# Stateless session tokens (Fernet-encrypted, self-expiring)
# --------------------------------------------------------------------------- #
def _fingerprint(acct: dict) -> str:
    # a slice of the password hash — changes whenever the password changes,
    # so old cookies stop validating the moment the password is updated.
    return hashlib.sha256(acct["pw_hash"].encode("utf-8")).hexdigest()[:16]


def make_token(acct: dict) -> str:
    payload = json.dumps({"u": acct["username"], "v": _fingerprint(acct)}).encode("utf-8")
    return config_store._fernet().encrypt(payload).decode("ascii")


def read_token(token: str):
    """Return the username if the cookie is valid + unexpired + matches the current
    password fingerprint, else None."""
    if not token:
        return None
    try:
        raw = config_store._fernet().decrypt(token.encode("ascii"), ttl=SESSION_TTL)
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 - expired, tampered, or key rotated
        return None
    acct = get_account()
    if not acct:
        return None
    if data.get("u") != acct["username"] or data.get("v") != _fingerprint(acct):
        return None
    return acct["username"]
