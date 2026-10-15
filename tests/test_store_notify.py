import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from dealwatch.db import Store
from dealwatch.evidence import identity
from dealwatch.models import Decision
from dealwatch.notify import notify_once, payload, quiet_priority


def finish(db, config, deal, ex, now, kind="possible"):
    db.ingest([deal], now)
    job = db.claim(now)
    d = Decision(kind=kind, reason="test evidence", effective_price=100)
    msg = payload(deal, ex, d, config)
    db.complete(job, deal, ex, d, identity(ex), 100, msg, config, now)
    return job


def test_crash_recovery_job_exactly_once(db, deal, now):
    db.ingest([deal], now)
    job = db.claim(now)
    assert not db.claim(now)
    db.recover()
    recovered = db.claim(now)
    assert job["id"] == recovered["id"]
    db.ingest([deal], now)
    assert db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_atomic_decision_outbox_failure(db, config, deal, extraction, now):
    db.conn.execute(
        "CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'crash'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        finish(db, config, deal, extraction, now)
    assert db.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    db.recover()
    assert db.claim(now)


async def test_notify_restart_dedupe(db, config, deal, extraction, now):
    finish(db, config, deal, extraction, now)
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"id": "sent"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await notify_once(client, db, config, now)
        db.recover()
        db.ingest([deal], now)
        assert not await notify_once(client, db, config, now)
    assert len(calls) == 1 and calls[0]["topic"].startswith("dealwatch-")


@pytest.mark.parametrize("change", ["stale", "expired", "revised"])
async def test_suppress_before_send(db, config, deal, extraction, now, change):
    finish(db, config, deal, extraction, now)
    sent_at = now
    if change == "stale":
        sent_at += timedelta(hours=7)
    else:
        if change == "expired":
            deal.status = "expired"
        else:
            deal.title += " Changed terms"
        db.ingest([deal], now)

    def no_network(req):
        pytest.fail("Suppressed alert must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
        await notify_once(client, db, config, sent_at)
    assert db.conn.execute("SELECT state FROM outbox").fetchone()[0] == "suppressed"


def test_upcoming_release_without_new_revision(db, deal, now):
    deal.status, deal.starting = "upcoming", now + timedelta(hours=1)
    db.ingest([deal], now)
    assert not db.claim(now)
    db.release_upcoming(now + timedelta(hours=2))
    assert db.claim(now + timedelta(hours=2))


def test_future_schema_refused(tmp_path):
    path = tmp_path / "future.db"
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version=999")
    c.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)


def test_daily_cap_and_dry_run(db, config, deal, extraction, now):
    config.possible_daily_limit = 1
    config.dry_run = True
    finish(db, config, deal, extraction, now)
    finish(db, config, deal.model_copy(update={"node_id": 124}), extraction, now)
    assert db.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    assert db.conn.execute("SELECT state FROM outbox").fetchone()[0] == "dry_run"


def test_quiet_hours(config, now):
    config.quiet_hours = "22:00-07:00"
    assert quiet_priority(4, config, now) == 2  # Brisbane midnight
    assert quiet_priority(4, config, now + timedelta(hours=12)) == 4


def test_alert_tiers_and_product_name(deal, extraction, config):
    from decimal import Decimal

    from dealwatch.models import Decision
    from dealwatch.notify import payload, product_name

    ex = extraction.model_copy(
        update={
            "brand": "Amazon Basics",
            "model": "Amazon Basics microSDXC 256GB",
            "variant": "microSDXC 256GB",
        }
    )
    assert product_name(ex) == "Amazon Basics microSDXC 256GB"

    def alert(kind, reason, score, signal="none", restrictions=()):
        e = extraction.model_copy(update={"signal": signal})
        d = Decision(
            kind=kind,
            reason=reason,
            effective_price=Decimal(29),
            value_score=Decimal(score),
            restrictions=list(restrictions),
        )
        return payload(deal, e, d, config)

    assert (
        alert("evidence", "35% below comparable historical median A$45.00", 35)["title"]
        == "Good deal · $29 (35% off)"
    )
    assert alert("evidence", "75% below history", 75)["priority"] == 5
    assert alert("possible", "price error", 10, "price_error")["title"].startswith("Likely price error · $29")
    # Unverified seller was-prices never earn Good deal on their own.
    m = alert(
        "wishlist_match",
        "Wishlist match: 50.0% below seller's claimed was-price A$58.00, unverified",
        50,
        restrictions=["Delivery included: A$0.00", "Bundle: a, b, c", "Membership: Prime"],
    )
    assert m["title"] == "Wishlist sale · $29 (50% off)"
    assert m["message"].splitlines()[1] == "Was $58 (shop's claim) · Needs Prime"
    assert "Bundle" not in m["message"] and "A$0.00" not in m["message"]
