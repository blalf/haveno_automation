"""
App configuration (daemon host, port, password).
Stored in config/app_config.json — never hardcoded.
"""

import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "app_config.json")

DEFAULT_CONFIG = {
    "host": "localhost",
    # Retoswap mainnet user1 daemon listens on 1202 (per haveno-reto's
    # Makefile). Upstream stagenet uses 3201/3202 — change in the
    # Connection tab if you're testing against a stagenet build.
    "port": 1202,
    "password": "apitest",
    "account_password": "",
    "word_count": 8,
    "default_security_deposit_pct": 0.10,
    "default_description_template": (
        "PASSWORD -- {password}\n"
        "INSTANT TRADE -- start it and I'll come in 5-10 mins max "
        "(for more than 0.5 $XMR, message me first)"
    ),
}


def load_config() -> dict:
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Fill in any missing keys from defaults
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
