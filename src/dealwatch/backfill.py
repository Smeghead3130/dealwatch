"""Durable, bounded wishlist backfill. Network I/O never holds a SQLite transaction."""

import json
import re
from datetime import datetime, timedelta

from lxml import html

from .evidence import LocalHistory
from .judge import judge
from .models import Deal, Decision, Extraction, WishlistItem, utcnow
from .notify import payload
from .wishlist import item_key, saved_items, source_key


async def availability(client, config, deal):
    """Read only the canonical node page, not merchant links or comments."""
    try:
        async with client.stream(
            "GET",
            f"https://www.ozbargain.com.au/node/{deal.node_id}",
            headers={"User-Agent": config.user_agent},
            timeout=10,
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 512_000:
                    return "unknown: page exceeds size limit"
                chunks.append(chunk)
        root = html.fromstring(b"".join(chunks))
        # Restrict status detection to the page title/deal status, never user comments.
        markers = root.xpath('//title/text() | //h1//text() | //span[contains(@class,"title-msg")]//text()')
        if re.search(r"\bexpired\b|out of stock|\boos\b", " ".join(markers), re.I):
            return "unavailable"
        return "page checked; availability not guaranteed"
    except Exception as exc:
        return "unknown: " + type(exc).__name__


def rank(result):
    decision = Decision.model_validate_json(result["decision"])
    strength = {"evidence": 4, "wishlist_target": 3, "wishlist_match": 2, "possible": 1, "none": 0}
    return (-strength[decision.kind], -decision.value_score, decision.effective_price or 0, result["node"])


async def backfill_once(client, db, config, now=None):
    realtime = now is None
    now = now or utcnow()
    batch = db.conn.execute(
        "SELECT * FROM backfill_batches WHERE state='pending' AND next_at<=? ORDER BY id LIMIT 1",
        (now.isoformat(),),
    ).fetchone()
    if not batch:
        return False
    batch = dict(batch)
    try:
        # Removed items or a changed source must not generate delayed notifications.
        current_keys = {item_key(i) for i in saved_items(db, config)}
        items = [WishlistItem.model_validate(i) for i in json.loads(batch["items"])]
        items = (
            [i for i in items if item_key(i) in current_keys] if batch["source"] == source_key(config) else []
        )
        created = datetime.fromisoformat(batch["created"])
        candidates = db.conn.execute(
            "SELECT * FROM deals WHERE first_seen>=? AND first_seen<=? ORDER BY node",
            ((created - timedelta(days=config.backfill_days)).isoformat(), batch["created"]),
        ).fetchall()
        for stored in candidates if items else []:
            deal = Deal.model_validate_json(stored["json"])
            if deal.unavailable(now) or db.already_alerted(deal.node_id):
                continue
            if db.conn.execute(
                "SELECT 1 FROM backfill_results WHERE batch=? AND node=?", (batch["id"], deal.node_id)
            ).fetchone():
                continue
            cached = db.conn.execute(
                "SELECT extraction FROM decisions WHERE node=? AND (revision=? OR revision LIKE ?) AND extraction IS NOT NULL ORDER BY ts DESC LIMIT 1",
                (deal.node_id, stored["revision"], stored["revision"] + ":wishlist_backfill:%"),
            ).fetchone()
            ex = Extraction.model_validate_json(cached[0]) if cached else None
            ex, decision = await judge(client, config, deal, LocalHistory(db), now, items, ex)
            if decision.wishlist_error:
                raise RuntimeError("Wishlist matching failed; retry batch")
            decision.trigger = "wishlist_backfill"
            if not decision.wishlist_applied or not decision.wishlist_matches:
                decision = decision.model_copy(
                    update={"kind": "none", "reason": "No qualifying new wishlist match"}
                )
            if decision.kind != "none":
                decision.availability = await availability(client, config, deal)
                if decision.availability == "unavailable":
                    decision = decision.model_copy(
                        update={"kind": "none", "reason": "Node page marked expired or out of stock"}
                    )
            with db.conn:
                db.conn.execute(
                    "INSERT OR REPLACE INTO backfill_results VALUES(?,?,?,?,?)",
                    (
                        batch["id"],
                        deal.node_id,
                        stored["revision"],
                        ex.model_dump_json() if ex else None,
                        decision.model_dump_json(),
                    ),
                )
        finalize(db, config, batch, utcnow() if realtime else now, bool(items))
    except Exception as exc:
        attempts = batch["attempts"] + 1
        with db.conn:
            db.conn.execute(
                "UPDATE backfill_batches SET attempts=?,state=?,next_at=?,error=? WHERE id=?",
                (
                    attempts,
                    "failed" if attempts >= config.max_job_attempts else "pending",
                    (now + timedelta(seconds=min(1800, 120 * 2**attempts))).isoformat(),
                    type(exc).__name__,
                    batch["id"],
                ),
            )
    return True


def finalize(db, config, batch, now, active=True):
    """Commit decisions, reservations, summary and batch completion atomically."""
    rows = sorted(
        db.conn.execute("SELECT * FROM backfill_results WHERE batch=?", (batch["id"],)).fetchall(), key=rank
    )
    current_keys = (
        {item_key(i) for i in saved_items(db, config)} if batch["source"] == source_key(config) else set()
    )
    count, overflow = 0, []
    with db.conn:
        for row in rows:
            stored = db.deal(row["node"])
            if (
                not active
                or not stored
                or stored["revision"] != row["revision"]
                or db.already_alerted(row["node"])
            ):
                continue
            deal = Deal.model_validate_json(stored["json"])
            decision = Decision.model_validate_json(row["decision"])
            if decision.kind != "none" and not any(
                item_key(m.item) in current_keys for m in decision.wishlist_matches
            ):
                decision = decision.model_copy(
                    update={"kind": "none", "reason": "Wishlist item removed or edited during backfill"}
                )
            if deal.unavailable(now):
                decision = decision.model_copy(update={"kind": "none", "reason": deal.unavailable(now)})
            decision = db.limit_decision(decision, config, now)
            revision = f"{row['revision']}:wishlist_backfill:{batch['id']}"
            if decision.kind != "none":
                if count >= config.backfill_max_alerts:
                    overflow.append((row, deal, decision))
                else:
                    ex = Extraction.model_validate_json(row["extraction"])
                    message = payload(deal, ex, decision, config)
                    if now - deal.published > timedelta(hours=config.stale_hours):
                        message["message"] += (
                            f"\nPosted {max(0, (now - deal.published).days)} days ago — may have ended"
                        )
                    message["message"] += "\nAvailability: " + (decision.availability or "not checked")
                    reserve(db, row["node"], row["revision"], decision.kind, message, config, now)
                    count += 1
            db.conn.execute(
                "INSERT OR IGNORE INTO decisions(node,revision,json,extraction,ts) VALUES(?,?,?,?,?)",
                (row["node"], revision, decision.model_dump_json(), row["extraction"], now.isoformat()),
            )
        if overflow:
            # One summary per sync, with per-item counts, consumes one wishlist/global slot.
            summary_decision = Decision(
                kind="wishlist_match", reason="Backfill summary", wishlist_applied=True
            )
            if db.limit_decision(summary_decision, config, now).kind != "none":
                names = {}
                members = []
                for row, deal, decision in overflow:
                    for match in decision.wishlist_matches:
                        names.setdefault(match.item.text, []).append(deal.deal_url)
                    members.append({"node": deal.node_id, "revision": row["revision"]})
                message = {
                    "title": "More older wishlist matches",
                    "priority": 3,
                    "tags": ["shopping_cart"],
                    "message": "\n".join(
                        f"+{len(links)} more older matches for {name}: {links[0]}"
                        for name, links in names.items()
                    )
                    + "\nOlder deals may have ended; availability is not guaranteed.",
                    "click": overflow[0][1].deal_url,
                    "_members": members,
                }
                reserve(db, 0, "service", f"backfill_summary:{batch['id']}", message, config, now)
                for row, _, decision in overflow:
                    reserve(
                        db,
                        row["node"],
                        row["revision"],
                        decision.kind,
                        {},
                        config,
                        now,
                        state="bundled",
                    )
        db.conn.execute("UPDATE backfill_batches SET state='done',error=NULL WHERE id=?", (batch["id"],))


def reserve(db, node, revision, kind, message, config, now, state=None):
    db.conn.execute(
        "INSERT OR IGNORE INTO outbox(node,revision,kind,payload,state,next_at,created,trigger,wishlist) VALUES(?,?,?,?,?,?,?,?,1)",
        (
            node,
            revision,
            kind,
            json.dumps(message),
            state or ("dry_run" if config.dry_run else "pending"),
            now.isoformat(),
            now.isoformat(),
            "wishlist_backfill",
        ),
    )
