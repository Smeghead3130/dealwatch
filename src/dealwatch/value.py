import re
from datetime import timedelta
from decimal import Decimal
from statistics import median

from .evidence import amounts, norm, quoted_references, supported
from .extract import source_text
from .models import Decision


def excluded(deal, config):
    categories = {norm(c) for c in deal.categories}
    wanted = {norm(c) for c in config.exclude_categories.split(",")}
    if categories & wanted:
        return True
    return bool(
        re.search(
            r"credit card|bank (?:account|signup)|\b(?:mobile|internet|nbn) plan|"
            r"\bsubscription\b|\b(?:gambling|sportsbet)\b",
            deal.title,
            re.I,
        )
    )


def validate_facts(deal, ex, allow_accessory=False, require_identity=True):
    text = source_text(deal)
    if ex.price is None or ex.currency.upper() != "AUD":
        return "Missing upfront AUD price"
    if require_identity and (not supported(ex.identity_quote, text) or not ex.model):
        return "Product identity lacks source evidence"
    from .wishlist import tokens

    if require_identity and not tokens(" ".join(filter(None, [ex.brand, ex.model, ex.variant]))) <= tokens(
        ex.identity_quote
    ):
        return "Product identity not supported by quote"
    if not supported(ex.price_quote, text) or ex.price not in amounts(ex.price_quote):
        # A literal free item is the sole nonnumeric price exception.
        if not (
            ex.price == 0 and supported(ex.price_quote, text) and re.search(r"\bfree\b", ex.price_quote, re.I)
        ):
            return "Price lacks source evidence"
    if re.search(r"after\s+(?:\$[\d.]+\s+)?cashback|after cashback|net of cashback", ex.price_quote, re.I):
        return "Cashback-net price cannot be used as upfront price"
    if ex.shipping is not None:
        if not supported(ex.shipping_quote, text):
            return "Delivery lacks source evidence"
        if ex.shipping not in amounts(ex.shipping_quote) and not (
            ex.shipping == 0
            and re.search(r"delivered|free (?:shipping|delivery)|shipping included", ex.shipping_quote, re.I)
        ):
            return "Delivery amount lacks source evidence"
    if ex.deposit or ex.subscription or re.search(r"\bdeposit\b|\bsubscription\b", deal.title, re.I):
        return "Deposit or recurring subscription"
    if ex.accessory and not allow_accessory:
        return "Accessory excluded from v1 steal alerts"
    if ex.condition == "unknown":
        return "Unknown condition"
    return None


def restrictions(ex):
    result = []
    for label, value in (
        ("Membership", ex.membership),
        ("Coupon", ex.coupon),
        ("Cashback (not deducted)", ex.cashback),
    ):
        if value:
            result.append(f"{label}: {value}")
    result.append(
        "Delivery unknown; price excludes delivery"
        if ex.shipping is None
        else f"Delivery included: A${ex.shipping:.2f}"
    )
    if ex.condition != "new":
        result.append(f"Condition: {ex.condition}")
    if ex.imported:
        result.append("Imported/grey stock")
    if ex.marketplace:
        result.append("Marketplace third-party seller")
    if ex.bundle:
        result.append("Bundle: " + ", ".join(ex.bundle))
    return result


