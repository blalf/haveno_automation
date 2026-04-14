"""
Bulk offer publisher (V2.1 — password injection only for regular offers).

Per-offer flow
--------------
1. Build the initial extra_info from the preset's description_template,
   replacing {password} with a neutral "..." marker. We don't post the
   raw placeholder so the offer still looks clean if the injection
   step is skipped or fails.
2. Call PostOffer. The Haveno gRPC call is synchronous: it only returns
   once the maker has reserved funds, the arbitrator has signed the
   offer, and it has been added to the offer book. At that point the
   OfferInfo reply contains the server-generated `challenge` passphrase
   for no-deposit offers.
3. If (and only if) the offer is NOT a no-deposit offer AND the
   template contained {password}, call EditOffer to replace the marker
   with the real passphrase. For no-deposit offers we skip EditOffer
   entirely — see "Why we can't inject the password for no-deposit
   offers" below.
4. If EditOffer unexpectedly fails for a regular offer, we:
     - Try to reactivate the offer (EditOffer deactivates it on the
       way in, and if the validation trips, the offer stays paused).
     - Report a non-fatal warning. The offer is still live in the
       offer book with the "..." placeholder.

Why we can't inject the password for no-deposit offers
------------------------------------------------------
Three independent blockers in the current Haveno gRPC API
(verified 2026-04-10 against haveno-dex/haveno master):

  * EditOffer is broken for offers posted with
    buyer_as_taker_without_deposit=true. The server deactivates the
    offer, rebuilds the OfferPayload, and re-runs the full validator
    against the STORED security_deposit_pct — which was set to 0.0
    server-side when the offer was created. The validator then throws
    "securityDeposit must not be less than 0.15 but was 0.0" and the
    offer is left deactivated. Reproduced in daemon logs on
    2026-04-10. The EditOfferRequest proto has no
    security_deposit_pct field so there is no way for the client to
    pass a value that would bypass the check.

  * Cloning via source_offer_id always generates a fresh challenge.
    CreateOfferService.createClonedOffer() in haveno-dex/haveno
    explicitly calls HavenoUtils.generateChallenge() for every cloned
    private offer — the source offer's passphrase is never inherited.
    So there is no way to post a clone with a known, frozen challenge
    baked into its description.

  * PostOfferRequest has no challenge field. The client can't specify
    its own passphrase; Haveno generates it server-side.

Until upstream Haveno fixes EditOffer (or exposes an API to either
override the stored security_deposit_pct during an edit, or to update
ONLY extra_info without re-validating), no-deposit offers will always
require the maker to copy the challenge manually from the Active
Offers tab. That's still a big improvement over the GUI because the
app fetches the challenge for you instead of forcing you to open each
offer by hand.

Historical cancel+clone workaround — also broken
-------------------------------------------------
An earlier version tried PostOffer → CancelOffer →
PostOffer(source_offer_id=...) to re-post with the challenge in the
description. That path crashes with
`IllegalStateException: openoffer with id '...' not found` because
cloneOffer calls getMyOffer(source_offer_id) against the openoffer
list, which no longer contains the source (crash reproduced in daemon
logs on 2026-04-10, just before the EditOffer attempt).
"""

from dataclasses import dataclass
from typing import List, Callable, Optional
from haveno_client import HavenoClient
from presets import Preset
from password_gen import inject_password


# Placeholder we post INITIALLY where the password will go.
# Replaced by EditOffer for regular offers; stays as-is for no-deposit.
_PLACEHOLDER = "..."
_TEMPLATE_TOKEN = "{password}"


@dataclass
class PublishResult:
    preset_name: str
    success: bool
    offer_id: Optional[str] = None
    password: Optional[str] = None       # Haveno-generated challenge, if any
    password_injected: bool = False      # True if EditOffer rewrote the desc
    error: Optional[str] = None          # fatal error (offer not posted)
    warning: Optional[str] = None        # non-fatal (offer posted, some step skipped/failed)


