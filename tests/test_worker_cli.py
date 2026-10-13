import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from dealwatch import worker
from dealwatch.__main__ import command
from dealwatch.feed import poll_once
from dealwatch.models import Decision
from dealwatch.notify import notify_once, publish


async def test_poll_continues_during_slow_judge(db, config, deal, extraction, now, feed_bytes, monkeypatch):
    db.ingest([deal], now)
    started, unblock = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        started.set()
        await unblock.wait()
        return extraction, Decision(kind="none", reason="test", effective_price=100)

    monkeypatch.setattr(worker, "judge", slow)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=feed_bytes))
    ) as client:
        task = asyncio.create_task(worker.judge_once(client, db, config, now))
        await started.wait()
        assert await asyncio.wait_for(poll_once(client, db, config, now), timeout=1) == 30
        assert not task.done()
        unblock.set()
        await task


async def test_cancel_mid_judge_recovers_job(db, config, deal, now, monkeypatch):
    db.ingest([deal], now)
    started = asyncio.Event()

    async def stalled(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "judge", stalled)
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(worker.judge_once(client, db, config, now))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    db.recover()
    assert db.claim(now)["node"] == deal.node_id
    assert not db.claim(now)


async def test_replay_never_modifies_delivery_history(db, config, deal, now, monkeypatch, capsys):
    from dealwatch import __main__ as cli

    db.ingest([deal], now)
    original = db.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    async def fake(*args):
        return None, Decision(kind="possible", reason="replay")

    monkeypatch.setattr(cli, "judge", fake)
    await command(argparse.Namespace(cmd="replay", hours=87600), config)
    assert db.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == original
    assert '"dry_run": true' in capsys.readouterr().out


async def test_ntfy_failure_retries_then_sends(db, config, deal, extraction, now):
    from datetime import timedelta

    from test_store_notify import finish

    finish(db, config, deal, extraction, now)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(503 if len(calls) == 1 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await notify_once(client, db, config, now)
        assert db.conn.execute("SELECT state FROM outbox").fetchone()[0] == "pending"
        await notify_once(client, db, config, now + timedelta(seconds=31))
    assert db.conn.execute("SELECT state FROM outbox").fetchone()[0] == "sent"


async def test_basic_and_token_auth(config):
    from pydantic import SecretStr

    requests = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: requests.append(req) or httpx.Response(200))
    ) as client:
        config.ntfy_username, config.ntfy_password = SecretStr("user"), SecretStr("password")
        await publish(client, config, "test", {"message": "one"})
        assert requests[-1].headers["Authorization"].startswith("Basic ")
        config.ntfy_token = SecretStr("token")
        await publish(client, config, "test", {"message": "two"})
        assert requests[-1].headers["Authorization"] == "Bearer token"


def test_eval_dataset_is_partitioned():
    cases = [json.loads(x) for x in Path("eval/cases.jsonl").read_text().splitlines()]
    assert len(cases) >= 40
    assert sum(c["split"] == "heldout" for c in cases) >= 10
    assert len({c["id"] for c in cases}) == len(cases)


@pytest.mark.parametrize("price,expected", [(Decimal("210"), "evidence"), (Decimal("210.01"), "none")])
def test_decimal_threshold_boundary(deal, extraction, config, now, price, expected):
    from dealwatch.__main__ import CaseHistory
    from dealwatch.value import decide

    deal.title = f"Acme X1 8TB HDD ${price} Delivered"
    ex = extraction.model_copy(update={"price": price, "price_quote": f"${price}", "signal": "none"})
    assert decide(deal, ex, CaseHistory([300] * 3), config, now).kind == expected