def base_decide(deal, ex, history, config, now):
    if reason := deal.unavailable(now):
        return Decision(kind="none", reason=reason)
    if excluded(deal, config):
        return Decision(kind="none", reason="Excluded category")
    if reason := validate_facts(deal, ex):
        return Decision(kind="none", reason=reason)
    effective = ex.price + (ex.shipping or Decimal(0))
    common = {"effective_price": effective, "restrictions": restrictions(ex)}
    observations = history.observations(ex, deal, now, config)
    if len(observations) >= config.min_observations:
        baseline = median(p for p, _ in observations)
        if baseline > 0 and effective <= baseline * Decimal(str(config.history_ratio)):
            pct = (1 - effective / baseline) * 100
            return Decision(
                kind="evidence",
                reason=f"{pct:.1f}% below A${baseline:.2f} median of "
                f"{len(observations)} comparable observed OzBargain prices; historical, not current market",
                value_score=pct,
                observations=len(observations),
                sources=[s for _, s in observations],
                **common,
            )
    for ref in quoted_references(ex, deal):
        # Compare known landed price conservatively against quoted item price. Unknown delivery is disclosed.
        if effective <= ref.price * Decimal(str(config.quoted_ratio)):
            pct = (1 - effective / ref.price) * 100
            return Decision(
                kind="evidence",
                reason=f"{pct:.1f}% below A${ref.price:.2f} at {ref.source} "
                "as quoted by the post (not independently checked); 1 quoted comparison",
                value_score=pct,
                observations=1,
                sources=[deal.deal_url + " — " + ref.quote],
                **common,
            )
    patterns = {
        "price_error": r"price error|pricing error|mispric|glitch",
        "clearance": r"\bclearance\b",
        "lowest_ever": r"lowest (?:ever|price)|all.time low",
        "free_tiny_cost": r"\bfree\b",
    }
    if ex.signal in patterns and supported(ex.signal_quote, source_text(deal)):
        valid = bool(re.search(patterns[ex.signal], ex.signal_quote, re.I))
        if ex.signal == "clearance":
            # "Clearance" alone produced noise (Pixel 10 at $797, iPhone Air at $999); clearance
            # deals still alert through the was-price, vote and history paths below.
            valid = False
        if ex.signal == "lowest_ever":
            valid = valid and bool(quoted_references(ex, deal))
        if ex.signal == "free_tiny_cost":
            valid = valid and ex.price == 0 and ex.shipping is not None and ex.shipping <= 10
        if valid:
            return Decision(
                kind="possible",
                reason="Possible — unverified: " + ex.signal_quote,
                sources=[deal.deal_url],
                **common,
            )
    if was := seller_was(deal):
        if effective <= was * Decimal("0.75"):
            return Decision(
                kind="possible",
                reason=f"Possible — seller's claimed was-price, unverified: A${was:.2f}",
                value_score=(1 - effective / was) * 100,
                sources=[deal.deal_url],
                **common,
            )
    age = now - deal.published
    if 0 <= age.total_seconds() <= 7200 and deal.votes_pos - deal.votes_neg >= 40:
        return Decision(
            kind="possible",
            reason=f"Possible — community signal, unverified: {deal.votes_pos - deal.votes_neg} net votes within 2 hours",
            sources=[deal.deal_url],
            **common,
        )
    return Decision(
        kind="none", reason="No qualifying comparable evidence or explicit exceptional-value signal", **common
    )


def decide(deal, ex, history, config, now, matches=None):
    """Leave the original decision path untouched unless a verified wishlist match applies."""
    description_matches = [m for m in (matches or []) if m.fit is not None]
    if description_matches:
        description = description_decide(deal, ex, history, config, now, description_matches)
        if description.kind != "none":
            return description
    matches = [m for m in (matches or []) if m.fit is None]
    if not matches or deal.unavailable(now) or excluded(deal, config) or validate_facts(deal, ex):
        return base_decide(deal, ex, history, config, now)
    effective = ex.price + (ex.shipping or Decimal(0))
    eligible = []
    for match in matches:
        item = match.item
        if item.target is None:
            eligible.append(match)
        elif ex.shipping is not None and (
            effective < item.target if item.strict_target else effective <= item.target
        ):
            eligible.append(match)
    targets = [m for m in eligible if m.item.target is not None]
    if targets:
        item = targets[0].item
        return Decision(
            kind="wishlist_target",
            value_score=(1 - effective / item.target) * 100,
            reason=f"Wishlist target hit: A${effective:.2f} "
            f"{'<' if item.strict_target else '<='} A${item.target:.2f}. This is a target hit, not a steal claim.",
            effective_price=effective,
            restrictions=restrictions(ex),
            sources=[deal.deal_url],
            wishlist_matches=matches,
            wishlist_applied=True,
        )
    adjusted = (
        config.model_copy(
            update={
                "history_ratio": config.wishlist_history_ratio,
                "quoted_ratio": config.wishlist_quoted_ratio,
            }
        )
        if eligible
        else config
    )
    result = base_decide(deal, ex, history, adjusted, now)
    result.wishlist_matches = matches
    result.wishlist_applied = bool(eligible)
    return result


