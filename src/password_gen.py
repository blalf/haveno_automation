"""
Secure random password generator.
Generates random word-phrases like "tennis nest topple library rally repair deposit endless"
using a BIP39 wordlist — same style the client already uses manually.
"""

import random
import os
import urllib.request


# BIP39 English wordlist (2048 words)
BIP39_URL = "https://raw.githubusercontent.com/trezor/python-mnemonic/master/src/mnemonic/wordlist/english.txt"
WORDLIST_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "wordlist.txt")

_WORDLIST: list[str] = []


def _load_wordlist() -> list[str]:
    """Load BIP39 wordlist from disk (downloads once if missing)."""
    global _WORDLIST
    if _WORDLIST:
        return _WORDLIST

    os.makedirs(os.path.dirname(WORDLIST_FILE), exist_ok=True)

    if not os.path.exists(WORDLIST_FILE):
        print("[password_gen] Downloading BIP39 wordlist...")
        urllib.request.urlretrieve(BIP39_URL, WORDLIST_FILE)

    with open(WORDLIST_FILE, "r", encoding="utf-8") as f:
        _WORDLIST = [line.strip() for line in f if line.strip()]

    return _WORDLIST


def generate_password(word_count: int = 8) -> str:
    """
    Generate a random passphrase of `word_count` BIP39 words.
    Default 8 words matches the client's existing password style.

    Example: "tennis nest topple library rally repair deposit endless"
    """
    wordlist = _load_wordlist()
    words = random.SystemRandom().choices(wordlist, k=word_count)
    return " ".join(words)


def inject_password(template: str, password: str) -> str:
    """
    Replace {password} placeholder in description template.

    Example:
        template = "PASSWORD -- {password}\nINSTANT TRADE -- start it..."
        result   = "PASSWORD -- tennis nest topple...\nINSTANT TRADE -- start it..."
    """
    return template.replace("{password}", password)


if __name__ == "__main__":
    # Quick test
    for _ in range(3):
        pw = generate_password()
        print(pw)
