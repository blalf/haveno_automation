"""
Haveno Offer Automator — Desktop UI
Built with CustomTkinter for a modern cross-platform look.
"""

import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

import customtkinter as ctk
from tkinter import messagebox, StringVar, BooleanVar

from haveno_client import HavenoClient
from presets import (
    Preset, load_presets, save_presets,
    add_preset, update_preset, delete_preset, get_enabled_presets, get_groups
)
from publisher import publish_all, cancel_all_my_offers
from config_manager import load_config, save_config
from password_gen import generate_password
import cache_manager
from commands_manager import (
    LaunchCommand, load_commands, save_commands,
    add_command, update_command, delete_command,
)

# ── Theme ───────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ORANGE  = "#FF8C00"
GREEN   = "#2ECC71"
RED     = "#E74C3C"
GREY    = "#2B2B2B"
LIGHT   = "#3A3A3A"


# ═══════════════════════════════════════════════════════════════════════════
#  PresetDialog — modal for creating / editing a preset
# ═══════════════════════════════════════════════════════════════════════════

class PresetDialog(ctk.CTkToplevel):
    def __init__(self, master, payment_accounts: list, preset: Preset = None,
                 default_template: str = "", on_save=None):
        super().__init__(master)
        self.title("Edit Preset" if preset else "New Preset")
        self.geometry("620x900")
        self.resizable(True, True)
        self.minsize(500, 600)
        self.after(200, self._safe_grab)

        self.payment_accounts = payment_accounts
        self.existing = preset
        self.on_save = on_save

        cfg = load_config()
        default_tmpl = default_template or cfg.get("default_description_template", "")

        # ── Scrollable form area ─────────────────────────────────────────
        form_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        form_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # Fix mouse wheel scrolling on Linux (CTkScrollableFrame bug)
        def _on_mousewheel(event):
            try:
                canvas = form_scroll._parent_canvas
                if event.num == 4:
                    canvas.yview_scroll(-3, "units")
                elif event.num == 5:
                    canvas.yview_scroll(3, "units")
                elif event.delta:
                    canvas.yview_scroll(int(-event.delta / 120), "units")
            except Exception:
                pass

        def _bind_scroll(widget):
            widget.bind("<Button-4>", _on_mousewheel, add="+")
            widget.bind("<Button-5>", _on_mousewheel, add="+")
            widget.bind("<MouseWheel>", _on_mousewheel, add="+")
            for child in widget.winfo_children():
                _bind_scroll(child)

        # Bind after all widgets are added (deferred)
        self.after(500, lambda: _bind_scroll(form_scroll))

        pad = {"padx": 16, "pady": 5}

        ctk.CTkLabel(form_scroll, text="Preset name", anchor="w").pack(fill="x", **pad)
        self.name_var = StringVar(value=preset.name if preset else "")
        ctk.CTkEntry(form_scroll, textvariable=self.name_var).pack(fill="x", **pad)

        ctk.CTkLabel(form_scroll, text="Payment account", anchor="w").pack(fill="x", **pad)
        acc_names = [f"{a['account_name']}  ({a['currency_code']})"
                     for a in payment_accounts]
        self.acc_var = StringVar()
        if preset and payment_accounts:
            match = next((a for a in payment_accounts
                          if a["id"] == preset.payment_account_id), None)
            if match:
                self.acc_var.set(f"{match['account_name']}  ({match['currency_code']})")
        self.acc_menu = ctk.CTkOptionMenu(
            form_scroll, values=acc_names or ["(no accounts loaded)"],
            variable=self.acc_var, command=self._on_account_changed
        )
        self.acc_menu.pack(fill="x", **pad)

        ctk.CTkLabel(form_scroll, text="Buyer's currency (from account)", anchor="w").pack(fill="x", **pad)
        self.currency_var = StringVar(value=preset.currency_code if preset else "")
        self.currency_entry = ctk.CTkEntry(
            form_scroll, textvariable=self.currency_var, state="disabled",
            text_color="white"
        )
        self.currency_entry.pack(fill="x", **pad)

        ctk.CTkLabel(form_scroll, text="Market price margin %  (e.g. 15 = +15%)", anchor="w").pack(fill="x", **pad)
        self.margin_var = StringVar(value=str(preset.market_price_margin_pct) if preset else "15.0")
        ctk.CTkEntry(form_scroll, textvariable=self.margin_var).pack(fill="x", **pad)

        ctk.CTkLabel(form_scroll, text="Minimum XMR per trade  (e.g. 0.2)", anchor="w").pack(fill="x", **pad)
        self.min_xmr_var = StringVar(value=str(preset.min_xmr) if preset else "0.2")
        ctk.CTkEntry(form_scroll, textvariable=self.min_xmr_var).pack(fill="x", **pad)

        # No deposit checkbox — BEFORE deposit field so it can control it
        self.no_deposit_var = BooleanVar(
            value=preset.buyer_as_taker_without_deposit if preset else False
        )
        ctk.CTkCheckBox(
            form_scroll, text="No deposit required from buyer (passphrase protected)",
            variable=self.no_deposit_var, command=self._on_no_deposit_toggled
        ).pack(anchor="w", **pad)

        self.deposit_label = ctk.CTkLabel(form_scroll, text="Security deposit %  (e.g. 0.10 = 10%)", anchor="w")
        self.deposit_label.pack(fill="x", **pad)
        self.deposit_var = StringVar(
            value=str(preset.security_deposit_pct) if preset else "0.10"
        )
        self.deposit_entry = ctk.CTkEntry(form_scroll, textvariable=self.deposit_var)
        self.deposit_entry.pack(fill="x", **pad)

        # Apply initial state for deposit field
        self._on_no_deposit_toggled()

        ctk.CTkLabel(form_scroll, text="Description template  (use {password} for auto-password)", anchor="w").pack(fill="x", **pad)
        self.tmpl_box = ctk.CTkTextbox(form_scroll, height=100)
        self.tmpl_box.pack(fill="x", **pad)
        self.tmpl_box.insert("1.0", preset.description_template if preset else default_tmpl)

        # ── Auto-chat settings ────────────────────────────────────────
        sep = ctk.CTkFrame(form_scroll, height=2, fg_color=ORANGE)
        sep.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(form_scroll, text="Auto-Chat  (messages sent when a trade starts)",
                     font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x", **pad)

        self.auto_chat_var = BooleanVar(
            value=preset.auto_chat_enabled if preset else False
        )
        ctk.CTkCheckBox(
            form_scroll, text="Enable auto-chat for this preset",
            variable=self.auto_chat_var
        ).pack(anchor="w", **pad)

        ctk.CTkLabel(form_scroll, text="Greeting message  (sent first when trade begins)", anchor="w").pack(fill="x", **pad)
        self.greeting_box = ctk.CTkTextbox(form_scroll, height=60)
        self.greeting_box.pack(fill="x", **pad)
        self.greeting_box.insert("1.0", preset.auto_chat_greeting if preset else "")

        ctk.CTkLabel(form_scroll, text="Follow-up messages  (one per line, sent in order)", anchor="w").pack(fill="x", **pad)
        self.chat_msgs_box = ctk.CTkTextbox(form_scroll, height=80)
        self.chat_msgs_box.pack(fill="x", **pad)
        self.chat_msgs_box.insert("1.0", preset.auto_chat_messages if preset else "")

        # ── Group ────────────────────────────────────────────────────
        ctk.CTkLabel(form_scroll, text="Group / folder  (type new or pick existing)", anchor="w").pack(fill="x", **pad)
        existing_groups = [g for g in get_groups(load_presets()) if g]
        self.group_var = StringVar(value=preset.group if preset else "")
        self.group_menu = ctk.CTkComboBox(
            form_scroll, values=existing_groups if existing_groups else [""],
            variable=self.group_var
        )
        self.group_menu.pack(fill="x", **pad)

        # ── Buttons (fixed at the bottom, outside scroll) ────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=12, side="bottom")
        ctk.CTkButton(btn_frame, text="Cancel", fg_color=LIGHT, width=120,
                      height=36, command=self.destroy).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Save", fg_color=ORANGE, width=120,
                      height=36, command=self._save).pack(side="right", padx=4)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            self.after(100, self._safe_grab)

    def _on_account_changed(self, selected_label: str):
        """Auto-fill currency code when a payment account is selected (read-only)."""
        acc = next(
            (a for a in self.payment_accounts
             if f"{a['account_name']}  ({a['currency_code']})" == selected_label),
            None
        )
        if acc and acc.get("currency_code"):
            self.currency_entry.configure(state="normal")
            self.currency_var.set(acc["currency_code"])
            self.currency_entry.configure(state="disabled")

    def _on_no_deposit_toggled(self):
        """Disable deposit field when no-deposit is checked.
        Haveno requires security_deposit_pct >= 0.15 even when
        buyer_as_taker_without_deposit is True (it overrides server-side)."""
        if self.no_deposit_var.get():
            self.deposit_entry.configure(state="normal")
            self.deposit_var.set("0.15")
            self.deposit_entry.configure(state="disabled")
            self.deposit_label.configure(
                text="Security deposit %  (ignored — no deposit mode)",
                text_color="grey"
            )
        else:
            self.deposit_entry.configure(state="normal")
            self.deposit_label.configure(
                text="Security deposit %  (e.g. 0.10 = 10%)",
                text_color=("gray10", "gray90")
            )

    def _save(self):
        name     = self.name_var.get().strip()
        currency = self.currency_var.get().strip().upper()
        template = self.tmpl_box.get("1.0", "end").strip()

        if not name or not currency:
            messagebox.showwarning("Missing fields", "Name and currency are required.", parent=self)
            return

        try:
            margin  = float(self.margin_var.get())
            min_xmr = float(self.min_xmr_var.get())
            deposit = float(self.deposit_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Margin, min XMR and deposit must be numbers.", parent=self)
            return

        selected_label = self.acc_var.get()
        acc = next(
            (a for a in self.payment_accounts
             if f"{a['account_name']}  ({a['currency_code']})" == selected_label),
            None
        )
        acc_id   = acc["id"]   if acc else ""
        acc_name = acc["account_name"] if acc else selected_label

        group = self.group_var.get().strip()
        auto_chat = self.auto_chat_var.get()
        greeting = self.greeting_box.get("1.0", "end").strip()
        chat_msgs = self.chat_msgs_box.get("1.0", "end").strip()

        preset = Preset(
            id=self.existing.id if self.existing else Preset.new_id(),
            name=name,
            payment_account_id=acc_id,
            payment_account_name=acc_name,
            currency_code=currency,
            market_price_margin_pct=margin,
            min_xmr=min_xmr,
            security_deposit_pct=deposit,
            description_template=template,
            buyer_as_taker_without_deposit=self.no_deposit_var.get(),
            enabled=self.existing.enabled if self.existing else True,
            group=group,
            auto_chat_enabled=auto_chat,
            auto_chat_greeting=greeting,
            auto_chat_messages=chat_msgs,
        )

        if self.on_save:
            self.on_save(preset)
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
#  SettingsDialog — configure daemon connection
# ═══════════════════════════════════════════════════════════════════════════

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, on_save=None):
        super().__init__(master)
        self.title("Daemon Settings")
        self.geometry("450x460")
        self.resizable(False, False)
        self.after(200, self._safe_grab)
        self.on_save = on_save

        cfg = load_config()
        pad = {"padx": 20, "pady": 8}

        # ── Form area ────────────────────────────────────────────────────
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(form, text="Daemon host", anchor="w").pack(fill="x", **pad)
        self.host_var = StringVar(value=cfg.get("host", "localhost"))
        ctk.CTkEntry(form, textvariable=self.host_var).pack(fill="x", **pad)

        ctk.CTkLabel(form, text="gRPC port", anchor="w").pack(fill="x", **pad)
        self.port_var = StringVar(value=str(cfg.get("port", 3201)))
        ctk.CTkEntry(form, textvariable=self.port_var).pack(fill="x", **pad)

        ctk.CTkLabel(form, text="API password", anchor="w").pack(fill="x", **pad)
        self.pw_var = StringVar(value=cfg.get("password", ""))
        ctk.CTkEntry(form, textvariable=self.pw_var, show="*").pack(fill="x", **pad)

        ctk.CTkLabel(form, text="Account password  (to unlock wallet in desktop mode)", anchor="w").pack(fill="x", **pad)
        self.acc_pw_var = StringVar(value=cfg.get("account_password", ""))
        ctk.CTkEntry(form, textvariable=self.acc_pw_var, show="*").pack(fill="x", **pad)

        # ── Buttons (fixed at the bottom) ────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=12, side="bottom")
        ctk.CTkButton(btn_frame, text="Cancel", fg_color=LIGHT, width=120,
                      height=36, command=self.destroy).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Save & Reconnect", fg_color=ORANGE,
                      width=160, height=36, command=self._save).pack(side="right", padx=4)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            self.after(100, self._safe_grab)

    def _save(self):
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number.", parent=self)
            return
        cfg = load_config()
        cfg["host"] = self.host_var.get().strip()
        cfg["port"] = port
        cfg["password"] = self.pw_var.get()
        cfg["account_password"] = self.acc_pw_var.get()
        save_config(cfg)
        if self.on_save:
            self.on_save()
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
#  LaunchCommandDialog — add/edit a saved shell command
# ═══════════════════════════════════════════════════════════════════════════