def seller_was(deal):
    """Only an explicit seller title was-price, never RRP or an unrelated summary amount."""
    title = source_text(deal.model_copy(update={"summary": ""}))
    if not re.search(r"@\s*\w", title) or re.search(r"\brrp|msrp|recommended retail\b", title, re.I):
        return None
    values = re.findall(r"\bwas\s+(?:AUD\s*)?\$([\d,]+(?:\.\d{1,2})?)(?![\d.])", title, re.I)
    if len(values) != 1:
        return None
    return Decimal(values[0].replace(",", ""))


def description_decide(deal, ex, history, config, now, matches):
    # Identity is proven per requirement by the quoted fit check, not by brand/model tokens.
    if (
        deal.unavailable(now)
        or excluded(deal, config)
        or validate_facts(deal, ex, allow_accessory=True, require_identity=False)
    ):
        return Decision(kind="none", reason="Description failed deal safety gates")
    effective = ex.price + (ex.shipping or Decimal(0))
    signals, scores = [], []
    ratio = Decimal(1) - Decimal(str(config.wishlist_desc_min_discount))
    observations = history.observations(ex, deal, now, config)
    if len(observations) >= config.min_observations:
        baseline = median(p for p, _ in observations)
        if baseline > 0 and effective <= baseline * ratio:
            scores.append((1 - effective / baseline) * 100)
            signals.append(
                f"{(1 - effective / baseline) * 100:.1f}% below comparable historical median A${baseline:.2f}"
            )
    for ref in quoted_references(ex, deal):
        if effective <= ref.price * ratio:
            scores.append((1 - effective / ref.price) * 100)
            signals.append(
                f"{(1 - effective / ref.price) * 100:.1f}% below {ref.source} comparison quoted in post, not independently checked"
            )
    was = seller_was(deal)
    if was and was > 0 and effective <= was * ratio:
        scores.append((1 - effective / was) * 100)
        signals.append(
            f"{(1 - effective / was) * 100:.1f}% below seller's claimed was-price A${was:.2f}, unverified"
        )
    age = now - deal.published
    if (
        timedelta(0) <= age <= timedelta(hours=6)
        and deal.votes_pos - deal.votes_neg >= config.wishlist_desc_min_votes
    ):
        signals.append(
            f"{deal.votes_pos - deal.votes_neg} net votes within 6 hours; community signal, not price evidence"
        )
    # Prefer complete fits over partial matches regardless of wishlist ordering.
    for match in sorted(matches, key=lambda m: m.fit.overall != "fits"):
        if ex.accessory and not match.item.interpretation.accessory_need:
            continue
        if any(row.status == "not_met" for row in match.fit.requirements):
            continue
        item = match.item
        target = (
            item.target is not None
            and ex.shipping is not None
            and (effective < item.target if item.strict_target else effective <= item.target)
        )
        value_signals = (
            [f"Within wishlist target A${item.target:.2f} including delivery"] if target else []
        ) + signals
        if not value_signals:
            continue
        partial = match.fit.overall == "partial"
        return Decision(
            kind="possible" if partial else "wishlist_match",
            value_score=max(scores + [(1 - effective / item.target) * 100 if target else Decimal(0)]),
            reason=("Possible wishlist fit — unknown requirements; " if partial else "Wishlist match: ")
            + "; ".join(value_signals),
            effective_price=effective,
            restrictions=restrictions(ex),
            sources=[deal.deal_url],
            wishlist_matches=[match],
            wishlist_applied=True,
        )
    return Decision(kind="none", reason="No description match with value signal")
