"""Read-only Keep adapter. Blocking third-party I/O runs in a bounded child process."""

import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime

from .models import WishlistItem, utcnow
from .wishlist import FileSource, configured, parse_lines, source_key


def fetch_keep(config, factory=None):
    """Synchronous worker boundary; no database or notification access."""
    import gkeepapi

    if not config.keep_email or not config.keep_master_token.get_secret_value():
        raise ValueError("Both Keep credentials are required")
    client = (factory or gkeepapi.Keep)()
    cache = config.data_dir / "keep_state.json"
    state = None
    try:
        saved = json.loads(cache.read_text())
        if (
            isinstance(saved, dict)
            and saved.get("source") == source_key(config)
            and isinstance(saved.get("state"), dict)
        ):
            state = saved["state"]
    except (OSError, ValueError, KeyError):
        pass  # Missing/corrupt cache: one full sync, last-good DB remains untouched.
    client.authenticate(config.keep_email, config.keep_master_token.get_secret_value(), state=state)
    notes = [
        n
        for n in client.all()
        if not n.trashed
        and not getattr(n, "deleted", False)
        and n.title.strip().casefold() == config.keep_note_title.strip().casefold()
    ]
    if not notes:
        raise LookupError("Keep note not found")

    def edited(note):
        value = note.timestamps.updated
        return value.timestamp() if isinstance(value, datetime) else 0

    note = max(notes, key=lambda n: (edited(n), n.id))
    if isinstance(note, gkeepapi.node.List):
        lines = [item.text for item in note.items if not item.checked and not getattr(item, "deleted", False)]
    else:
        lines = note.text.splitlines()
    items = parse_lines(lines)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".keep-state-", dir=config.data_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"source": source_key(config), "state": client.dump()}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, cache)  # mkstemp's 0600 mode also applies after replacement.
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"items": [x.model_dump(mode="json") for x in items], "duplicates": len(notes) > 1}


class KeepSource:
    def __init__(self, config):
        self.config = config

    async def fetch(self):
        # gkeepapi/gpsoauth have synchronous HTTP calls without a universal timeout hook.
        # A killable child bounds the entire sync and does not consume the inference loop/thread pool.
        env = {
            **os.environ,
            "KEEP_EMAIL": self.config.keep_email,
            "KEEP_MASTER_TOKEN": self.config.keep_master_token.get_secret_value(),
            "KEEP_NOTE_TITLE": self.config.keep_note_title,
            "DATA_DIR": str(self.config.data_dir),
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "dealwatch.keep_sync",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
            if process.returncode:
                raise RuntimeError("Keep sync failed")
            result = json.loads(stdout)
            if result.get("duplicates"):
                logging.getLogger("dealwatch").warning(
                    json.dumps(
                        {
                            "event": "keep_duplicate_title",
                            "message": "Multiple matching Keep notes; used the most recently edited",
                        }
                    )
                )
            return [WishlistItem.model_validate(x) for x in result["items"]]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def sync_once(db, config, source=None, now=None, force=False):
    now = now or utcnow()
    key = source_key(config)
    state = db.wishlist_state(key)
    if not force and state and state["next_sync"] and datetime.fromisoformat(state["next_sync"]) > now:
        return (datetime.fromisoformat(state["next_sync"]) - now).total_seconds()
    source = source or (KeepSource(config) if configured(config) else FileSource(config))
    try:
        items = await source.fetch()
        db.wishlist_success(key, items, config, now)
        logging.getLogger("dealwatch").info(
            json.dumps(
                {
                    "event": "wishlist_synced",
                    "source": "keep" if configured(config) else "file",
                    "count": len(items),
                }
            )
        )
        return config.keep_sync_minutes * 60
    except Exception as exc:
        # Never log exception messages from authentication libraries or input credentials.
        delay = db.wishlist_failure(key, type(exc).__name__, config, now)
        logging.getLogger("dealwatch").warning(
            json.dumps({"event": "wishlist_sync_failed", "error": type(exc).__name__, "retry_seconds": delay})
        )
        return delay


def main():
    from .config import Config

    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(fetch_keep(Config())))
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
