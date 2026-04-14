"""
Launch commands the user keeps handy in the Connection tab.

These are plain shell one-liners (seednode, daemon, desktop, anything
else) that the user copy-pastes into a terminal. The app does NOT run
them itself — we only store and expose them with a one-click copy
button. Keeping them out-of-process is deliberate: each Haveno
instance needs its own terminal so the user can see its logs, and
running them as child processes would hide those logs and complicate
shutdown.

Stored as JSON in config/commands.json. On first launch we seed the
file with a sensible default list pulled from the existing
tuto_lancer_haveno.txt notes so the user has something to copy
immediately.
"""

import json
import os
import uuid
from dataclasses import dataclass, asdict
from typing import List

COMMANDS_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "commands.json")


@dataclass
class LaunchCommand:
    id: str
    name: str          # short label shown in the UI (e.g. "User1 daemon")
    command: str       # the actual shell command to copy
    category: str = "" # free-form tag, e.g. "Haveno", "Custom"

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())[:8]


# Default commands seeded on first launch.
#
# Just the two commands the client uses in production:
#   * Desktop = the exact .deb command line the client runs.
#   * Daemon  = same command line with --apiPort / --apiPassword added
#               so the automation app can connect to it.
#
# The client edits/deletes them from the Connection tab if he needs to
# (different apiPort, extra flags, wrap in bwrap, etc.).
_DEFAULTS = [
    {
        "name": "Haveno desktop",
        "command": (
            "/opt/haveno/bin/Haveno "
            "--torControlPort=9051 "
            "--torControlCookieFile=/run/tor/control.authcookie "
            "--torControlUseSafeCookieAuth "
            "--socks5ProxyXmrAddress=127.0.0.1:9050 "
            "--torStreamIsolation "
            "--useTorForXmr=on "
            "--disableRateLimits=true"
        ),
        "category": "",
    },
    {
        "name": "Haveno daemon (API on port 1202)",
        "command": (
            "/opt/haveno/bin/Haveno "
            "--torControlPort=9051 "
            "--torControlCookieFile=/run/tor/control.authcookie "
            "--torControlUseSafeCookieAuth "
            "--socks5ProxyXmrAddress=127.0.0.1:9050 "
            "--torStreamIsolation "
            "--useTorForXmr=on "
            "--disableRateLimits=true "
            "--apiPort=1202 "
            "--apiPassword=apitest"
        ),
        "category": "",
    },
]


def load_commands() -> List[LaunchCommand]:
    os.makedirs(os.path.dirname(COMMANDS_FILE), exist_ok=True)
    if not os.path.exists(COMMANDS_FILE):
        # Seed with defaults on first launch.
        seeded = [LaunchCommand(id=LaunchCommand.new_id(), **d) for d in _DEFAULTS]
        save_commands(seeded)
        return seeded
    try:
        with open(COMMANDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out: List[LaunchCommand] = []
    for c in data:
        # Backward-compat defaults for missing fields.
        c.setdefault("category", "")
        c.setdefault("id", LaunchCommand.new_id())
        try:
            out.append(LaunchCommand(**c))
        except TypeError:
            # Ignore entries with unexpected fields instead of crashing.
            continue
    return out


def save_commands(commands: List[LaunchCommand]) -> None:
    os.makedirs(os.path.dirname(COMMANDS_FILE), exist_ok=True)
    with open(COMMANDS_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in commands], f, indent=2, ensure_ascii=False)


def add_command(commands: List[LaunchCommand], cmd: LaunchCommand) -> List[LaunchCommand]:
    commands.append(cmd)
    save_commands(commands)
    return commands


def update_command(commands: List[LaunchCommand], updated: LaunchCommand) -> List[LaunchCommand]:
    for i, c in enumerate(commands):
        if c.id == updated.id:
            commands[i] = updated
            break
    save_commands(commands)
    return commands


def delete_command(commands: List[LaunchCommand], cmd_id: str) -> List[LaunchCommand]:
    commands = [c for c in commands if c.id != cmd_id]
    save_commands(commands)
    return commands
