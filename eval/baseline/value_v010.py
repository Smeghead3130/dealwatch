# Frozen v0.1.0 decision engine (tag v0.1.0); only imports made absolute.
import re
from decimal import Decimal
from statistics import median

from dealwatch.evidence import amounts, norm, quoted_references, supported
from dealwatch.extract import source_text
from dealwatch.models import Decision


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


def validate_facts(deal, ex):
    text = source_text(deal)
    if ex.price is None or ex.currency.upper() != "AUD":
        return "Missing upfront AUD price"
    if not supported(ex.identity_quote, text) or not ex.model:
        return "Product identity lacks source evidence"
    if not all(norm(s) in norm(ex.identity_quote) for s in (ex.brand or "", ex.model)) or not set(
        re.findall(r"\w+", norm(ex.variant))
    ).issubset(set(re.findall(r"\w+", norm(ex.identity_quote)))):
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
    if ex.accessory:
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


def decide(deal, ex, history, config, now):
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
    return Decision(
        kind="none", reason="No qualifying comparable evidence or explicit exceptional-value signal", **common
    )
