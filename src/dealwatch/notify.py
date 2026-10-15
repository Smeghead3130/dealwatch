import json
import logging
import re
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
    """Alert label from deterministic decision facts only (no model judgement)."""
    weak = decision.kind == "possible" or any(w in decision.reason.lower() for w in WEAK_EVIDENCE)
    score = decision.value_score or Decimal(0)
    if ex.signal == "price_error" or score >= 70:
        return "Likely price error", 5, "rotating_light"
    if decision.kind == "evidence" or (score >= 40 and not weak):
        return "Good deal", 4, "fire"
    if decision.kind == "wishlist_target":
        return "Target price hit", 4, "dart"
    if decision.kind == "wishlist_match":
        return "Wishlist sale", 3, "shopping_cart"
    return "Possible deal", 3, "mag"


def money(value):
    value = Decimal(value)
    return f"${value:,.0f}" if value == value.to_integral() else f"${value:,.2f}"


BASIS = [
    (
        re.compile(r"seller's claimed was-price A\$([\d,.]+)"),
        lambda m: f"Was {money(m[1].replace(',', ''))} (shop's claim)",
    ),
    (
        re.compile(r"historical median A\$([\d,.]+)"),
        lambda m: f"Usually about {money(m[1].replace(',', ''))}",
    ),
    (re.compile(r"below (.+?) comparison quoted in post"), lambda m: f"Cheaper than {m[1]} (per post)"),
    (re.compile(r"(\d+) net votes"), lambda m: f"{m[1]} upvotes in its first hours"),
    (
        re.compile(r"[<>=]+ A\$([\d,.]+)\. This is a target hit"),
        lambda m: f"Your target: {money(m[1].replace(',', ''))}",
    ),
    (re.compile(r"clearance", re.I), lambda m: "On clearance"),
    (re.compile(r"price error", re.I), lambda m: "Possible pricing mistake"),
]


def basis(reason):
    found = []
    for pattern, render in BASIS:
        if (m := pattern.search(reason)) and (text := render(m)) not in found:
            found.append(text)
    return found[:2]


def catches(restrictions):
    """Only the conditions that change whether Mat can buy at this price; at most two."""
    out = []
    for r in restrictions:
        key, _, value = r.partition(": ")
        if key == "Membership":
            out.append("Needs " + value)
        elif key == "Coupon":
            out.append("Code " + value)
        elif key.startswith("Cashback"):
            out.append("Price before cashback")
        elif key == "Delivery unknown; price excludes delivery":
            out.append("Plus delivery")
        elif key == "Delivery included" and value not in ("A$0.00", "A$0"):
            out.append(value.replace("A$", "$") + " delivery")
        elif key == "Marketplace third-party seller":
            out.append("Marketplace seller")
        elif key in ("Condition", "Imported stock"):
            out.append(r)
    return out[:2]


def payload(deal, ex, decision, config):
    label, priority, tag = tier(ex, decision)
    saving = (
        f" ({decision.value_score:.0f}% off)" if decision.value_score and decision.value_score >= 1 else ""
    )
    lines = [product_name(ex)[:80]]
    detail = basis(decision.reason)
    if decision.kind == "possible":
        detail.append("Unverified")
    lines.append(" · ".join(detail + catches(decision.restrictions)))
    unconfirmed = [
        req.text
        for m in decision.wishlist_matches
        if m.fit
        for fit, req in zip(m.fit.requirements, m.item.interpretation.requirements, strict=True)
        if req.hard and fit.status == "unknown"
    ]
    if unconfirmed:
        lines.append("Not confirmed: " + ", ".join(unconfirmed[:2]))
    wishlist = [m.item.text for m in decision.wishlist_matches]
    if wishlist:
        lines.append("On your wishlist: " + ", ".join(wishlist))
    result = {
        "title": f"{label} · {money(decision.effective_price)}{saving}",
        "message": "\n".join(line for line in lines if line)[:3900],
        "priority": priority,
        "tags": [tag],
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
