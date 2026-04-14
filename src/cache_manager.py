"""
Persistent cache for the Haveno Offer Automator.

The app is designed to be "offline-first": presets, settings, UI all work
without the daemon. The daemon is only required to actually PUBLISH or
modify offers. To support that model, we persist the most recent state
snapshot (active offers, recent passwords, payment accounts) to disk so
the user can:

  * see their last-known active offers even after the daemon is killed
    or the network drops;
  * copy generated passwords from a previous session (critical for
    no-deposit offers — the password is only shown once by the daemon);
  * edit and create presets offline (we need the list of payment
    accounts to pick from, so we snapshot it on every successful
    connect and reuse the snapshot when offline).

Stored as simple JSON in config/cache.json. Best-effort: any read/write
error is swallowed and treated as "no cache yet" so the app never dies
because of a corrupt cache file.
"""

import json
import os
import time
from typing import Optional

CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "cache.json")

_DEFAULT: dict = {
    "offers": [],              # last-known active offers (dicts from HavenoClient)
    "offers_updated_at": 0.0,  # unix ts of last successful fetch
    "passwords": [],           # [{preset_name, password, injected, ts}]
    "payment_accounts": [],    # last-known payment accounts
    "accounts_updated_at": 0.0,
}


def _read() -> dict:
    if not os.path.exists(CACHE_FILE):
        return _DEFAULT.copy()
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Fill in any missing keys so callers can rely on them
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return _DEFAULT.copy()


def _write(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        # Cache is best-effort — never crash the app over a write error.
        pass


# ── Offers snapshot ────────────────────────────────────────────────────

def get_cached_offers() -> list:
    return list(_read().get("offers", []))


def get_offers_updated_at() -> float:
    return float(_read().get("offers_updated_at", 0.0))


def save_offers(offers: list) -> None:
    data = _read()
    data["offers"] = list(offers or [])
    data["offers_updated_at"] = time.time()
    _write(data)


# ── Payment accounts snapshot ──────────────────────────────────────────

def get_cached_payment_accounts() -> list:
    return list(_read().get("payment_accounts", []))


def save_payment_accounts(accounts: list) -> None:
    data = _read()
    data["payment_accounts"] = list(accounts or [])
    data["accounts_updated_at"] = time.time()
    _write(data)


# ── Password history ───────────────────────────────────────────────────
#
# Each entry: {"preset_name": str, "password": str, "injected": bool,
#              "ts": float (unix), "offer_id": str (optional)}
#
# Entries are append-only. The UI can clear them with clear_passwords().

_PW_MAX = 200  # hard cap so the cache file can't grow unbounded


def get_passwords() -> list:
    return list(_read().get("passwords", []))


def add_password(
    preset_name: str,
    password: str,
    injected: bool,
    offer_id: Optional[str] = None,
) -> None:
    data = _read()
    entries = list(data.get("passwords", []))
    entries.append({
        "preset_name": preset_name,
        "password": password,
        "injected": bool(injected),
        "offer_id": offer_id or "",
        "ts": time.time(),
    })
    # Trim oldest if we grow past the cap.
    if len(entries) > _PW_MAX:
        entries = entries[-_PW_MAX:]
    data["passwords"] = entries
    _write(data)


def clear_passwords() -> None:
    data = _read()
    data["passwords"] = []
    _write(data)


def remove_password(index: int) -> None:
    """Remove a single password entry by list index (matches get_passwords())."""
    data = _read()
    entries = list(data.get("passwords", []))
    if 0 <= index < len(entries):
        entries.pop(index)
        data["passwords"] = entries
        _write(data)
