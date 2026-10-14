from .extract import extract
from .models import Decision, utcnow
from .value import decide, excluded, validate_facts
from .wishlist import match_items


async def judge(client, config, deal, history, now=None, wishlist_items=None, stored_extraction=None):
    now = now or utcnow()
    if reason := deal.unavailable(now):
        return None, Decision(kind="none", reason=reason)
    if excluded(deal, config):
        return None, Decision(kind="none", reason="Excluded category")
    if stored_extraction is None:
        ex, model, latency = await extract(client, config, deal)
    else:
        from .extract import normalise_extraction

        ex, model, latency = normalise_extraction(stored_extraction.model_copy(deep=True), deal), "stored", 0
    matches, match_error = [], None
    # Specific-product matches are re-gated with full identity checks in decide().
    if wishlist_items and not validate_facts(deal, ex, allow_accessory=True, require_identity=False):
        try:
            matches, match_ms = await match_items(
                client, config, ex, wishlist_items, deal, getattr(history, "db", None)
            )
            latency += match_ms
        except Exception as exc:
            # A matcher outage must not suppress an otherwise valid non-wishlist steal.
            match_error = type(exc).__name__
    decision = decide(deal, ex, history, config, now, matches)
    decision.wishlist_error = match_error
    decision.model = model
    decision.latency_ms = latency
    return ex, decision
