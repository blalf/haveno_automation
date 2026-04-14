"""
Haveno/RetoSwap gRPC Client
Wraps the auto-generated proto stubs with a clean Python API.
"""

import grpc
import sys
import os
import time
import threading
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
import grpc_pb2
import grpc_pb2_grpc
import pb_pb2

# Haveno's server-side rate limit for GetMyOffers is 3 calls/minute.
# We self-throttle to stay well under that and avoid the daemon
# returning RESOURCE_EXHAUSTED (which can bubble up as an ugly crash).
_GET_MY_OFFERS_MIN_INTERVAL = 22.0  # seconds between calls
_GET_MY_OFFERS_CACHE_TTL = 15.0     # seconds to reuse a cached result

# Haveno limits SendChatMessage to 4 messages per minute (per-peer).
# We keep one window of timestamps per trade_id and refuse to send
# (without ever hitting the daemon) when the window is full.
_CHAT_SEND_LIMIT = 4
_CHAT_SEND_WINDOW = 60.0  # seconds


class AuthInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Injects the API password into every gRPC request metadata."""

    def __init__(self, password: str):
        self.password = password

    def intercept_unary_unary(self, continuation, client_call_details, request):
        metadata = list(client_call_details.metadata or [])
        metadata.append(("password", self.password))
        new_details = client_call_details._replace(metadata=metadata)
        return continuation(new_details, request)


class HavenoClient:
    """
    Python client for the Haveno/RetoSwap gRPC daemon.

    Usage:
        client = HavenoClient(host="localhost", port=3201, password="apitest")
        client.connect()
        offers = client.get_my_offers()
        client.disconnect()
    """

    def __init__(self, host: str = "localhost", port: int = 3201, password: str = "apitest",
                 account_password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self.account_password = account_password
        self._channel = None
        self._account_stub = None
        self._offers_stub = None
        self._wallets_stub = None
        self._payment_accounts_stub = None
        self._version_stub = None
        # Rate-limit state for get_my_offers
        self._offers_lock = threading.Lock()
        self._offers_cache: Optional[list] = None  # last successful reply
        self._offers_cache_ts: float = 0.0
        self._offers_last_call_ts: float = 0.0
        # Rate-limit state for send_chat_message
        # { trade_id: [ts1, ts2, ...] } — only timestamps within the window
        self._chat_send_lock = threading.Lock()
        self._chat_send_history: dict[str, list] = {}

    def connect(self):
        """Open the gRPC channel to the daemon."""
        target = f"{self.host}:{self.port}"
        interceptor = AuthInterceptor(self.password)
        self._channel = grpc.intercept_channel(
            grpc.insecure_channel(target),
            interceptor
        )
        self._account_stub = grpc_pb2_grpc.AccountStub(self._channel)
        self._offers_stub = grpc_pb2_grpc.OffersStub(self._channel)
        self._wallets_stub = grpc_pb2_grpc.WalletsStub(self._channel)
        self._payment_accounts_stub = grpc_pb2_grpc.PaymentAccountsStub(self._channel)
        self._version_stub = grpc_pb2_grpc.GetVersionStub(self._channel)
        self._trades_stub = grpc_pb2_grpc.TradesStub(self._channel)

    def disconnect(self):
        """Close the gRPC channel."""
        if self._channel:
            self._channel.close()
            self._channel = None

    def _check_connected(self):
        if not self._channel:
            raise RuntimeError("Not connected. Call connect() first.")

    # ── Version / health check ─────────────────────────────────────────────

    def get_version(self) -> str:
        """Returns the daemon version string. Good for testing connectivity."""
        self._check_connected()
        reply = self._version_stub.GetVersion(grpc_pb2.GetVersionRequest())
        return reply.version

    # ── Account ───────────────────────────────────────────────────────────

    def is_app_initialized(self) -> bool:
        """Check if the Haveno application has finished initializing."""
        self._check_connected()
        reply = self._account_stub.IsAppInitialized(
            grpc_pb2.IsAppInitializedRequest()
        )
        return reply.is_app_initialized

    def account_exists(self) -> bool:
        """Check if a Haveno account has been created."""
        self._check_connected()
        reply = self._account_stub.AccountExists(
            grpc_pb2.AccountExistsRequest()
        )
        return reply.account_exists

    def is_account_open(self) -> bool:
        """Check if the Haveno account is unlocked and ready."""
        self._check_connected()
        reply = self._account_stub.IsAccountOpen(
            grpc_pb2.IsAccountOpenRequest()
        )
        return reply.is_account_open

    def open_account(self, password: str) -> None:
        """Unlock the Haveno account with the given password."""
        self._check_connected()
        self._account_stub.OpenAccount(
            grpc_pb2.OpenAccountRequest(password=password)
        )

    def ensure_account_open(self) -> str:
        """
        Ensure the account is initialized and open.
        Returns a status message describing what happened.
        Raises RuntimeError if the account cannot be opened.
        """
        self._check_connected()

        # Wait for app initialization (can take a moment on desktop mode)
        if not self.is_app_initialized():
            raise RuntimeError(
                "Haveno app is not yet initialized. "
                "Please wait for it to finish starting up."
            )

        if self.is_account_open():
            return "Account already open"

        # Account exists but is locked — try to open it
        if self.account_exists():
            if not self.account_password:
                raise RuntimeError(
                    "Account is locked. Set an account password in Settings "
                    "or open the account in the Haveno GUI first."
                )
            self.open_account(self.account_password)
            return "Account unlocked via gRPC"

        # No account exists yet — create one
        if not self.account_password:
            raise RuntimeError(
                "No Haveno account found and no account password configured. "
                "Create an account in the Haveno GUI or set an account password in Settings."
            )
        # CreateAccount uses the same proto pattern
        self._account_stub.CreateAccount(
            grpc_pb2.CreateAccountRequest(password=self.account_password)
        )
        return "New account created via gRPC"

    # ── Wallet ─────────────────────────────────────────────────────────────

    def get_balances(self) -> dict:
        """Returns XMR balance info: available, reserved, total (in atomic units)."""
        self._check_connected()
        reply = self._wallets_stub.GetBalances(grpc_pb2.GetBalancesRequest())
        b = reply.balances.xmr
        return {
            "balance":                 b.balance,
            "available_balance":       b.available_balance,
            "pending_balance":         b.pending_balance,
            "reserved_offer_balance":  b.reserved_offer_balance,
            "reserved_trade_balance":  b.reserved_trade_balance,
        }

    # ── Payment accounts ───────────────────────────────────────────────────

    def get_payment_accounts(self) -> list:
        """Returns all configured payment accounts as a list of dicts."""
        self._check_connected()
        reply = self._payment_accounts_stub.GetPaymentAccounts(
            grpc_pb2.GetPaymentAccountsRequest()
        )
        accounts = []
        for acc in reply.payment_accounts:
            accounts.append({
                "id":             acc.id,
                "account_name":   acc.account_name,
                "payment_method": acc.payment_method.id,
                "currency_code":  acc.selected_trade_currency.code
                                  if acc.selected_trade_currency else None,
            })
        return accounts

    # ── Offers ─────────────────────────────────────────────────────────────

    def get_my_offers(
        self,
        direction: str = "",
        currency_code: str = "",
        force: bool = False,
    ) -> list:
        """
        Returns the user's posted offers.

        direction:     "BUY" | "SELL" | "" (all)
        currency_code: e.g. "USD", "EUR", "" (all)
        force:         Bypass the local cache and always hit the daemon
                       (still respects the inter-call throttle).

        This method is rate-limited to match Haveno's server-side limit
        of 3 GetMyOffers calls/minute. To stay well within that budget
        we:
          * Serve a cached result if it's younger than CACHE_TTL.
          * Otherwise wait until at least MIN_INTERVAL seconds have
            passed since the last real call.
          * If the daemon still returns RESOURCE_EXHAUSTED, we sleep and
            retry once instead of crashing — and if that also fails, we
            fall back to whatever we have in the cache.
        """
        self._check_connected()

        with self._offers_lock:
            now = time.time()

            # 1) Return fresh cache if we have one.
            if (
                not force
                and self._offers_cache is not None
                and (now - self._offers_cache_ts) < _GET_MY_OFFERS_CACHE_TTL
            ):
                return list(self._offers_cache)

            # 2) Throttle: wait until MIN_INTERVAL has passed.
            wait = _GET_MY_OFFERS_MIN_INTERVAL - (now - self._offers_last_call_ts)
            if wait > 0:
                time.sleep(wait)

            request = grpc_pb2.GetMyOffersRequest(
                direction=direction,
                currency_code=currency_code,
            )

            def _do_call() -> list:
                self._offers_last_call_ts = time.time()
                reply = self._offers_stub.GetMyOffers(request)
                return [self._parse_offer(o) for o in reply.offers]

            try:
                offers = _do_call()
            except grpc.RpcError as e:
                code = e.code() if hasattr(e, "code") else None
                if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                    # Daemon says we're still over the limit. Back off
                    # hard (22s = just past Haveno's 1/20s window) and
                    # try one more time.
                    time.sleep(_GET_MY_OFFERS_MIN_INTERVAL)
                    try:
                        offers = _do_call()
                    except grpc.RpcError:
                        if self._offers_cache is not None:
                            # Best-effort: return stale cache rather than
                            # crashing the UI.
                            return list(self._offers_cache)
                        raise
                else:
                    raise

            self._offers_cache = list(offers)
            self._offers_cache_ts = time.time()
            return offers

    def invalidate_offers_cache(self) -> None:
        """
        Drop the cached get_my_offers() result so the next call hits
        the daemon. Use this after posting or cancelling an offer.
        """
        with self._offers_lock:
            self._offers_cache = None
            self._offers_cache_ts = 0.0

    def post_offer(
        self,
        payment_account_id: str,
        currency_code: str,
        direction: str,           # "SELL" for selling XMR
        amount: int,              # in atomic units (1 XMR = 1_000_000_000_000)
        min_amount: int,
        market_price_margin_pct: float,
        security_deposit_pct: float = 0.15,
        extra_info: str = "",
        buyer_as_taker_without_deposit: bool = False,
        trigger_price: str = "",
        source_offer_id: str = "",
    ) -> dict:
        """
        Posts a new offer to sell XMR.
        Returns the created OfferInfo as a dict.
        """
        self._check_connected()
        reply = self._offers_stub.PostOffer(
            grpc_pb2.PostOfferRequest(
                currency_code=currency_code,
                direction=direction,
                use_market_based_price=True,
                market_price_margin_pct=market_price_margin_pct,
                amount=amount,
                min_amount=min_amount,
                security_deposit_pct=security_deposit_pct,
                payment_account_id=payment_account_id,
                is_private_offer=buyer_as_taker_without_deposit,  # must be private when no deposit
                buyer_as_taker_without_deposit=buyer_as_taker_without_deposit,
                extra_info=extra_info,
                trigger_price=trigger_price,
                source_offer_id=source_offer_id,
            )
        )
        self.invalidate_offers_cache()
        return self._parse_offer(reply.offer)

    def cancel_offer(self, offer_id: str):
        """Permanently removes an offer."""
        self._check_connected()
        self._offers_stub.CancelOffer(
            grpc_pb2.CancelOfferRequest(id=offer_id)
        )
        self.invalidate_offers_cache()

    def deactivate_offer(self, offer_id: str):
        """Temporarily disables an active offer."""
        self._check_connected()
        self._offers_stub.DeactivateOffer(
            grpc_pb2.DeactivateOfferRequest(offer_id=offer_id)
        )

    def activate_offer(self, offer_id: str):
        """Re-enables a deactivated offer."""
        self._check_connected()
        self._offers_stub.ActivateOffer(
            grpc_pb2.ActivateOfferRequest(offer_id=offer_id)
        )

    # ── Trades ─────────────────────────────────────────────────────────────

    def get_trades(self, category: str = "OPEN") -> list:
        """
        Returns trades in a given category.
        category: "OPEN" | "CLOSED" | "FAILED"
        """
        self._check_connected()
        cat_map = {"OPEN": 0, "CLOSED": 1, "FAILED": 2}
        reply = self._trades_stub.GetTrades(
            grpc_pb2.GetTradesRequest(category=cat_map.get(category, 0))
        )
        return [self._parse_trade(t) for t in reply.trades]

    def get_trade(self, trade_id: str) -> dict:
        """Returns a single trade by ID."""
        self._check_connected()
        reply = self._trades_stub.GetTrade(
            grpc_pb2.GetTradeRequest(trade_id=trade_id)
        )
        return self._parse_trade(reply.trade)

    # ── Trade chat ────────────────────────────────────────────────────────

    def get_chat_messages(self, trade_id: str) -> list:
        """Returns all chat messages for a trade."""
        self._check_connected()
        reply = self._trades_stub.GetChatMessages(
            grpc_pb2.GetChatMessagesRequest(trade_id=trade_id)
        )
        messages = []
        for m in reply.message:
            messages.append({
                "date":              m.date,
                "trade_id":          m.trade_id,
                "message":           m.message,
                "sender_is_trader":  m.sender_is_trader,
                "is_system_message": m.is_system_message,
                "uid":               m.uid,
            })
        return messages

    def check_chat_send_allowed(self, trade_id: str) -> tuple[bool, float]:
        """
        Check whether sending a chat message right now would stay under
        Haveno's 4-messages-per-minute API limit for this trade.

        Returns (allowed, wait_seconds).
            allowed=True  -> safe to send now; wait_seconds is 0.
            allowed=False -> would trip the limit; wait_seconds is the
                             number of seconds until the oldest send in
                             the window expires.
        """
        with self._chat_send_lock:
            now = time.time()
            history = self._chat_send_history.get(trade_id, [])
            # Drop timestamps that already fell out of the window.
            history = [t for t in history if now - t < _CHAT_SEND_WINDOW]
            self._chat_send_history[trade_id] = history
            if len(history) < _CHAT_SEND_LIMIT:
                return True, 0.0
            wait = _CHAT_SEND_WINDOW - (now - history[0]) + 0.5
            return False, max(0.0, wait)

    def send_chat_message(self, trade_id: str, message: str):
        """
        Send a chat message in a trade.

        Raises RuntimeError("CHAT_RATE_LIMIT", wait_seconds) if sending
        would exceed Haveno's 4-messages-per-minute server-side limit.
        Callers should check `check_chat_send_allowed()` first to give
        the user a friendlier warning.
        """
        self._check_connected()
        allowed, wait = self.check_chat_send_allowed(trade_id)
        if not allowed:
            raise RuntimeError(
                f"CHAT_RATE_LIMIT: Haveno's API only allows "
                f"{_CHAT_SEND_LIMIT} chat messages per minute per trade. "
                f"Wait {wait:.0f}s before sending again."
            )
        with self._chat_send_lock:
            self._chat_send_history.setdefault(trade_id, []).append(time.time())
        self._trades_stub.SendChatMessage(
            grpc_pb2.SendChatMessageRequest(
                trade_id=trade_id,
                message=message,
            )
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def edit_offer_extra_info(self, offer_id: str, extra_info: str):
        """
        Updates only the extra_info (description) of an existing offer.
        Preserves all other offer fields to avoid proto default-value overwrite.
        """
        self._check_connected()
        # Fetch current offer state to preserve its fields
        reply = self._offers_stub.GetMyOffer(
            grpc_pb2.GetMyOfferRequest(id=offer_id)
        )
        o = reply.offer
        # When using market-based pricing, price must be empty
        # otherwise Haveno rejects with "fixed price or floating market price but not both"
        price = "" if o.use_market_based_price else o.price
        self._offers_stub.EditOffer(
            grpc_pb2.EditOfferRequest(
                offer_id=offer_id,
                currency_code=o.counter_currency_code,
                price=price,
                use_market_based_price=o.use_market_based_price,
                market_price_margin_pct=o.market_price_margin_pct,
                trigger_price=o.trigger_price,
                payment_account_id=o.payment_account_id,
                extra_info=extra_info,
            )
        )

    @staticmethod
    def _parse_trade(t) -> dict:
        """Converts a protobuf TradeInfo to a plain dict."""
        return {
            "trade_id":              t.trade_id,
            "short_id":              t.short_id,
            "role":                  t.role,
            "state":                 t.state,
            "phase":                 t.phase,
            "amount":                t.amount,
            "price":                 t.price,
            "date":                  t.date,
            "start_time":            t.start_time,
            "is_deposits_published": t.is_deposits_published,
            "is_deposits_confirmed": t.is_deposits_confirmed,
            "is_deposits_unlocked":  t.is_deposits_unlocked,
            "is_payment_sent":       t.is_payment_sent,
            "is_payment_received":   t.is_payment_received,
            "is_completed":          t.is_completed,
            "trade_peer_node_address": t.trade_peer_node_address,
            "offer":                 {
                "id":             t.offer.id if t.offer else "",
                "currency_code":  t.offer.counter_currency_code if t.offer else "",
                "payment_method": t.offer.payment_method_id if t.offer else "",
                "extra_info":     t.offer.extra_info if t.offer else "",
                "challenge":      t.offer.challenge if t.offer else "",
            } if t.offer else {},
        }

    @staticmethod
    def _parse_offer(o) -> dict:
        """Converts a protobuf OfferInfo to a plain dict."""
        return {
            "id":                     o.id,
            "direction":              o.direction,
            "currency_code":          o.counter_currency_code,
            "amount":                 o.amount,
            "min_amount":             o.min_amount,
            "volume":                 o.volume,
            "min_volume":             o.min_volume,
            "price":                  o.price,
            "market_price_margin_pct": o.market_price_margin_pct,
            "payment_method":         o.payment_method_id,
            "payment_account_id":     o.payment_account_id,
            "state":                  o.state,
            "is_activated":           o.is_activated,
            "extra_info":             o.extra_info,
            "trigger_price":          o.trigger_price,
            # Haveno-generated passphrase for buyer_as_taker_without_deposit offers
            "challenge":              o.challenge,
        }

    @staticmethod
    def xmr_to_atomic(xmr: float) -> int:
        """Converts XMR float to atomic units (piconeros)."""
        return int(xmr * 1_000_000_000_000)

    @staticmethod
    def atomic_to_xmr(atomic: int) -> float:
        """Converts atomic units to XMR float."""
        return atomic / 1_000_000_000_000
