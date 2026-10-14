import asyncio
import json
import secrets
import sqlite3
from datetime import timedelta

from .feed import revision_hash
from .models import Deal, utcnow

SCHEMA_VERSION = 3


class Store:
    def __init__(self, path):
        self.interpretation_lock = asyncio.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self.conn.close()
            raise RuntimeError("Database schema is newer than this application; refusing rollback")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        if version == 0:
            self.conn.executescript("""
            BEGIN;
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE deals(node INTEGER PRIMARY KEY, json TEXT NOT NULL, revision TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
            CREATE TABLE revisions(node INTEGER, revision TEXT, json TEXT NOT NULL, ts TEXT NOT NULL,
                PRIMARY KEY(node,revision));
            CREATE TABLE jobs(id INTEGER PRIMARY KEY, node INTEGER NOT NULL, revision TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_at TEXT NOT NULL, error TEXT, UNIQUE(node,revision));
            CREATE TABLE decisions(id INTEGER PRIMARY KEY, node INTEGER, revision TEXT, json TEXT,
                extraction TEXT, ts TEXT, UNIQUE(node,revision));
            CREATE TABLE observations(node INTEGER PRIMARY KEY, identity TEXT, price TEXT,
                ts TEXT, source TEXT);
            CREATE INDEX obs_identity ON observations(identity,ts);
            CREATE TABLE outbox(id INTEGER PRIMARY KEY, node INTEGER, revision TEXT, kind TEXT,
                payload TEXT, state TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
                next_at TEXT, created TEXT, sent TEXT, error TEXT, UNIQUE(node,kind));
            PRAGMA user_version=1;
            COMMIT;
            """)
            version = 1
        if version == 1:
            self.conn.executescript("""
            BEGIN;
            CREATE TABLE wishlist_state(source TEXT PRIMARY KEY, items TEXT NOT NULL DEFAULT '[]',
                last_sync TEXT, failed_since TEXT, notice_queued INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0, next_sync TEXT, error TEXT);
            PRAGMA user_version=2;
            COMMIT;
            """)

            version = 2
        if version == 2:
            self.conn.executescript("""
            BEGIN;
            CREATE TABLE wishlist_interpretations(key TEXT PRIMARY KEY, json TEXT NOT NULL);
            CREATE TABLE wishlist_seen(source TEXT, key TEXT, PRIMARY KEY(source,key));
            CREATE TABLE backfill_batches(id INTEGER PRIMARY KEY, source TEXT, items TEXT,
                created TEXT, state TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
                next_at TEXT, error TEXT);
            CREATE TABLE backfill_results(batch INTEGER, node INTEGER, revision TEXT,
                extraction TEXT, decision TEXT, PRIMARY KEY(batch,node));
            ALTER TABLE outbox ADD COLUMN trigger TEXT NOT NULL DEFAULT 'feed';
            ALTER TABLE outbox ADD COLUMN wishlist INTEGER NOT NULL DEFAULT 0;
            UPDATE outbox SET wishlist=1 WHERE kind='wishlist_target' OR EXISTS(
                SELECT 1 FROM decisions d WHERE d.node=outbox.node AND d.revision=outbox.revision
                AND json_extract(d.json, '$.wishlist_applied')=1);
            PRAGMA user_version=3;
            COMMIT;
            """)

    def close(self):
        self.conn.close()

    def meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))

    def topic(self, configured=""):
        if configured:
            return configured
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO meta VALUES('topic',?)", ("dealwatch-" + secrets.token_hex(6),)
            )
        return self.meta("topic")

    def ingest(self, deals, now=None):
        now = now or utcnow()
        ts = now.isoformat()
        with self.conn:
            for deal in deals:
                rev = revision_hash(deal)
                raw = deal.model_dump_json()
                self.conn.execute(
                    """INSERT INTO deals VALUES(?,?,?,?,?) ON CONFLICT(node) DO UPDATE
                    SET json=excluded.json, revision=excluded.revision, last_seen=excluded.last_seen""",
                    (deal.node_id, raw, rev, ts, ts),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)", (deal.node_id, rev, raw, ts)
                )
                state = "held" if (deal.unavailable(now) or "").startswith("upcoming") else "pending"
                if deal.unavailable(now) == "expired":
                    state = "skipped"
                self.conn.execute(
                    "INSERT OR IGNORE INTO jobs(node,revision,state,next_at) VALUES(?,?,?,?)",
                    (deal.node_id, rev, state, ts),
                )
                self.conn.execute(
                    """UPDATE jobs SET state='superseded' WHERE node=? AND revision!=?
                    AND state IN ('pending','held','running')""",
                    (deal.node_id, rev),
                )
        self.release_upcoming(now)

    def release_upcoming(self, now=None):
        now = now or utcnow()
        with self.conn:
            for row in self.conn.execute(
                "SELECT jobs.id,deals.json FROM jobs JOIN deals ON jobs.node=deals.node "
                "WHERE state='held' AND jobs.revision=deals.revision"
            ).fetchall():
                reason = Deal.model_validate_json(row["json"]).unavailable(now)
                if reason is None or reason == "expired":
                    self.conn.execute(
                        "UPDATE jobs SET state=?,next_at=? WHERE id=?",
                        ("pending" if reason is None else "skipped", now.isoformat(), row["id"]),
                    )

    def recover(self):
        # Called only while holding the single-service process lock.
        with self.conn:
            self.conn.execute("UPDATE jobs SET state='pending' WHERE state='running'")
            self.conn.execute("UPDATE outbox SET state='pending' WHERE state='sending'")

    def claim(self, now=None):
        ts = (now or utcnow()).isoformat()
        with self.conn:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE state='pending' AND next_at<=? ORDER BY id LIMIT 1", (ts,)
            ).fetchone()
            if row:
                self.conn.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
        return dict(row) if row else None

    def deal(self, node):
        row = self.conn.execute("SELECT * FROM deals WHERE node=?", (node,)).fetchone()
        return dict(row) if row else None

    def fail_job(self, job, error, max_attempts, now=None):
        now = now or utcnow()
        attempts = job["attempts"] + 1
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET state=?,attempts=?,error=?,next_at=? WHERE id=?",
                (
                    "failed" if attempts >= max_attempts else "pending",
                    attempts,
                    error,
                    (now + timedelta(seconds=min(1800, 120 * 2**attempts))).isoformat(),
                    job["id"],
                ),
            )

    def complete(self, job, deal, extraction, decision, identity, observed_price, payload, config, now=None):
        now = now or utcnow()
        ts = now.isoformat()
        with self.conn:
            current = self.deal(deal.node_id)
            if not current or current["revision"] != job["revision"]:
                self.conn.execute("UPDATE jobs SET state='superseded' WHERE id=?", (job["id"],))
                return
            if identity and observed_price is not None:
                self.conn.execute(
                    """INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(node)
                    DO UPDATE SET identity=excluded.identity,price=excluded.price,ts=excluded.ts,source=excluded.source""",
                    (deal.node_id, identity, str(observed_price), ts, deal.deal_url),
                )
            decision = self.limit_decision(decision, config, now)
            if decision.kind == "none":
                payload = None
            self.conn.execute(
                "INSERT OR REPLACE INTO decisions(node,revision,json,extraction,ts) VALUES(?,?,?,?,?)",
                (
                    deal.node_id,
                    job["revision"],
                    decision.model_dump_json(),
                    extraction.model_dump_json() if extraction else None,
                    ts,
                ),
            )
            if payload and decision.kind != "none":
                # Reserve once per node/class even in dry-run; replay never touches this table.
                self.conn.execute(
                    "INSERT OR IGNORE INTO outbox(node,revision,kind,payload,next_at,created,state,wishlist) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        deal.node_id,
                        job["revision"],
                        decision.kind,
                        json.dumps(payload),
                        ts,
                        ts,
                        "dry_run" if config.dry_run else "pending",
                        int(decision.wishlist_applied),
                    ),
                )
            self.conn.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))
        return decision

    def claim_notification(self, now=None):
        ts = (now or utcnow()).isoformat()
        with self.conn:
            row = self.conn.execute(
                "SELECT * FROM outbox WHERE state='pending' AND next_at<=? ORDER BY id LIMIT 1", (ts,)
            ).fetchone()
            if row:
                self.conn.execute("UPDATE outbox SET state='sending' WHERE id=?", (row["id"],))
        return dict(row) if row else None

    def notification_done(self, row, state, error=None, now=None):
        now = now or utcnow()
        with self.conn:
            self.conn.execute(
                "UPDATE outbox SET state=?,sent=?,error=? WHERE id=?",
                (state, now.isoformat() if state == "sent" else None, error, row["id"]),
            )

    def retry_notification(self, row, error, now=None):
        now = now or utcnow()
        delay = min(1800, 30 * 2 ** min(row["attempts"], 6))
        with self.conn:
            self.conn.execute(
                "UPDATE outbox SET state='pending',attempts=attempts+1,next_at=?,error=? WHERE id=?",
                ((now + timedelta(seconds=delay)).isoformat(), error, row["id"]),
            )

    def status(self, config):
        def counts(table):
            return dict(self.conn.execute(f"SELECT state,COUNT(*) FROM {table} GROUP BY state"))

        return {
            "topic": self.topic(config.ntfy_topic),
            "dry_run": config.dry_run,
            "schema": SCHEMA_VERSION,
            "wishlist": self.wishlist_status(config),
            "last_poll": self.meta("last_poll"),
            "last_feed_error": self.meta("feed_error"),
            "last_judge": self.meta("last_judge"),
            "jobs": counts("jobs"),
            "backfill_batches": counts("backfill_batches"),
            "notifications": counts("outbox"),
            "oldest_pending": self.conn.execute(
                "SELECT MIN(next_at) FROM jobs WHERE state='pending'"
            ).fetchone()[0],
            "observations": self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "last_sent": self.conn.execute("SELECT MAX(sent) FROM outbox").fetchone()[0],
        }

    def wishlist_state(self, source):
        row = self.conn.execute("SELECT * FROM wishlist_state WHERE source=?", (source,)).fetchone()
        return dict(row) if row else None

    def wishlist_status(self, config):
        from .wishlist import configured, source_key

        state = self.wishlist_state(source_key(config)) or {}
        return {
            "source": "keep" if configured(config) else "file",
            "last_sync": state.get("last_sync"),
            "keep_sync_degraded": bool(configured(config) and state.get("failed_since")),
            "failed_since": state.get("failed_since"),
            "last_error": state.get("error"),
            "items": self.interpreted_status_items(state),
            "interpretation_error": self.meta("wishlist_interpretation_error"),
        }

    def _keep_notice(self, source, episode, kind, message, config, now):
        # Same persistent outbox/delivery semantics as deal alerts; node 0 is reserved for service notices.
        key = f"keep_{kind}:{source}:{episode}"
        self.conn.execute(
            "INSERT OR IGNORE INTO outbox(node,revision,kind,payload,state,next_at,created) "
            "VALUES(0,'service',?,?,?,?,?)",
            (
                key,
                json.dumps(
                    {
                        "title": "Dealwatch Keep sync",
                        "message": message,
                        "priority": 3,
                        "tags": ["warning" if kind == "broken" else "white_check_mark"],
                    }
                ),
                "dry_run" if config.dry_run else "pending",
                now.isoformat(),
                now.isoformat(),
            ),
        )

    def wishlist_success(self, source, items, config, now):
        with self.conn:
            state = self.wishlist_state(source)
            from .wishlist import item_key

            baseline_key = "backfill_baseline:" + source
            baseline = self.meta(baseline_key) is None
            new_items = []
            for item in items:
                key = item_key(item)
                seen = self.conn.execute(
                    "SELECT 1 FROM wishlist_seen WHERE source=? AND key=?", (source, key)
                ).fetchone()
                if not seen and not baseline:
                    new_items.append(item)
                self.conn.execute("INSERT OR IGNORE INTO wishlist_seen VALUES(?,?)", (source, key))
            self.conn.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (baseline_key, now.isoformat()))
            if new_items and config.backfill_max_alerts:
                self.conn.execute(
                    "INSERT INTO backfill_batches(source,items,created,next_at) VALUES(?,?,?,?)",
                    (
                        source,
                        json.dumps([i.model_dump(mode="json") for i in new_items]),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            if state and state["failed_since"] and state["notice_queued"]:
                self._keep_notice(
                    source,
                    state["failed_since"],
                    "recovered",
                    "Keep sync recovered; the wishlist is up to date again.",
                    config,
                    now,
                )
            self.conn.execute(
                "INSERT OR REPLACE INTO wishlist_state VALUES(?,?,?,NULL,0,0,?,NULL)",
                (
                    source,
                    json.dumps([x.model_dump(mode="json") for x in items]),
                    now.isoformat(),
                    (now + timedelta(minutes=config.keep_sync_minutes)).isoformat(),
                ),
            )

    def wishlist_failure(self, source, error, config, now):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO wishlist_state(source) VALUES(?)", (source,))
            state = self.wishlist_state(source)
            failures = state["failures"] + 1
            delay = min(3600, config.keep_sync_minutes * 60 * 2 ** min(failures - 1, 8))
            self.conn.execute(
                "UPDATE wishlist_state SET failed_since=COALESCE(failed_since,?),failures=?,"
                "next_sync=?,error=? WHERE source=?",
                (now.isoformat(), failures, (now + timedelta(seconds=delay)).isoformat(), error, source),
            )
        self.maybe_keep_notice(source, config, now)
        return delay

    def maybe_keep_notice(self, source, config, now):
        from datetime import datetime

        if not source.startswith("keep:"):
            return
        with self.conn:
            state = self.wishlist_state(source)
            if (
                state
                and state["failed_since"]
                and not state["notice_queued"]
                and (now - datetime.fromisoformat(state["failed_since"]) >= timedelta(hours=6))
            ):
                self._keep_notice(
                    source,
                    state["failed_since"],
                    "broken",
                    f"Keep sync broken since {state['failed_since']}. Using the last saved wishlist.",
                    config,
                    now,
                )
                self.conn.execute("UPDATE wishlist_state SET notice_queued=1 WHERE source=?", (source,))

    def prune(self, now=None):
        now = now or utcnow()
        short = (now - timedelta(days=30)).isoformat()
        with self.conn:
            self.conn.execute(
                "DELETE FROM observations WHERE ts<?", ((now - timedelta(days=180)).isoformat(),)
            )
            self.conn.execute(
                "DELETE FROM backfill_results WHERE batch IN (SELECT id FROM backfill_batches WHERE created<? AND state!='pending')",
                (short,),
            )
            self.conn.execute("DELETE FROM backfill_batches WHERE created<? AND state!='pending'", (short,))
            self.conn.execute("DELETE FROM revisions WHERE ts<?", (short,))
            self.conn.execute("DELETE FROM decisions WHERE ts<?", (short,))
            self.conn.execute(
                "DELETE FROM jobs WHERE node IN (SELECT node FROM deals WHERE last_seen<?)", (short,)
            )
            self.conn.execute("DELETE FROM deals WHERE last_seen<?", (short,))
            self.conn.execute(
                "DELETE FROM outbox WHERE created<?", ((now - timedelta(days=365)).isoformat(),)
            )

    def interpreted_status_items(self, state):
        from .models import WishlistItem
        from .wishlist import item_key

        items = json.loads(state.get("items", "[]"))
        for item in items:
            row = self.conn.execute(
                "SELECT json FROM wishlist_interpretations WHERE key=?",
                (item_key(WishlistItem.model_validate(item)),),
            ).fetchone()
            item["interpretation"] = json.loads(row[0]) if row else None
        return items

    def limit_decision(self, decision, config, now):
        from zoneinfo import ZoneInfo

        if decision.kind == "none":
            return decision

        start = (
            now.astimezone(ZoneInfo(config.tz))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(now.tzinfo)
            .isoformat()
        )
        limits = [
            ("node!=0 OR trigger='wishlist_backfill'", config.alert_daily_limit, "Total alert daily limit")
        ]
        if decision.kind == "possible":
            limits.append(("kind='possible'", config.possible_daily_limit, "Possible alert daily limit"))
        if decision.wishlist_applied:
            limits.append(("wishlist=1", config.wishlist_daily_limit, "Wishlist alert daily limit"))
        for condition, limit, reason in limits:
            count = self.conn.execute(
                f"SELECT COUNT(*) FROM outbox WHERE ({condition}) AND created>=? AND state IN ('pending','sending','sent','dry_run')",
                (start,),
            ).fetchone()[0]
            if count >= limit:
                return decision.model_copy(update={"kind": "none", "reason": reason})
        return decision

    def already_alerted(self, node):
        return bool(
            self.conn.execute(
                "SELECT 1 FROM outbox WHERE node=? AND state IN ('pending','sending','sent','dry_run','bundled')",
                (node,),
            ).fetchone()
        )
