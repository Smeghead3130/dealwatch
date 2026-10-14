"""Deterministic replay of explicitly recorded/authored extraction inputs; no network."""

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from dealwatch.__main__ import CaseHistory
from dealwatch.config import Config
from dealwatch.db import Store
from dealwatch.extract import normalise_extraction
from dealwatch.models import Deal, Extraction
from dealwatch.notify import payload
from dealwatch.value import decide


def replay():
    rows = []
    with TemporaryDirectory() as directory:
        db = Store(Path(directory) / "replay.db")
        config = Config(dry_run=True)
        inputs = [json.loads(line) for line in Path("eval/replay_v030.jsonl").read_text().splitlines()]
        for item in sorted(inputs, key=lambda r: r["timestamp"]):
            now = datetime.fromisoformat(item["timestamp"])
            deal = Deal.model_validate(item["deal"])
            ex = normalise_extraction(Extraction.model_validate(item["extraction"]), deal)
            decision = decide(deal, ex, CaseHistory([]), config, now)
            db.ingest([deal], now)
            job = db.claim(now)
            # The fixture has skipped/held offers; record the decision without claiming them ready.
            if job:
                decision = (
                    db.complete(
                        job,
                        deal,
                        ex,
                        decision,
                        None,
                        None,
                        payload(deal, ex, decision, config) if decision.kind != "none" else None,
                        config,
                        now,
                    )
                    or decision
                )
            rows.append(
                {
                    "node": deal.node_id,
                    "title": deal.title,
                    "actual": decision.kind,
                    "reason": decision.reason,
                    "effective_price": str(decision.effective_price),
                    "restrictions": decision.restrictions,
                    "provenance": item["provenance"],
                }
            )
        notifications = db.conn.execute("SELECT node,kind,state,created FROM outbox").fetchall()
        days = sorted(
            {
                datetime.fromisoformat(x["timestamp"])
                .astimezone(__import__("zoneinfo").ZoneInfo(config.tz))
                .date()
                .isoformat()
                for x in inputs
            }
        )
        counts = {
            day: sum(
                datetime.fromisoformat(r["created"])
                .astimezone(__import__("zoneinfo").ZoneInfo(config.tz))
                .date()
                .isoformat()
                == day
                for r in notifications
            )
            for day in days
        }
        db.close()
    return {
        "mode": "offline partial historical feed plus user-brief examples; NOT fresh feed or live model results",
        "rows": rows,
        "summary": {
            "cases": len(rows),
            "alerts": [{"node": r["node"], "kind": r["kind"]} for r in notifications],
            "notifications_per_sampled_local_day": counts,
            "full_day_coverage": False,
            "rate_limitation": "A 30-item feed snapshot and three selected examples cannot estimate a real daily rate. Default global cap is 5; no minimum is forced.",
        },
    }


if __name__ == "__main__":
    result = replay()
    Path("eval/reports/v030-replay.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["summary"], indent=2))
