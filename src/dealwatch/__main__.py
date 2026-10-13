import argparse
import asyncio
import json
import logging
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from .config import Config
from .db import Store
from .evidence import LocalHistory
from .feed import poll_once
from .judge import judge
from .models import Deal, utcnow
from .notify import publish
from .worker import run


class CaseHistory:
    def __init__(self, prices):
        self.prices = prices

    def observations(self, extraction, deal, now, config):
        return [(Decimal(str(p)), f"fixture:prior-observation-{i}") for i, p in enumerate(self.prices)]


async def evaluate(client, config, path, split):
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    cases = [c for c in cases if split == "all" or c["split"] == split]
    outcomes = []
    for c in cases:
        deal = Deal(
            node_id=c["id"],
            title=c["title"],
            summary=c.get("summary", ""),
            published=utcnow(),
            categories=c.get("categories", []),
            status=c.get("status"),
            deal_url=f"https://www.ozbargain.com.au/node/{c['id']}",
        )
        try:
            _, decision = await judge(client, config, deal, CaseHistory(c.get("history", [])))
            item = {
                "id": c["id"],
                "expected": c["expected"],
                "actual": decision.kind,
                "model": decision.model,
                "latency_ms": decision.latency_ms,
                "reason": decision.reason,
            }
        except Exception as exc:
            item = {"id": c["id"], "expected": c["expected"], "actual": "error", "error": type(exc).__name__}
        outcomes.append(item)
        print(json.dumps(item), flush=True)
    tp = sum(c["expected"] != "none" and c["actual"] not in ("none", "error") for c in outcomes)
    fp = sum(c["expected"] == "none" and c["actual"] not in ("none", "error") for c in outcomes)
    misses = [c["id"] for c in outcomes if c["expected"] != "none" and c["actual"] in ("none", "error")]
    print(
        json.dumps(
            {
                "summary": {
                    "split": split,
                    "cases": len(outcomes),
                    "true_positives": tp,
                    "false_positives": fp,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "misses": misses,
                    "errors": sum(c["actual"] == "error" for c in outcomes),
                    "class_accuracy": sum(c["actual"] == c["expected"] for c in outcomes) / len(outcomes)
                    if outcomes
                    else None,
                }
            }
        )
    )


async def command(args, config):
    if args.cmd == "evaluate":
        async with httpx.AsyncClient() as client:
            await evaluate(client, config, args.cases, args.split)
        return
    db = Store(config.data_dir / "dealwatch.db")
    try:
        if args.cmd == "run":
            await run(db, config)
        elif args.cmd == "status":
            print(json.dumps(db.status(config), indent=2))
        elif args.cmd == "export":
            rows = db.conn.execute("SELECT node,revision,json,ts FROM decisions ORDER BY ts")
            for row in rows:
                print(json.dumps(dict(row)))
        else:
            async with httpx.AsyncClient() as client:
                if args.cmd == "test-notify":
                    await publish(
                        client,
                        config,
                        args.topic,
                        {
                            "title": "Dealwatch test",
                            "message": "Codex build test. No purchase action. Dealwatch to ntfy is working.",
                            "priority": 3,
                        },
                    )
                    print(json.dumps({"sent": True, "topic": args.topic}))
                elif args.cmd == "judge":
                    if args.refresh or not db.deal(args.node):
                        await poll_once(client, db, config)
                    stored = db.deal(args.node)
                    if not stored:
                        raise ValueError("Node is not in the current feed or local database")
                    ex, decision = await judge(
                        client, config, Deal.model_validate_json(stored["json"]), LocalHistory(db)
                    )
                    print(
                        json.dumps(
                            {
                                "node": args.node,
                                "extraction": ex.model_dump(mode="json") if ex else None,
                                "decision": decision.model_dump(mode="json"),
                            },
                            indent=2,
                        )
                    )
                elif args.cmd == "replay":
                    since = (utcnow() - timedelta(hours=args.hours)).isoformat()
                    for row in db.conn.execute(
                        "SELECT json FROM deals WHERE first_seen>=? ORDER BY first_seen", (since,)
                    ).fetchall():
                        deal = Deal.model_validate_json(row[0])
                        _, decision = await judge(client, config, deal, LocalHistory(db))
                        print(
                            json.dumps(
                                {
                                    "node": deal.node_id,
                                    "dry_run": True,
                                    "decision": decision.model_dump(mode="json"),
                                }
                            )
                        )
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Dealwatch: Australian steal alerts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "status", "export"):
        sub.add_parser(name)
    p = sub.add_parser("test-notify")
    p.add_argument("--topic", default="dealwatch-codex-test")
    p = sub.add_parser("judge")
    p.add_argument("node", type=int)
    p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("replay")
    p.add_argument("--hours", type=int, default=24)
    p = sub.add_parser("evaluate")
    p.add_argument("--cases", type=Path, default=Path("eval/cases.jsonl"))
    p.add_argument("--split", choices=("tune", "heldout", "all"), default="heldout")
    args = parser.parse_args()
    try:
        config = Config()
        logging.basicConfig(level=config.log_level, format="%(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        asyncio.run(command(args, config))
    except Exception as exc:
        # Pydantic/http errors can contain input credentials; never dump their repr.
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": "Command failed; check configuration and service status",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