class LaunchCommandDialog(ctk.CTkToplevel):
    def __init__(self, master, existing: LaunchCommand = None, on_save=None):
        super().__init__(master)
        self.title("Edit command" if existing else "New command")
        self.geometry("560x380")
        self.resizable(True, True)
        self.minsize(420, 320)
        self.after(200, self._safe_grab)

        self.existing = existing
        self.on_save = on_save

        pad = {"padx": 16, "pady": 6}

        ctk.CTkLabel(self, text="Name", anchor="w").pack(fill="x", **pad)
        self.name_var = StringVar(value=existing.name if existing else "")
        ctk.CTkEntry(self, textvariable=self.name_var).pack(fill="x", **pad)

        ctk.CTkLabel(self, text="Category  (optional, e.g. Haveno, Custom)",
                     anchor="w").pack(fill="x", **pad)
        self.cat_var = StringVar(value=existing.category if existing else "")
        ctk.CTkEntry(self, textvariable=self.cat_var).pack(fill="x", **pad)

        ctk.CTkLabel(self, text="Command  (copied to clipboard when clicking Copy)",
                     anchor="w").pack(fill="x", **pad)
        self.cmd_box = ctk.CTkTextbox(self, height=140)
        self.cmd_box.pack(fill="both", expand=True, **pad)
        if existing:
            self.cmd_box.insert("1.0", existing.command)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=10, side="bottom")
        ctk.CTkButton(btn_frame, text="Cancel", fg_color=LIGHT, width=120,
                      height=34, command=self.destroy).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Save", fg_color=ORANGE, width=120,
                      height=34, command=self._save).pack(side="right", padx=4)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            self.after(100, self._safe_grab)

    def _save(self):
        name = self.name_var.get().strip()
        cat  = self.cat_var.get().strip()
        cmd  = self.cmd_box.get("1.0", "end").strip()
        if not name or not cmd:
            messagebox.showwarning("Missing fields",
                                   "Name and command are required.", parent=self)
            return
        out = LaunchCommand(
            id=self.existing.id if self.existing else LaunchCommand.new_id(),
            name=name, command=cmd, category=cat,
        )
        if self.on_save:
            self.on_save(out)
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
#  Main App
# ═══════════════════════════════════════════════════════════════════════════

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Haveno Offer Automator")
        self.geometry("1060x750")
        self.minsize(900, 650)

        self.client: HavenoClient = None
        self.presets: list[Preset] = load_presets()
        # Payment accounts come from the daemon, but we snapshot them
        # on every successful connect so presets can still be edited
        # offline. Seed from cache on startup.
        self.payment_accounts: list = cache_manager.get_cached_payment_accounts()
        self._connected = False
        # Seed offers and password history from cache so the user can
        # see their last-known state immediately — no daemon required.
        self._active_offers: list = cache_manager.get_cached_offers()
        self._open_trades: list = []
        self._collapsed_groups: set = set()  # group names currently folded
        self._trade_monitor_active = False
        self._greeted_trade_ids: set = set()  # trades we already sent greeting to
        self._last_offers_fetch = 0.0   # timestamp of last get_my_offers call
        self._offers_cooldown = 25.0   # seconds between get_my_offers calls (Haveno: 3/min)
        self._offers_refresh_lock = threading.Lock()  # prevent concurrent refreshes
        # Launch commands shown in the Connection tab
        self.launch_commands: list[LaunchCommand] = load_commands()
        # Publish spinner state
        self._spinner_running = False
        self._spinner_frames = ["◐", "◓", "◑", "◒"]
        self._spinner_index = 0

        self._build_ui()
        # Render cached state before the (possibly slow) auto-connect.
        self._render_offers_list()
        self._render_passwords_from_cache()
        self.after(300, self._auto_connect)

    # ── UI Layout ──────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_status_bar()

        # Tabbed layout
        self.tabview = ctk.CTkTabview(self, fg_color="transparent")
        self.tabview.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.tab_presets    = self.tabview.add("Presets")
        self.tab_offers     = self.tabview.add("Active Offers")
        self.tab_publish    = self.tabview.add("Publish")
        self.tab_trades     = self.tabview.add("Trades & Chat")
        self.tab_connection = self.tabview.add("Connection")

        # Publish tab must be built BEFORE presets tab because
        # _refresh_preset_list() uses self.preset_count_label from publish tab
        self._build_publish_tab(self.tab_publish)
        self._build_presets_tab(self.tab_presets)
        self._build_offers_tab(self.tab_offers)
        self._build_trades_tab(self.tab_trades)
        self._build_connection_tab(self.tab_connection)

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, height=44, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        ctk.CTkLabel(bar, text="Haveno Offer Automator",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=16)

        # IMPORTANT: pack the buttons FIRST so they stay glued to the
        # right edge of the bar. Anything packed after them (the status
        # indicator) will sit to the left of the buttons. If we packed
        # the status label first, a long error message would push the
        # buttons off the right edge of a narrow window.
        ctk.CTkButton(bar, text="Connection", width=100, height=28,
                      fg_color=LIGHT,
                      command=lambda: self.tabview.set("Connection")
                      ).pack(side="right", padx=(4, 12))
        ctk.CTkButton(bar, text="Reconnect", width=90, height=28,
                      fg_color=LIGHT, command=self._auto_connect).pack(side="right", padx=4)

        # Status indicator: fixed width so it can't shove siblings around.
        self.status_dot = ctk.CTkLabel(bar, text="●", text_color=RED,
                                       font=ctk.CTkFont(size=18))
        self.status_dot.pack(side="right", padx=4)
        self.status_label = ctk.CTkLabel(bar, text="Disconnected",
                                         width=220, anchor="e")
        self.status_label.pack(side="right", padx=2)

    # ── Tab: Presets ──────────────────────────────────────────────────────

    def _build_presets_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkLabel(hdr, text="Offer Presets",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="+ New Preset", width=110, height=30,
                      fg_color=ORANGE, command=self._new_preset).pack(side="right")

        # Scrollable list
        self.preset_scroll = ctk.CTkScrollableFrame(parent, fg_color=GREY)
        self.preset_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.preset_scroll.columnconfigure(0, weight=1)

        self._refresh_preset_list()

    # ── Tab: Active Offers ────────────────────────────────────────────────

    def _build_offers_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # Header with refresh button
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkLabel(hdr, text="Active Offers on Daemon",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.offer_count_label = ctk.CTkLabel(hdr, text="", text_color="grey")
        self.offer_count_label.pack(side="left", padx=12)
        ctk.CTkButton(hdr, text="Refresh", width=90, height=28,
                      fg_color=LIGHT, command=self._refresh_offers).pack(side="right", padx=4)
        ctk.CTkButton(hdr, text="Cancel ALL", width=100, height=28,
                      fg_color="#8B0000", command=self._cancel_all).pack(side="right", padx=4)

        # Scrollable offers list
        self.offers_scroll = ctk.CTkScrollableFrame(parent, fg_color=GREY)
        self.offers_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.offers_scroll.columnconfigure(0, weight=1)

        ctk.CTkLabel(self.offers_scroll, text="Click 'Refresh' to load offers.",
                     text_color="grey").grid(row=0, column=0, pady=40)

    def _refresh_offers(self):
        if not self._connected:
            messagebox.showerror("Not connected", "Connect to the daemon first.", parent=self)
            return
        self._log("Refreshing active offers...")
        threading.Thread(target=self._refresh_offers_worker, daemon=True).start()

    def _refresh_offers_worker(self):
        # Prevent concurrent refresh calls (each one hits the rate limit)
        if not self._offers_refresh_lock.acquire(blocking=False):
            return  # another refresh is already running

        try:
            # Throttle: Haveno allows max 3 getmyoffers calls per minute
            now = time.time()
            wait = self._offers_cooldown - (now - self._last_offers_fetch)
            if wait > 0:
                self.after(0, self._log, f"Rate limit — waiting {wait:.0f}s before refreshing offers...")
                time.sleep(wait)
            self._last_offers_fetch = time.time()
            offers = self.client.get_my_offers()
            # Filter out offers that have already been taken — i.e.
            # they show up as an OPEN trade. Haveno occasionally still
            # reports them via GetMyOffers for a short window after the
            # take, which clutters the "Active Offers" view.
            try:
                open_trades = self.client.get_trades("OPEN")
                self._open_trades = open_trades
                in_trade_ids = {
                    (t.get("offer") or {}).get("id", "")
                    for t in open_trades
                }
                in_trade_ids.discard("")
                offers = [o for o in offers if o.get("id") not in in_trade_ids]
            except Exception as trade_err:
                # Non-fatal — fall back to showing all offers
                self.after(0, self._log,
                    f"  (couldn't filter in-trade offers: {trade_err})")
            self._active_offers = offers
            # Persist so the Active Offers tab stays populated after
            # the daemon is killed or the network drops.
            try:
                cache_manager.save_offers(offers)
            except Exception:
                pass
            self.after(0, self._render_offers_list)
            self.after(0, self._log, f"Loaded {len(offers)} active offer(s)")
        except Exception as e:
            self.after(0, self._log, f"Failed to load offers: {e}")
        finally:
            self._offers_refresh_lock.release()

    def _render_offers_list(self):
        for w in self.offers_scroll.winfo_children():
            w.destroy()

        count_text = f"({len(self._active_offers)} offers)"
        if not self._connected:
            # Make it explicit that the list is stale — the user can
            # still see the IDs and descriptions but action buttons
            # (Pause/Cancel) will fail until they reconnect.
            import time as _time
            ts = cache_manager.get_offers_updated_at()
            if ts:
                age_min = max(0, int((_time.time() - ts) / 60))
                count_text += f"  — cached ({age_min} min ago, offline)"
            else:
                count_text += "  — offline"
        self.offer_count_label.configure(text=count_text)

        if not self._active_offers:
            ctk.CTkLabel(self.offers_scroll, text="No active offers.",
                         text_color="grey").grid(row=0, column=0, pady=40)
            return

        for i, offer in enumerate(self._active_offers):
            self._offer_row(i, offer)

    def _offer_row(self, row: int, offer: dict):
        row_frame = ctk.CTkFrame(self.offers_scroll, fg_color=LIGHT, corner_radius=8)
        row_frame.grid(row=row, column=0, sticky="ew", pady=3)
        row_frame.columnconfigure(1, weight=1)

        # Status indicator
        color = GREEN if offer.get("is_activated") else RED
        ctk.CTkLabel(row_frame, text="●", text_color=color,
                     font=ctk.CTkFont(size=14)).grid(row=0, column=0, padx=10, pady=10)

        # Offer info
        info = ctk.CTkFrame(row_frame, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", pady=6)

        xmr_amount = HavenoClient.atomic_to_xmr(offer["amount"])
        min_xmr    = HavenoClient.atomic_to_xmr(offer["min_amount"])
        margin     = offer.get("market_price_margin_pct", 0) * 100  # API fraction → display %
        currency   = offer.get("currency_code", "?")
        offer_id   = offer.get("id", "???")
        state      = offer.get("state", "?")
        method     = offer.get("payment_method", "?")
        extra      = offer.get("extra_info", "")
        challenge  = offer.get("challenge", "")

        ctk.CTkLabel(info, text=f"{currency}  |  {xmr_amount:.4f} XMR  |  +{margin:.1f}%",
                     font=ctk.CTkFont(weight="bold", size=13), anchor="w").pack(fill="x")
        ctk.CTkLabel(info,
                     text=f"Min: {min_xmr:.4f} XMR  |  Method: {method}  |  State: {state}",
                     text_color="grey", anchor="w", font=ctk.CTkFont(size=11)).pack(fill="x")
        ctk.CTkLabel(info,
                     text=f"ID: {offer_id[:20]}...",
                     text_color="grey", anchor="w", font=ctk.CTkFont(size=10)).pack(fill="x")

        if extra:
            # Show first 60 chars of description
            short_extra = extra[:60] + ("..." if len(extra) > 60 else "")
            ctk.CTkLabel(info, text=f"Desc: {short_extra}",
                         text_color="#888", anchor="w", font=ctk.CTkFont(size=10)).pack(fill="x")

        if challenge:
            ctk.CTkLabel(info, text=f"Challenge: {challenge}",
                         text_color=ORANGE, anchor="w", font=ctk.CTkFont(size=10)).pack(fill="x")

        # Action buttons
        btns = ctk.CTkFrame(row_frame, fg_color="transparent")
        btns.grid(row=0, column=2, padx=8)

        if offer.get("is_activated"):
            ctk.CTkButton(btns, text="Pause", width=60, height=26, fg_color=LIGHT,
                          command=lambda oid=offer_id: self._deactivate_offer(oid)).pack(pady=2)
        else:
            ctk.CTkButton(btns, text="Resume", width=60, height=26, fg_color=GREEN,
                          command=lambda oid=offer_id: self._activate_offer(oid)).pack(pady=2)

        ctk.CTkButton(btns, text="Cancel", width=60, height=26, fg_color="#8B0000",
                      command=lambda oid=offer_id: self._cancel_single_offer(oid)).pack(pady=2)

    def _cancel_single_offer(self, offer_id: str):
        short = offer_id[:16]
        if not messagebox.askyesno("Cancel offer", f"Cancel offer {short}...?", parent=self):
            return
        def worker():
            try:
                self.client.cancel_offer(offer_id)
                self.after(0, self._log, f"Cancelled offer {short}...")
                self.after(0, self._refresh_offers_worker_from_main)
            except Exception as e:
                self.after(0, self._log, f"Failed to cancel {short}...: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _deactivate_offer(self, offer_id: str):
        def worker():
            try:
                self.client.deactivate_offer(offer_id)
                self.after(0, self._log, f"Paused offer {offer_id[:16]}...")
                self.after(0, self._refresh_offers_worker_from_main)
            except Exception as e:
                self.after(0, self._log, f"Failed to pause: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _activate_offer(self, offer_id: str):
        def worker():
            try:
                self.client.activate_offer(offer_id)
                self.after(0, self._log, f"Resumed offer {offer_id[:16]}...")
                self.after(0, self._refresh_offers_worker_from_main)
            except Exception as e:
                self.after(0, self._log, f"Failed to resume: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_offers_worker_from_main(self):
        """Trigger offer refresh from main thread."""
        threading.Thread(target=self._refresh_offers_worker, daemon=True).start()

    # ── Tab: Trades & Chat ─────────────────────────────────────────────────

    def _build_trades_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkLabel(hdr, text="Open Trades & Auto-Chat",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.trade_count_label = ctk.CTkLabel(hdr, text="", text_color="grey")
        self.trade_count_label.pack(side="left", padx=12)

        ctk.CTkButton(hdr, text="Refresh", width=90, height=28,
                      fg_color=LIGHT, command=self._refresh_trades).pack(side="right", padx=4)

        self.monitor_btn = ctk.CTkButton(
            hdr, text="Start Monitor", width=120, height=28,
            fg_color=GREEN, command=self._toggle_trade_monitor
        )
        self.monitor_btn.pack(side="right", padx=4)

        # Scrollable trades list
        self.trades_scroll = ctk.CTkScrollableFrame(parent, fg_color=GREY)
        self.trades_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.trades_scroll.columnconfigure(0, weight=1)

        ctk.CTkLabel(self.trades_scroll, text="Click 'Refresh' to load open trades.",
                     text_color="grey").grid(row=0, column=0, pady=40)

    def _refresh_trades(self):
        if not self._connected:
            messagebox.showerror("Not connected", "Connect to the daemon first.", parent=self)
            return
        threading.Thread(target=self._refresh_trades_worker, daemon=True).start()

    def _refresh_trades_worker(self):
        try:
            trades = self.client.get_trades("OPEN")
            self._open_trades = trades
            self.after(0, self._render_trades_list)
        except Exception as e:
            self.after(0, self._log, f"Failed to load trades: {e}")

    def _render_trades_list(self):
        for w in self.trades_scroll.winfo_children():
            w.destroy()

        self.trade_count_label.configure(text=f"({len(self._open_trades)} open)")

        if not self._open_trades:
            ctk.CTkLabel(self.trades_scroll, text="No open trades.",
                         text_color="grey").grid(row=0, column=0, pady=40)
            return

        for i, trade in enumerate(self._open_trades):
            self._trade_row(i, trade)

    def _trade_row(self, row: int, trade: dict):
        row_frame = ctk.CTkFrame(self.trades_scroll, fg_color=LIGHT, corner_radius=8)
        row_frame.grid(row=row, column=0, sticky="ew", pady=3)
        row_frame.columnconfigure(1, weight=1)

        # Phase color
        phase = trade.get("phase", "")
        if "DEPOSIT" in phase:
            color = ORANGE
        elif "PAYMENT_SENT" in phase:
            color = "#FFD700"
        elif "COMPLETED" in phase or trade.get("is_completed"):
            color = GREEN
        else:
            color = "grey"

        ctk.CTkLabel(row_frame, text="●", text_color=color,
                     font=ctk.CTkFont(size=14)).grid(row=0, column=0, padx=10, pady=10)

        info = ctk.CTkFrame(row_frame, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", pady=6)

        trade_id = trade.get("trade_id", "???")
        short_id = trade.get("short_id", trade_id[:12])
        xmr_amount = HavenoClient.atomic_to_xmr(trade.get("amount", 0))
        offer = trade.get("offer", {})
        currency = offer.get("currency_code", "?")
        state = trade.get("state", "?")

        ctk.CTkLabel(info, text=f"{short_id}  |  {xmr_amount:.4f} XMR  |  {currency}",
                     font=ctk.CTkFont(weight="bold", size=13), anchor="w").pack(fill="x")
        ctk.CTkLabel(info, text=f"State: {state}  |  Phase: {phase}",
                     text_color="grey", anchor="w", font=ctk.CTkFont(size=11)).pack(fill="x")
        ctk.CTkLabel(info, text=f"Role: {trade.get('role', '?')}",
                     text_color="grey", anchor="w", font=ctk.CTkFont(size=10)).pack(fill="x")

        # Buttons
        btns = ctk.CTkFrame(row_frame, fg_color="transparent")
        btns.grid(row=0, column=2, padx=8)
        ctk.CTkButton(btns, text="Chat", width=55, height=26, fg_color=ORANGE,
                      command=lambda tid=trade_id: self._open_chat_window(tid)).pack(pady=2)
        ctk.CTkButton(btns, text="Send Greeting", width=90, height=26, fg_color=LIGHT,
                      command=lambda tid=trade_id: self._send_greeting_to_trade(tid)).pack(pady=2)

    def _open_chat_window(self, trade_id: str):
        """Open a small chat window for a trade.

        Auto-loads on open and polls every 4s while the window is alive
        so messages from the buyer show up without manual refresh.
        Send button is rate-limit aware (Haveno: 4 chat msgs/minute).
        """
        win = ctk.CTkToplevel(self)
        win.title(f"Chat — {trade_id[:12]}")
        win.geometry("520x500")
        win.after(200, lambda: self._safe_grab_win(win))

        # Track window lifetime so the polling loop stops itself.
        state = {"alive": True, "msg_count": -1}
        def _on_close():
            state["alive"] = False
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        # Messages display
        chat_box = ctk.CTkTextbox(win, state="disabled", font=ctk.CTkFont(size=12))
        chat_box.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        # Status line for rate-limit warnings etc.
        status_var = StringVar(value="")
        status_lbl = ctk.CTkLabel(win, textvariable=status_var,
                                  text_color=ORANGE, anchor="w",
                                  font=ctk.CTkFont(size=11))
        status_lbl.pack(fill="x", padx=10, pady=(0, 2))

        # Input row
        input_frame = ctk.CTkFrame(win, fg_color="transparent")
        input_frame.pack(fill="x", padx=8, pady=(0, 8))
        msg_var = StringVar()
        msg_entry = ctk.CTkEntry(input_frame, textvariable=msg_var,
                                 placeholder_text="Type a message...")
        msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        def render_msgs(msgs):
            if not state["alive"]:
                return
            # Only redraw if the message count actually changed,
            # otherwise polling makes the textbox jitter and lose
            # the user's scroll position.
            if len(msgs) == state["msg_count"]:
                return
            state["msg_count"] = len(msgs)
            chat_box.configure(state="normal")
            chat_box.delete("1.0", "end")
            for m in msgs:
                sender = "You" if m["sender_is_trader"] else "Peer"
                if m["is_system_message"]:
                    sender = "System"
                chat_box.insert("end", f"[{sender}] {m['message']}\n")
            chat_box.see("end")
            chat_box.configure(state="disabled")

        def refresh_chat():
            def worker():
                try:
                    msgs = self.client.get_chat_messages(trade_id)
                    self.after(0, lambda: render_msgs(msgs))
                except Exception as e:
                    if state["alive"]:
                        self.after(0, self._log, f"Chat load error: {e}")
            threading.Thread(target=worker, daemon=True).start()

        def poll_loop():
            if not state["alive"]:
                return
            refresh_chat()
            win.after(4000, poll_loop)

        def send():
            text = msg_var.get().strip()
            if not text:
                return
            # Pre-flight rate-limit check so we can warn the user
            # BEFORE we try to hit the daemon and trip the limit.
            allowed, wait = self.client.check_chat_send_allowed(trade_id)
            if not allowed:
                status_var.set(
                    f"Rate limit: the app is limited to 4 chat messages "
                    f"per minute (Haveno API). Wait {wait:.0f}s."
                )
                return
            status_var.set("")
            msg_var.set("")
            def worker():
                try:
                    self.client.send_chat_message(trade_id, text)
                    if state["alive"]:
                        self.after(0, refresh_chat)
                except Exception as e:
                    err = str(e)
                    if state["alive"]:
                        if "CHAT_RATE_LIMIT" in err:
                            self.after(0, lambda: status_var.set(err))
                        else:
                            self.after(0, self._log, f"Chat send error: {e}")
            threading.Thread(target=worker, daemon=True).start()

        ctk.CTkButton(input_frame, text="Send", width=70, height=30,
                      fg_color=ORANGE, command=send).pack(side="right")
        msg_entry.bind("<Return>", lambda e: send())

        # Initial load + start polling
        refresh_chat()
        win.after(4000, poll_loop)

    def _safe_grab_win(self, win):
        try:
            win.grab_set()
        except Exception:
            win.after(100, lambda: self._safe_grab_win(win))

    def _send_greeting_to_trade(self, trade_id: str):
        """Send the auto-chat greeting for a trade based on its offer's preset."""
        # Find matching preset by offer ID
        trade = next((t for t in self._open_trades if t["trade_id"] == trade_id), None)
        if not trade:
            self._log(f"Trade {trade_id[:12]} not found in open trades")
            return

        offer_id = trade.get("offer", {}).get("id", "")
        preset = self._find_preset_for_offer(offer_id)

        if not preset or not preset.auto_chat_enabled:
            self._log(f"No auto-chat preset found for trade {trade_id[:12]}")
            return

        messages_to_send = []
        if preset.auto_chat_greeting.strip():
            messages_to_send.append(preset.auto_chat_greeting.strip())
        for line in preset.auto_chat_messages.strip().splitlines():
            line = line.strip()
            if line:
                messages_to_send.append(line)

        if not messages_to_send:
            return

        def worker():
            for msg in messages_to_send:
                try:
                    self.client.send_chat_message(trade_id, msg)
                    self.after(0, self._log, f"  Chat sent to {trade_id[:12]}: {msg[:50]}...")
                except Exception as e:
                    self.after(0, self._log, f"  Chat error {trade_id[:12]}: {e}")
                    break

        self._log(f"Sending {len(messages_to_send)} auto-chat message(s) to {trade_id[:12]}...")
        threading.Thread(target=worker, daemon=True).start()

    def _find_preset_for_offer(self, offer_id: str) -> "Preset | None":
        """Try to match an offer to a preset by payment account + currency."""
        # We don't store which preset created which offer, so match by payment account
        for offer in self._active_offers:
            if offer.get("id") == offer_id:
                for p in self.presets:
                    if (p.payment_account_id == offer.get("payment_account_id") and
                            p.currency_code == offer.get("currency_code")):
                        return p
        # Fallback: check if any preset has auto-chat enabled
        for p in self.presets:
            if p.auto_chat_enabled:
                return p
        return None

    # ── Trade monitor (auto-greet new trades) ─────────────────────────────

    def _toggle_trade_monitor(self):
        if self._trade_monitor_active:
            self._trade_monitor_active = False
            self.monitor_btn.configure(text="Start Monitor", fg_color=GREEN)
            self._log("Trade monitor stopped.")
        else:
            if not self._connected:
                messagebox.showerror("Not connected", "Connect to the daemon first.", parent=self)
                return
            self._trade_monitor_active = True
            self.monitor_btn.configure(text="Stop Monitor", fg_color=RED)
            self._log("Trade monitor started — auto-greeting new trades every 30s...")
            # Snapshot current trade IDs so we don't greet existing trades
            threading.Thread(target=self._monitor_init, daemon=True).start()

    def _monitor_init(self):
        try:
            trades = self.client.get_trades("OPEN")
            self._greeted_trade_ids = {t["trade_id"] for t in trades}
            self.after(0, self._log, f"Monitor: {len(trades)} existing trades noted.")
        except Exception as e:
            self.after(0, self._log, f"Monitor init error: {e}")
        self.after(30000, self._monitor_tick)

    def _monitor_tick(self):
        if not self._trade_monitor_active:
            return
        threading.Thread(target=self._monitor_check, daemon=True).start()

    def _monitor_check(self):
        try:
            trades = self.client.get_trades("OPEN")
            self._open_trades = trades
            self.after(0, self._render_trades_list)

            new_trades = [t for t in trades if t["trade_id"] not in self._greeted_trade_ids]
            for t in new_trades:
                trade_id = t["trade_id"]
                self._greeted_trade_ids.add(trade_id)
                offer_id = t.get("offer", {}).get("id", "")
                preset = self._find_preset_for_offer(offer_id)
                if preset and preset.auto_chat_enabled:
                    self.after(0, self._log, f"Monitor: New trade {trade_id[:12]} — sending greeting...")
                    self._send_greeting_to_trade(trade_id)
                else:
                    self.after(0, self._log, f"Monitor: New trade {trade_id[:12]} — no auto-chat preset.")
        except Exception as e:
            self.after(0, self._log, f"Monitor error: {e}")
        # Schedule next tick
        self.after(30000, self._monitor_tick)

    # ── Tab: Connection ───────────────────────────────────────────────────
    #
    # Single place to manage everything daemon-related: the copy-pasteable
    # launch commands (seednode / user1 daemon / user1 desktop / anything
    # the user adds) and the gRPC connection settings. The app itself is
    # fully usable offline — this tab is only needed when the user wants
    # to actually publish offers.

    def _build_connection_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkLabel(hdr, text="Connection",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="Reconnect", width=100, height=28,
                      fg_color=LIGHT, command=self._auto_connect).pack(side="right", padx=4)

        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        scroll.columnconfigure(0, weight=1)

        # ── Daemon settings section ──────────────────────────────────────
        cfg = load_config()
        settings_frame = ctk.CTkFrame(scroll, fg_color=GREY, corner_radius=8)
        settings_frame.grid(row=0, column=0, sticky="ew", pady=(4, 12))
        settings_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(settings_frame, text="Daemon settings",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=ORANGE).grid(row=0, column=0, columnspan=2,
                                             sticky="w", padx=12, pady=(10, 6))

        self.conn_host_var = StringVar(value=cfg.get("host", "localhost"))
        self.conn_port_var = StringVar(value=str(cfg.get("port", 3201)))
        self.conn_pw_var   = StringVar(value=cfg.get("password", ""))
        self.conn_acc_pw_var = StringVar(value=cfg.get("account_password", ""))

        def _row(label, var, row, show=None):
            ctk.CTkLabel(settings_frame, text=label, anchor="w",
                         width=180).grid(row=row, column=0, sticky="w",
                                         padx=12, pady=4)
            entry = ctk.CTkEntry(settings_frame, textvariable=var)
            if show:
                entry.configure(show=show)
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=4)

        _row("Daemon host",     self.conn_host_var, 1)
        _row("gRPC port",       self.conn_port_var, 2)
        _row("API password",    self.conn_pw_var,   3, show="*")
        _row("Account password",self.conn_acc_pw_var, 4, show="*")

        btns = ctk.CTkFrame(settings_frame, fg_color="transparent")
        btns.grid(row=5, column=0, columnspan=2, sticky="e", padx=12, pady=(8, 12))
        ctk.CTkButton(btns, text="Save", width=100, height=32,
                      fg_color=LIGHT,
                      command=self._save_connection_settings).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Save & Reconnect", width=160, height=32,
                      fg_color=ORANGE,
                      command=self._save_and_reconnect).pack(side="right", padx=4)

        # ── Launch commands section ──────────────────────────────────────
        cmds_header = ctk.CTkFrame(scroll, fg_color="transparent")
        cmds_header.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        cmds_header.columnconfigure(0, weight=1)
        ctk.CTkLabel(cmds_header, text="Launch commands",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=ORANGE, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(cmds_header, text="+ New Command", width=140, height=28,
                      fg_color=ORANGE,
                      command=self._new_launch_command).grid(row=0, column=1, padx=4)

        self.commands_frame = ctk.CTkFrame(scroll, fg_color=GREY, corner_radius=8)
        self.commands_frame.grid(row=2, column=0, sticky="ew", pady=(4, 12))
        self.commands_frame.columnconfigure(0, weight=1)
        self._refresh_commands_list()

    def _refresh_commands_list(self):
        for w in self.commands_frame.winfo_children():
            w.destroy()

        if not self.launch_commands:
            ctk.CTkLabel(self.commands_frame,
                         text="No commands yet. Click '+ New Command' to add one.",
                         text_color="grey").grid(row=0, column=0, padx=12, pady=20)
            return

        for row, cmd in enumerate(self.launch_commands):
            self._launch_command_row(row, cmd)

    def _launch_command_row(self, row: int, cmd: LaunchCommand):
        row_frame = ctk.CTkFrame(self.commands_frame, fg_color=LIGHT, corner_radius=6)
        row_frame.grid(row=row, column=0, sticky="ew", padx=6, pady=3)
        row_frame.columnconfigure(0, weight=1)

        info = ctk.CTkFrame(row_frame, fg_color="transparent")
        info.grid(row=0, column=0, sticky="ew", padx=10, pady=6)

        name_line = cmd.name
        if cmd.category:
            name_line += f"  [{cmd.category}]"
        ctk.CTkLabel(info, text=name_line,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x")
        # Truncate long commands in display, but copy the FULL command.
        short_cmd = cmd.command if len(cmd.command) <= 120 else cmd.command[:117] + "..."
        ctk.CTkLabel(info, text=short_cmd, text_color="grey",
                     font=ctk.CTkFont(size=10), anchor="w",
                     wraplength=760, justify="left").pack(fill="x")

        btns = ctk.CTkFrame(row_frame, fg_color="transparent")
        btns.grid(row=0, column=1, padx=6, pady=6)
        copy_btn = ctk.CTkButton(
            btns, text="Copy", width=70, height=26, fg_color=ORANGE,
        )
        copy_btn.configure(
            command=lambda c=cmd.command, b=copy_btn: self._copy_to_clipboard(c, b)
        )
        copy_btn.pack(side="left", padx=2)
        ctk.CTkButton(btns, text="Edit", width=55, height=26, fg_color=LIGHT,
                      command=lambda c=cmd: self._edit_launch_command(c)).pack(side="left", padx=2)
        ctk.CTkButton(btns, text="Del", width=55, height=26, fg_color="#5a1a1a",
                      command=lambda c=cmd: self._delete_launch_command(c)).pack(side="left", padx=2)

    def _copy_to_clipboard(self, text: str, button=None):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()  # flush so other apps can read it
            if button is not None:
                original = button.cget("text")
                original_fg = button.cget("fg_color")
                button.configure(text="Copied!", fg_color=GREEN)
                self.after(1200, lambda: button.configure(text=original, fg_color=original_fg))
        except Exception as e:
            self._log(f"Clipboard error: {e}")

    def _new_launch_command(self):
        LaunchCommandDialog(self, on_save=self._on_launch_command_saved)

    def _edit_launch_command(self, cmd: LaunchCommand):
        LaunchCommandDialog(self, existing=cmd, on_save=self._on_launch_command_edited)

    def _delete_launch_command(self, cmd: LaunchCommand):
        if messagebox.askyesno("Delete command", f"Delete '{cmd.name}'?", parent=self):
            self.launch_commands = delete_command(self.launch_commands, cmd.id)
            self._refresh_commands_list()

    def _on_launch_command_saved(self, cmd: LaunchCommand):
        self.launch_commands = add_command(self.launch_commands, cmd)
        self._refresh_commands_list()

    def _on_launch_command_edited(self, cmd: LaunchCommand):
        self.launch_commands = update_command(self.launch_commands, cmd)
        self._refresh_commands_list()

    def _save_connection_settings(self):
        try:
            port = int(self.conn_port_var.get())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number.", parent=self)
            return
        cfg = load_config()
        cfg["host"] = self.conn_host_var.get().strip()
        cfg["port"] = port
        cfg["password"] = self.conn_pw_var.get()
        cfg["account_password"] = self.conn_acc_pw_var.get()
        save_config(cfg)
        self._log("Connection settings saved.")

    def _save_and_reconnect(self):
        self._save_connection_settings()
        self._auto_connect()

    # ── Tab: Publish ──────────────────────────────────────────────────────

    def _build_publish_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        parent.rowconfigure(3, weight=0)

        ctk.CTkLabel(parent, text="Publish Offers",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 4))

        # Settings row
        settings_frame = ctk.CTkFrame(parent, fg_color="transparent")
        settings_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=6)

        ctk.CTkLabel(settings_frame, text="XMR amount per offer:").pack(side="left", padx=(0, 10))
        self.xmr_var = StringVar(value="0.5")
        ctk.CTkEntry(settings_frame, textvariable=self.xmr_var, width=120).pack(side="left")

        self.preset_count_label = ctk.CTkLabel(settings_frame, text="", text_color="grey")
        self.preset_count_label.pack(side="left", padx=20)
        self._update_preset_count()

        # Action buttons
        btn_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        btn_frame.pack(side="right")

        self.publish_btn = ctk.CTkButton(
            btn_frame, text="Publish All Offers",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38, width=180, fg_color=ORANGE,
            command=self._publish_all
        )
        self.publish_btn.pack(side="right", padx=4)

        # Rotating-circle spinner shown while publish_all() is running.
        # Hidden by default; _start_spinner() packs it next to the button
        # and _stop_spinner() removes it. The user asked for a spinning
        # indicator (not a progress bar) so they can see something is
        # actually happening while the daemon processes offers one by
        # one.
        self.spinner_label = ctk.CTkLabel(
            btn_frame, text="", font=ctk.CTkFont(size=20, weight="bold"),
            text_color=ORANGE, width=30
        )
        self.spinner_status = ctk.CTkLabel(
            btn_frame, text="", text_color="grey",
            font=ctk.CTkFont(size=11)
        )

        # Log
        log_frame = ctk.CTkFrame(parent, fg_color="transparent")
        log_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(6, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)

        ctk.CTkLabel(log_frame, text="Activity log", font=ctk.CTkFont(size=12),
                     text_color="grey").grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.log_box = ctk.CTkTextbox(log_frame, state="disabled", font=ctk.CTkFont(size=12))
        self.log_box.grid(row=1, column=0, sticky="nsew")

        # ── Recent passwords panel ────────────────────────────────────
        # After publishing, each offer that has a Haveno-generated
        # challenge gets a row here with a one-click copy button so the
        # user can paste it into the Haveno desktop GUI to edit the
        # offer description (the workaround for no-deposit offers).
        pw_frame = ctk.CTkFrame(parent, fg_color=GREY, corner_radius=8)
        pw_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        pw_frame.columnconfigure(0, weight=1)

        pw_header = ctk.CTkFrame(pw_frame, fg_color="transparent")
        pw_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        pw_header.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            pw_header, text="Recent passwords (persisted — usable offline)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="grey", anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            pw_header, text="Clear", width=70, height=22,
            fg_color="#5a1a1a", font=ctk.CTkFont(size=11),
            command=self._clear_passwords,
        ).grid(row=0, column=1, padx=4)

        self.passwords_box = ctk.CTkScrollableFrame(
            pw_frame, fg_color="transparent", height=150
        )
        self.passwords_box.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.passwords_box.columnconfigure(0, weight=1)
        self._password_rows = 0

    # ── Preset list ────────────────────────────────────────────────────────

    def _refresh_preset_list(self):
        for w in self.preset_scroll.winfo_children():
            w.destroy()

        if not self.presets:
            ctk.CTkLabel(self.preset_scroll, text="No presets yet.\nClick '+ New Preset' to create one.",
                         text_color="grey").grid(row=0, column=0, pady=40)
            return

        # Build grouped structure: { group_name: [preset, ...] }
        groups = get_groups(self.presets)
        grouped = {}
        for g in groups:
            grouped[g] = [p for p in self.presets if p.group == g]

        grid_row = 0
        for group_name in groups:
            group_presets = grouped[group_name]
            if not group_presets:
                continue

            # ── Group header ──────────────────────────────────────────
            display_name = group_name if group_name else "Ungrouped"
            collapsed = group_name in self._collapsed_groups
            hdr = ctk.CTkFrame(self.preset_scroll, fg_color="#1E1E1E", corner_radius=6)
            hdr.grid(row=grid_row, column=0, sticky="ew", pady=(8, 2))
            hdr.columnconfigure(2, weight=1)

            # Fold/unfold toggle (▶ when collapsed, ▼ when expanded)
            fold_char = "▶" if collapsed else "▼"
            ctk.CTkButton(
                hdr, text=fold_char, width=28, height=24, fg_color="#1E1E1E",
                hover_color=LIGHT, font=ctk.CTkFont(size=12),
                command=lambda g=group_name: self._toggle_group_fold(g)
            ).grid(row=0, column=0, padx=(6, 2), pady=4)

            ctk.CTkLabel(
                hdr, text=f"{display_name}  ({len(group_presets)})",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=ORANGE, anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=4, pady=6)

            ctk.CTkButton(
                hdr, text="All ON", width=60, height=24, fg_color=GREEN,
                font=ctk.CTkFont(size=11),
                command=lambda g=group_name: self._set_group_enabled(g, True)
            ).grid(row=0, column=3, padx=2, pady=4)
            ctk.CTkButton(
                hdr, text="All OFF", width=60, height=24, fg_color="#5a1a1a",
                font=ctk.CTkFont(size=11),
                command=lambda g=group_name: self._set_group_enabled(g, False)
            ).grid(row=0, column=4, padx=(2, 8), pady=4)

            grid_row += 1

            # ── Preset rows within this group ─────────────────────────
            if not collapsed:
                for idx_in_group, preset in enumerate(group_presets):
                    self._preset_row(grid_row, preset, idx_in_group, len(group_presets))
                    grid_row += 1

        self._update_preset_count()

    def _toggle_group_fold(self, group_name: str):
        """Collapse or expand a preset group in the list view."""
        if group_name in self._collapsed_groups:
            self._collapsed_groups.discard(group_name)
        else:
            self._collapsed_groups.add(group_name)
        self._refresh_preset_list()

    def _set_group_enabled(self, group_name: str, enabled: bool):
        """Enable or disable all presets in a group."""
        for p in self.presets:
            if p.group == group_name:
                p.enabled = enabled
        save_presets(self.presets)
        self._refresh_preset_list()

    def _preset_row(self, grid_row: int, preset: Preset,
                    idx_in_group: int, group_size: int):
        row_frame = ctk.CTkFrame(self.preset_scroll, fg_color=LIGHT, corner_radius=8)
        row_frame.grid(row=grid_row, column=0, sticky="ew", pady=2)
        row_frame.columnconfigure(1, weight=1)

        # Enable/disable checkbox
        en_var = BooleanVar(value=preset.enabled)
        def toggle(p=preset, v=en_var):
            p.enabled = v.get()
            save_presets(self.presets)
            self._update_preset_count()
        ctk.CTkCheckBox(row_frame, text="", variable=en_var, width=28,
                        command=toggle).grid(row=0, column=0, padx=6, pady=8)

        # Info
        info = ctk.CTkFrame(row_frame, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", pady=6)

        no_dep = " [no deposit]" if preset.buyer_as_taker_without_deposit else ""
        ctk.CTkLabel(info, text=f"{preset.name}{no_dep}",
                     font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(
            info,
            text=f"{preset.currency_code}  |  +{preset.market_price_margin_pct}%  |  min {preset.min_xmr} XMR",
            text_color="grey", anchor="w", font=ctk.CTkFont(size=11)
        ).pack(fill="x")
        ctk.CTkLabel(
            info,
            text=f"Account: {preset.payment_account_name or 'Not set'}",
            text_color="grey", anchor="w", font=ctk.CTkFont(size=10)
        ).pack(fill="x")

        # Move up/down within the global list
        global_idx = self.presets.index(preset)
        total = len(self.presets)
        move_btns = ctk.CTkFrame(row_frame, fg_color="transparent")
        move_btns.grid(row=0, column=2, padx=2)
        up_state = "normal" if global_idx > 0 else "disabled"
        down_state = "normal" if global_idx < total - 1 else "disabled"
        ctk.CTkButton(move_btns, text="^", width=28, height=22, fg_color=GREY,
                      state=up_state,
                      command=lambda r=global_idx: self._move_preset(r, -1)).pack(pady=1)
        ctk.CTkButton(move_btns, text="v", width=28, height=22, fg_color=GREY,
                      state=down_state,
                      command=lambda r=global_idx: self._move_preset(r, 1)).pack(pady=1)

        # Edit / Delete buttons
        btns = ctk.CTkFrame(row_frame, fg_color="transparent")
        btns.grid(row=0, column=3, padx=8)
        ctk.CTkButton(btns, text="Edit", width=50, height=26, fg_color=LIGHT,
                      command=lambda p=preset: self._edit_preset(p)).pack(pady=2)
        ctk.CTkButton(btns, text="Del", width=50, height=26, fg_color="#5a1a1a",
                      command=lambda p=preset: self._delete_preset(p)).pack(pady=2)

    # ── Preset reorder ──────────────────────────────────────────────────────

    def _move_preset(self, index: int, direction: int):
        """Move a preset up (-1) or down (+1) in the list."""
        new_index = index + direction
        if 0 <= new_index < len(self.presets):
            self.presets[index], self.presets[new_index] = self.presets[new_index], self.presets[index]
            save_presets(self.presets)
            self._refresh_preset_list()

    # ── Preset CRUD ────────────────────────────────────────────────────────

    def _new_preset(self):
        cfg = load_config()
        PresetDialog(
            self,
            payment_accounts=self.payment_accounts,
            default_template=cfg.get("default_description_template", ""),
            on_save=self._on_preset_saved
        )

    def _edit_preset(self, preset: Preset):
        cfg = load_config()
        PresetDialog(
            self,
            payment_accounts=self.payment_accounts,
            preset=preset,
            default_template=cfg.get("default_description_template", ""),
            on_save=self._on_preset_edited
        )

    def _on_preset_saved(self, preset: Preset):
        self.presets = add_preset(self.presets, preset)
        self._refresh_preset_list()

    def _on_preset_edited(self, preset: Preset):
        self.presets = update_preset(self.presets, preset)
        self._refresh_preset_list()

    def _delete_preset(self, preset: Preset):
        if messagebox.askyesno("Delete preset", f"Delete '{preset.name}'?", parent=self):
            self.presets = delete_preset(self.presets, preset.id)
            self._refresh_preset_list()

    def _update_preset_count(self):
        enabled = len(get_enabled_presets(self.presets))
        total   = len(self.presets)
        self.preset_count_label.configure(
            text=f"{enabled} of {total} presets enabled"
        )

    # ── Connection ─────────────────────────────────────────────────────────

    def _auto_connect(self):
        self._set_status("Connecting...", "grey")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            cfg = load_config()
            if self.client:
                self.client.disconnect()
            self.client = HavenoClient(
                host=cfg["host"], port=cfg["port"], password=cfg["password"],
                account_password=cfg.get("account_password", ""),
            )
            self.client.connect()
            version = self.client.get_version()

            # Ensure the Haveno account is open (required for both daemon & desktop modes)
            account_status = self.client.ensure_account_open()
            self.after(0, self._log, f"  Account: {account_status}")

            self.payment_accounts = self.client.get_payment_accounts()
            # Snapshot so the preset editor stays usable offline.
            try:
                cache_manager.save_payment_accounts(self.payment_accounts)
            except Exception:
                pass
            balances = self.client.get_balances()
            avail_xmr = HavenoClient.atomic_to_xmr(balances["available_balance"])
            reserved  = HavenoClient.atomic_to_xmr(balances["reserved_offer_balance"])
            self._connected = True
            self.after(0, lambda: self._set_status(
                f"Connected  v{version}  |  {avail_xmr:.4f} XMR available  |  {reserved:.4f} XMR reserved", GREEN
            ))
            self.after(0, self._log, f"Connected to daemon v{version}")
            self.after(0, self._log, f"  Available: {avail_xmr:.4f} XMR  |  Reserved: {reserved:.4f} XMR")
            self.after(0, self._log, f"  Payment accounts: {len(self.payment_accounts)}")
            # Auto-refresh offers on connect
            self.after(0, self._refresh_offers_worker_from_main)
        except Exception as e:
            self._connected = False
            err = str(e)
            # Detect the classic "wrong Haveno mode" pitfall: the user
            # is running `haveno-desktop` instead of `haveno-daemon`,
            # so the gRPC port is closed even though the GUI is up.
            # The desktop entry point (HavenoAppMain) doesn't include
            # daemon.jar in its classpath and never starts GrpcServer,
            # so --apiPort/--apiPassword are silently ignored.
            hint = ""
            if ("Connection refused" in err
                    or "failed to connect" in err.lower()
                    or "UNAVAILABLE" in err):
                hint = (
                    "  HINT: nothing is listening on the gRPC port. "
                    "Make sure Haveno is running in DAEMON mode "
                    "(e.g. `make user1-daemon-stagenet`), not desktop "
                    "mode — `haveno-desktop` does NOT expose the API."
                )
            self.after(0, lambda: self._set_status(f"Disconnected - {err}", RED))
            self.after(0, self._log, f"Connection failed: {err}")
            if hint:
                self.after(0, self._log, hint)

    def _set_status(self, text: str, color: str):
        # Keep the status bar one-line and short. Long gRPC errors get
        # truncated; the full message still goes to the log box below.
        first_line = text.splitlines()[0] if text else ""
        if len(first_line) > 38:
            first_line = first_line[:35] + "..."
        self.status_label.configure(text=first_line)
        self.status_dot.configure(text_color=color)

    def _open_settings(self):
        # Kept for backward compat — settings now live in the Connection tab.
        self.tabview.set("Connection")

    # ── Publish ────────────────────────────────────────────────────────────

    def _publish_all(self):
        if not self._connected:
            messagebox.showerror("Not connected", "Connect to the daemon first.", parent=self)
            return
        try:
            xmr = float(self.xmr_var.get())
        except ValueError:
            messagebox.showerror("Invalid amount", "Enter a valid XMR amount.", parent=self)
            return

        enabled = get_enabled_presets(self.presets)
        if not enabled:
            messagebox.showwarning("No presets", "Enable at least one preset.", parent=self)
            return

        if not messagebox.askyesno(
            "Confirm",
            f"Publish {len(enabled)} offers of {xmr} XMR each?",
            parent=self
        ):
            return

        self.publish_btn.configure(state="disabled", text="Publishing...")
        self._start_spinner(f"Publishing {len(enabled)} offer(s)...")
        self._log(f"-- Publishing {len(enabled)} offers ({xmr} XMR each) --")
        threading.Thread(target=self._publish_worker, args=(enabled, xmr), daemon=True).start()

    def _publish_worker(self, presets, xmr):
        total = len(presets)
        counter = {"done": 0}

        def on_progress(result):
            counter["done"] += 1
            self.after(0, self.update_spinner_status,
                       f"{counter['done']} / {total} done")
            if result.success:
                lines = [f"  OK  {result.preset_name}  ->  {result.offer_id[:12]}..."]
                if result.password:
                    status = "injected into description" if result.password_injected \
                             else "NOT injected — copy it manually"
                    lines.append(f"      password ({status}): {result.password}")
                    # Surface the password in the copy panel AND persist
                    # it to the cache so it survives daemon disconnects
                    # and app restarts (the user still needs to hand out
                    # the challenge to buyers after the fact).
                    cache_manager.add_password(
                        preset_name=result.preset_name,
                        password=result.password,
                        injected=result.password_injected,
                        offer_id=result.offer_id,
                    )
                    self.after(0, self._add_password_row,
                               result.preset_name, result.password,
                               result.password_injected)
                if result.warning:
                    lines.append(f"      WARN: {result.warning}")
                self.after(0, self._log, "\n".join(lines))
            else:
                self.after(0, self._log,
                    f"  FAIL  {result.preset_name}  ->  {result.error}")

        try:
            results = publish_all(self.client, presets, xmr, on_progress=on_progress)
            ok  = sum(1 for r in results if r.success)
            err = sum(1 for r in results if not r.success)
            self.after(0, self._log,
                f"-- Done: {ok} published, {err} failed --\n")
            # Auto-refresh offers after publish
            self.after(0, self._refresh_offers_worker_from_main)
        except Exception as e:
            self.after(0, self._log, f"Publish error: {e}")
        finally:
            self.after(0, self._stop_spinner)
            self.after(0, lambda: self.publish_btn.configure(
                state="normal", text="Publish All Offers"))

    def _cancel_all(self):
        if not self._connected:
            messagebox.showerror("Not connected", "Connect to the daemon first.", parent=self)
            return
        if not messagebox.askyesno(
            "Confirm",
            "Cancel ALL your active offers? This cannot be undone.",
            parent=self
        ):
            return

        self._log("-- Cancelling all active offers... --")
        def worker():
            def on_prog(oid, ok, err):
                status = "OK" if ok else "FAIL"
                msg = f"  {status}  {oid[:16]}... {'' if ok else err}"
                self.after(0, self._log, msg)
            r = cancel_all_my_offers(self.client, on_progress=on_prog)
            self.after(0, self._log,
                f"-- Done: {r['cancelled']} cancelled, {r['errors']} errors --\n")
            # Auto-refresh offers after cancel
            self.after(0, self._refresh_offers_worker_from_main)
        threading.Thread(target=worker, daemon=True).start()

    # ── Password copy panel ───────────────────────────────────────────────

    def _render_passwords_from_cache(self):
        """Populate the passwords panel from the persistent cache on
        startup. Each entry rendered here keeps the same 'Copy' UX as
        freshly-published passwords — this is what makes the panel
        usable in offline mode."""
        for w in self.passwords_box.winfo_children():
            w.destroy()
        self._password_rows = 0
        entries = cache_manager.get_passwords()
        if not entries:
            ctk.CTkLabel(
                self.passwords_box, text="No passwords yet.",
                text_color="grey", font=ctk.CTkFont(size=11)
            ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
            return
        # Show newest first.
        for entry in reversed(entries):
            self._add_password_row(
                entry.get("preset_name", "?"),
                entry.get("password", ""),
                bool(entry.get("injected", False)),
                persist=False,  # already in cache
            )

    def _clear_passwords(self):
        if not messagebox.askyesno(
            "Clear passwords",
            "Delete the entire password history? This cannot be undone.",
            parent=self,
        ):
            return
        cache_manager.clear_passwords()
        self._render_passwords_from_cache()

    def _add_password_row(self, preset_name: str, password: str,
                          injected: bool, persist: bool = True):
        """Append a row to the 'Recent passwords' panel with a copy
        button. Called from the publish worker via self.after() and from
        _render_passwords_from_cache() on startup. When persist=True
        the entry is ALREADY persisted by the caller (publish worker
        writes the cache before invoking us) — the flag only exists so
        cache-replay during startup doesn't double-write."""
        # First call: clear the "No passwords yet." placeholder.
        if self._password_rows == 0:
            for w in self.passwords_box.winfo_children():
                w.destroy()

        row = ctk.CTkFrame(self.passwords_box, fg_color=LIGHT, corner_radius=6)
        row.grid(row=self._password_rows, column=0, sticky="ew", pady=2, padx=2)
        row.columnconfigure(1, weight=1)
        self._password_rows += 1

        tag = "auto" if injected else "MANUAL"
        tag_color = GREEN if injected else ORANGE
        ctk.CTkLabel(row, text=f"[{tag}]", text_color=tag_color,
                     font=ctk.CTkFont(size=10, weight="bold"),
                     width=60).grid(row=0, column=0, padx=(8, 4), pady=4)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", pady=2)
        ctk.CTkLabel(info, text=preset_name,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     anchor="w").pack(fill="x")
        # Truncate display so very long phrases don't break the layout,
        # but always copy the FULL password to clipboard.
        short = password if len(password) <= 60 else password[:57] + "..."
        ctk.CTkLabel(info, text=short, text_color="grey",
                     font=ctk.CTkFont(size=10), anchor="w").pack(fill="x")

        copy_btn = ctk.CTkButton(
            row, text="Copy", width=60, height=26, fg_color=ORANGE,
            command=lambda p=password, b=None: self._copy_password(p, copy_btn)
        )
        copy_btn.grid(row=0, column=2, padx=8, pady=4)

    def _copy_password(self, password: str, button=None):
        """Copy a password to the system clipboard."""
        try:
            self.clipboard_clear()
            self.clipboard_append(password)
            # Force tk to flush the clipboard so other apps can read it
            # even if the window is destroyed shortly after.
            self.update()
            if button is not None:
                original = button.cget("text")
                button.configure(text="Copied!", fg_color=GREEN)
                self.after(1200,
                           lambda: button.configure(text=original, fg_color=ORANGE))
        except Exception as e:
            self._log(f"Clipboard error: {e}")

    # ── Publish spinner ───────────────────────────────────────────────────
    #
    # A small rotating-circle indicator shown next to the Publish button
    # while publish_all() runs. The glyphs (◐ ◓ ◑ ◒) are half-filled
    # circles that cycle in place — visually identical to a spinner
    # without needing an image asset or an animated GIF. We tick every
    # 120ms which is fast enough to read as "motion" but slow enough to
    # stay calm on low-end hardware.

    def _start_spinner(self, status_text: str = ""):
        if self._spinner_running:
            self.spinner_status.configure(text=status_text)
            return
        self._spinner_running = True
        self._spinner_index = 0
        self.spinner_label.pack(side="right", padx=(10, 2))
        self.spinner_status.configure(text=status_text)
        self.spinner_status.pack(side="right", padx=4)
        self._spinner_tick()

    def _stop_spinner(self):
        self._spinner_running = False
        try:
            self.spinner_label.pack_forget()
            self.spinner_status.pack_forget()
        except Exception:
            pass
        self.spinner_label.configure(text="")
        self.spinner_status.configure(text="")

    def _spinner_tick(self):
        if not self._spinner_running:
            return
        frame = self._spinner_frames[self._spinner_index % len(self._spinner_frames)]
        self.spinner_label.configure(text=frame)
        self._spinner_index += 1
        self.after(120, self._spinner_tick)

    def update_spinner_status(self, text: str):
        """Public helper so publish_worker callbacks can annotate progress."""
        try:
            self.spinner_status.configure(text=text)
        except Exception:
            pass

    # ── Log ────────────────────────────────────────────────────────────────

    def _log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
