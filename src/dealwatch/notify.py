import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .models import Deal, utcnow


def notification_log(row, state, reason=None):
    logging.getLogger("dealwatch").info(
        json.dumps(
            {
                "event": "notification",
                "node": row["node"],
                "state": state,
                "reason": reason,
                "ts": utcnow().isoformat(),
            }
        )
    )


def safe_url(value):
    if not value or any(c in value for c in "\r\n"):
        return None
    try:
        p = urlsplit(value)
        return value if p.scheme in ("http", "https") and p.hostname and not p.username else None
    except ValueError:
        return None


WEAK_EVIDENCE = ("unverified", "community signal", "possible")


def product_name(ex):
    """Brand, model and variant without the repeats extraction sometimes produces."""
    parts = []
    for part in (ex.brand, ex.model, ex.variant):
        part = (part or "").strip()
        if part and not any(part.lower() in kept.lower() for kept in parts):
            parts = [kept for kept in parts if kept.lower() not in part.lower()] + [part]
    return " ".join(parts)


def tier(ex, decision):
    """Mat's three alert labels, from deterministic decision facts only (no model judgement)."""
    weak = decision.kind == "possible" or any(w in decision.reason.lower() for w in WEAK_EVIDENCE)
    score = decision.value_score or Decimal(0)
    if ex.signal == "price_error" or score >= 70:
        return "Likely price error", "🚨", 5, "rotating_light"
    if decision.kind == "evidence" or (score >= 40 and not weak):
        return "Good deal", "🔥", 4, "fire"
    if decision.kind == "wishlist_target":
        return "Wishlist target hit", "🎯", 4, "dart"
    if decision.kind == "wishlist_match":
        return "Wishlist sale", "🛒", 3, "shopping_cart"
    return "Good deal", "🔥", 3, "fire"


def payload(deal, ex, decision, config):
    label, emoji, priority, tag = tier(ex, decision)
    name = product_name(ex)
    saving = (
        f" ({decision.value_score:.0f}% off)" if decision.value_score and decision.value_score > 0 else ""
    )
    reason = decision.reason.removeprefix("Wishlist match: ").removeprefix("Possible — ")
    parts = ["Unverified — check before buying."] if decision.kind == "possible" else []
    parts.append("Why: " + reason)
    if decision.restrictions:
        parts.append("Watch out: " + "; ".join(decision.restrictions))
    wishlist = [m.item.text for m in decision.wishlist_matches]
    if wishlist:
        parts.append("On your wishlist: " + ", ".join(wishlist))
    for match in decision.wishlist_matches:
        if match.fit:
            for result, requirement in zip(
                match.fit.requirements, match.item.interpretation.requirements, strict=True
            ):
                marker = {"met": "✓", "unknown": "?", "not_met": "✗"}[result.status]
                detail = result.quote if requirement.hard else result.judgement + " — model's judgement"
                parts.append(f"{marker} {requirement.text}: {detail or 'unknown'}")
    parts.extend(decision.sources[:3])
    result = {
        "title": f"{emoji} {label}: {name} — A${decision.effective_price:.2f}{saving}"[:220],
        "message": "\n".join(parts)[:3900],
        "priority": priority,
        "tags": [tag] + (["star"] if wishlist and label != "Wishlist sale" else []),
    }
    if url := safe_url(deal.deal_url):
        result["click"] = url
    if url := safe_url(deal.merchant_url):
        result["actions"] = [{"action": "view", "label": "Open store", "url": url}]
    return result


def quiet_priority(priority, config, now):
    if not config.quiet_hours:
        return priority
    start, end = config.quiet_hours.split("-")
    current = now.astimezone(ZoneInfo(config.tz)).strftime("%H:%M")
    quiet = start <= current < end if start < end else current >= start or current < end
    return 2 if quiet else priority


async def publish(client, config, topic, message):
    headers = {}
    auth = None
    if token := config.ntfy_token.get_secret_value():
        headers["Authorization"] = "Bearer " + token
    elif config.ntfy_username.get_secret_value():
        auth = (config.ntfy_username.get_secret_value(), config.ntfy_password.get_secret_value())
    response = await client.post(
        config.ntfy_url.rstrip("/"), json={**message, "topic": topic}, headers=headers, auth=auth, timeout=30
    )
    response.raise_for_status()


async def notify_once(client, db, config, now=None):
    now = now or utcnow()
    row = db.claim_notification(now)
    if not row:
        return False
    stored = db.deal(row["node"])
    reason = None
    if row["node"] == 0 and row["kind"].startswith("backfill_summary:"):
        members = json.loads(row["payload"]).get("_members", [])
        if not members or any(
            not (member := db.deal(m["node"]))
            or member["revision"] != m["revision"]
            or Deal.model_validate_json(member["json"]).unavailable(now)
            for m in members
        ):
            reason = "Summary contains unavailable or revised deals"
    elif row["node"] == 0 and row["kind"].startswith("keep_"):
        pass  # Service notices are independent of deal expiry/revisions.
    elif not stored:
        reason = "Deal no longer stored"
    else:
        deal = Deal.model_validate_json(stored["json"])
        reason = deal.unavailable(now)
        if stored["revision"] != row["revision"]:
            reason = "Offer revised after judgement"
        if row.get("trigger", "feed") != "wishlist_backfill" and now - datetime.fromisoformat(
            stored["first_seen"]
        ) > timedelta(hours=config.stale_hours):
            reason = "Stale alert"
    if row.get("trigger") == "wishlist_backfill" and now - datetime.fromisoformat(row["created"]) > timedelta(
        hours=config.stale_hours
    ):
        reason = "Backfill delivery retry window expired"
    if reason:
        db.notification_done(row, "suppressed", reason, now)
        notification_log(row, "suppressed", reason)
        return True
    if config.dry_run:
        db.notification_done(row, "dry_run", "DRY_RUN enabled", now)
        notification_log(row, "dry_run", "DRY_RUN enabled")
        return True
    message = json.loads(row["payload"])
    message.pop("_members", None)
    message["priority"] = quiet_priority(message["priority"], config, now)
    try:
        await publish(client, config, db.topic(config.ntfy_topic), message)
        db.notification_done(row, "sent", now=now)
        notification_log(row, "sent")
    except Exception as exc:
        reason = type(exc).__name__
        if hasattr(exc, "response"):
            reason += f" HTTP {exc.response.status_code}"
        db.retry_notification(row, reason, now)
        notification_log(row, "retry", reason)
    return True
