import json
import logging
import re
from datetime import datetime, timedelta
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


def payload(deal, ex, decision, config):
    title = " ".join(filter(None, [ex.brand, ex.model, ex.variant]))
    parts = [
        decision.kind + ("-backed steal" if decision.kind == "evidence" else " — unverified"),
        decision.reason,
    ]
    parts.extend(decision.restrictions)
    parts.extend(decision.sources[:3])
    wishlist = config.data_dir / "wishlist.txt"
    if wishlist.exists():
        # Optional relevance annotation only; never involved in the alert decision.
        product_words = set(re.findall(r"[a-z0-9]+", title.casefold()))
        relevant = []
        for line in wishlist.read_text()[:16000].splitlines():
            if line.strip() and not line.lstrip().startswith(("#", "//", "~")):
                words = set(re.findall(r"[a-z0-9]+", line.casefold()))
                if len(words & product_words) >= 2:
                    relevant.append(line.strip())
        if relevant:
            parts.append("Wishlist relevance (text overlap): " + "; ".join(relevant[:3]))
    result = {
        "title": f"{title} — A${decision.effective_price:.2f}"[:220],
        "message": "\n".join(parts)[:3900],
        "priority": 4 if decision.kind == "evidence" else 3,
        "tags": ["shopping_cart"],
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
    if not stored:
        reason = "Deal no longer stored"
    else:
        deal = Deal.model_validate_json(stored["json"])
        reason = deal.unavailable(now)
        if stored["revision"] != row["revision"]:
            reason = "Offer revised after judgement"
        if now - datetime.fromisoformat(stored["first_seen"]) > timedelta(hours=config.stale_hours):
            reason = "Stale alert"
    if reason:
        db.notification_done(row, "suppressed", reason, now)
        notification_log(row, "suppressed", reason)
        return True
    if config.dry_run:
        db.notification_done(row, "dry_run", "DRY_RUN enabled", now)
        notification_log(row, "dry_run", "DRY_RUN enabled")
        return True
    message = json.loads(row["payload"])
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
