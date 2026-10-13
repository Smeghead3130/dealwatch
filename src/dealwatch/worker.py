import asyncio
import fcntl
import json
import logging
import signal
from datetime import datetime, timedelta

import httpx

from .evidence import LocalHistory, identity
from .feed import poll_once, retry_delay
from .judge import judge
from .keep_sync import sync_once
from .models import Deal, Decision, utcnow
from .notify import notify_once, payload
from .value import validate_facts
from .wishlist import current_items, source_key

logger = logging.getLogger("dealwatch")


def event(name, **fields):
    logger.info(json.dumps({"event": name, "ts": utcnow().isoformat(), **fields}, ensure_ascii=False))


async def judge_once(client, db, config, now=None):
    now = now or utcnow()
    job = db.claim(now)
    if not job:
        return False
    stored = db.deal(job["node"])
    deal = Deal.model_validate_json(stored["json"])
    try:
        if now - datetime.fromisoformat(stored["first_seen"]) > timedelta(hours=config.stale_hours):
            ex, decision = None, Decision(kind="none", reason="Stale before judgement")
        else:
            items = await current_items(db, config)
            ex, decision = await judge(client, config, deal, LocalHistory(db), now, items)
        message = payload(deal, ex, decision, config) if ex and decision.kind != "none" else None
        valid = ex and validate_facts(deal, ex) is None
        stored_decision = db.complete(
            job,
            deal,
            ex,
            decision,
            identity(ex) if valid else None,
            ex.price + (ex.shipping or 0) if valid else None,
            message,
            config,
            now=utcnow(),
        )
        db.set_meta("last_judge", utcnow().isoformat())
        event(
            "judged",
            node=deal.node_id,
            title=deal.title,
            decision=(
                stored_decision or Decision(kind="none", reason="Superseded during inference")
            ).model_dump(mode="json"),
            dry_run=config.dry_run,
        )
    except Exception as exc:
        db.fail_job(job, type(exc).__name__, config.max_job_attempts)
        event("judge_failed", node=deal.node_id, error=type(exc).__name__)
    return True


async def run(db, config):
    lock = (config.data_dir / "service.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another Dealwatch service is running") from None
    db.recover()
    db.topic(config.ntfy_topic)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def wait(seconds):
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async with httpx.AsyncClient(follow_redirects=False) as client:

        async def poller():
            failures = 0
            while not stop.is_set():
                delay = config.poll_seconds
                try:
                    n = await poll_once(client, db, config)
                    failures = 0
                    db.set_meta("feed_error", "")
                    event("feed_poll", items=n)
                except Exception as exc:
                    failures += 1
                    headers = exc.response.headers if isinstance(exc, httpx.HTTPStatusError) else {}
                    delay = retry_delay(headers, failures, utcnow())
                    db.set_meta("feed_error", type(exc).__name__)
                    event("feed_failed", error=type(exc).__name__, retry_seconds=delay)
                await wait(delay)

        async def judging():
            while not stop.is_set():
                if not await judge_once(client, db, config):
                    await wait(1)

        async def notifying():
            while not stop.is_set():
                if await notify_once(client, db, config):
                    event("notification_processed")
                await wait(1)

        async def maintenance():
            last_prune = None
            while not stop.is_set():
                now = utcnow()
                (config.data_dir / "heartbeat").write_text(str(now.timestamp()))
                db.release_upcoming(now)
                db.maybe_keep_notice(source_key(config), config, now)
                if last_prune != now.date():
                    db.prune(now)
                    last_prune = now.date()
                await wait(15)

        async def wishlist_sync():
            while not stop.is_set():
                await wait(await sync_once(db, config))

        tasks = [asyncio.create_task(f()) for f in (poller, judging, notifying, maintenance, wishlist_sync)]
        stopped = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stopped:
                    task.result()
        finally:
            stop.set()
            for task in [*tasks, stopped]:
                task.cancel()
            await asyncio.gather(*tasks, stopped, return_exceptions=True)
            lock.close()
