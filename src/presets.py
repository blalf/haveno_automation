"""
Preset management — save/load/edit offer configurations.
Each preset describes one offer type (payment method + currency + margin + description).
Stored as a simple JSON file: presets.json
"""

import json
import os
import uuid
from dataclasses import dataclass, asdict, field
from typing import List, Optional


PRESETS_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "presets.json")


@dataclass
class Preset:
    """One offer configuration template."""
    id: str                        # unique identifier
    name: str                      # display name, e.g. "USD – Wise"
    payment_account_id: str        # Haveno payment account ID
    payment_account_name: str      # human-readable label (for display only)
    currency_code: str             # e.g. "USD", "EUR", "BTC"
    market_price_margin_pct: float # e.g. 15.0 = 15% above market
    min_xmr: float                 # minimum XMR per trade
    description_template: str      # use {password} placeholder for auto-generated password
    security_deposit_pct: float = 0.10
    buyer_as_taker_without_deposit: bool = False
    enabled: bool = True           # whether to include in bulk publish
    group: str = ""                # group/folder name for organizing presets
    auto_chat_enabled: bool = False  # send auto messages when trade starts
    auto_chat_greeting: str = ""     # first message sent when trade begins
    auto_chat_messages: str = ""     # additional messages (one per line, sent in order)

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())[:8]


def load_presets() -> List[Preset]:
    """Load presets from disk. Returns empty list if file doesn't exist."""
    os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
    if not os.path.exists(PRESETS_FILE):
        return []
    with open(PRESETS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    presets = []
    for p in data:
        p.setdefault("group", "")  # backward compat
        p.setdefault("auto_chat_enabled", False)
        p.setdefault("auto_chat_greeting", "")
        p.setdefault("auto_chat_messages", "")
        presets.append(Preset(**p))
    return presets


def save_presets(presets: List[Preset]):
    """Persist presets to disk."""
    os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in presets], f, indent=2, ensure_ascii=False)


def add_preset(presets: List[Preset], preset: Preset) -> List[Preset]:
    presets.append(preset)
    save_presets(presets)
    return presets


def update_preset(presets: List[Preset], updated: Preset) -> List[Preset]:
    for i, p in enumerate(presets):
        if p.id == updated.id:
            presets[i] = updated
            break
    save_presets(presets)
    return presets


def delete_preset(presets: List[Preset], preset_id: str) -> List[Preset]:
    presets = [p for p in presets if p.id != preset_id]
    save_presets(presets)
    return presets


def get_enabled_presets(presets: List[Preset]) -> List[Preset]:
    return [p for p in presets if p.enabled]


def get_groups(presets: List[Preset]) -> List[str]:
    """Return ordered list of unique group names (ungrouped = '' first)."""
    seen = []
    for p in presets:
        if p.group not in seen:
            seen.append(p.group)
    return seen