def publish_all(
    client: HavenoClient,
    presets: List[Preset],
    xmr_amount: float,
    on_progress: Optional[Callable[[PublishResult], None]] = None,
) -> List[PublishResult]:
    """
    Publish one offer per preset.

    Args:
        client:       Connected HavenoClient instance.
        presets:      List of enabled presets to publish.
        xmr_amount:   Total XMR amount for each offer (float, e.g. 0.5).
        on_progress:  Optional callback called after each offer attempt.

    Returns:
        List of PublishResult — one per preset.
    """
    results: List[PublishResult] = []
    amount_atomic = HavenoClient.xmr_to_atomic(xmr_amount)

    for preset in presets:
        result = _publish_one(client, preset, amount_atomic)
        results.append(result)
        if on_progress:
            on_progress(result)

    return results


def _publish_one(
    client: HavenoClient,
    preset: Preset,
    amount_atomic: int,
) -> PublishResult:
    """Publish a single preset, optionally injecting the password."""
    min_atomic = HavenoClient.xmr_to_atomic(preset.min_xmr)

    try:
        # Haveno validates security_deposit_pct >= 0.15 even when
        # buyer_as_taker_without_deposit=True (the server then
        # internally overrides the buyer deposit to 0). Clamp upward.
        deposit_pct = preset.security_deposit_pct
        if preset.buyer_as_taker_without_deposit and deposit_pct < 0.15:
            deposit_pct = 0.15

        # UI stores margin as percentage (15 = 15%); API wants a fraction.
        margin_fraction = preset.market_price_margin_pct / 100.0

        template = preset.description_template or ""
        has_placeholder = _TEMPLATE_TOKEN in template
        initial_description = (
            template.replace(_TEMPLATE_TOKEN, _PLACEHOLDER)
            if has_placeholder
            else template
        )

        # ── Step 1: post the offer ─────────────────────────────────────
        # PostOffer blocks until the offer is signed and added to the
        # offer book, so the `challenge` field is already populated in
        # the reply.
        offer = client.post_offer(
            payment_account_id=preset.payment_account_id,
            currency_code=preset.currency_code,
            direction="SELL",
            amount=amount_atomic,
            min_amount=min_atomic,
            market_price_margin_pct=margin_fraction,
            security_deposit_pct=deposit_pct,
            buyer_as_taker_without_deposit=preset.buyer_as_taker_without_deposit,
            extra_info=initial_description,
        )

        offer_id = offer["id"]
        challenge = (offer.get("challenge") or "").strip()
        is_no_deposit = preset.buyer_as_taker_without_deposit

        injected = False
        warning: Optional[str] = None

        # ── Step 2: inject the challenge — REGULAR OFFERS ONLY ─────────
        if has_placeholder and challenge and not is_no_deposit:
            final_description = inject_password(template, challenge)
            try:
                client.edit_offer_extra_info(offer_id, final_description)
                injected = True
            except Exception as edit_err:
                # Non-fatal. The offer is live but still paused by
                # the failed EditOffer — try to wake it back up.
                warning = f"EditOffer failed: {edit_err}"
                try:
                    client.activate_offer(offer_id)
                    warning += " (offer reactivated automatically)"
                except Exception as reactivate_err:
                    warning += f" (reactivate also failed: {reactivate_err})"

        elif has_placeholder and is_no_deposit and challenge:
            # Known limitation — see module docstring.
            warning = (
                "No-deposit offer: password injection is not supported "
                "by Haveno's current API. Copy the password manually "
                "from the Active Offers tab."
            )

        elif has_placeholder and not challenge:
            warning = (
                "Template has {password} but Haveno returned no challenge "
                "(only no-deposit offers get one). Placeholder left as-is."
            )

        return PublishResult(
            preset_name=preset.name,
            success=True,
            offer_id=offer_id,
            password=challenge or None,
            password_injected=injected,
            warning=warning,
        )

    except Exception as e:
        return PublishResult(
            preset_name=preset.name,
            success=False,
            error=str(e),
        )


def cancel_all_my_offers(
    client: HavenoClient,
    on_progress: Optional[Callable[[str, bool, str], None]] = None,
) -> dict:
    """
    Cancel ALL active offers. Useful for cleanup before re-publishing.
    Returns {"cancelled": N, "errors": N}.
    """
    offers = client.get_my_offers()
    cancelled = 0
    errors = 0

    for offer in offers:
        offer_id = offer["id"]
        try:
            client.cancel_offer(offer_id)
            cancelled += 1
            if on_progress:
                on_progress(offer_id, True, "")
        except Exception as e:
            errors += 1
            if on_progress:
                on_progress(offer_id, False, str(e))

    return {"cancelled": cancelled, "errors": errors}
