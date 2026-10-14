"""Offline authored mocks by default; --live explicitly runs real Ollama, never ntfy/Keep."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

from dealwatch.__main__ import CaseHistory
from dealwatch.config import Config
from dealwatch.extract import normalise_extraction
from dealwatch.judge import judge
from dealwatch.models import Deal, Decision, Extraction
from dealwatch.value import decide, excluded
from dealwatch.wishlist import parse_lines

ROOT = Path(__file__).resolve().parents[1]


def metrics(rows):
    positives = [r for r in rows if r["actual"] not in ("none", "error")]
    false_positives = [r["id"] for r in positives if r["expected"] == "none"]
    misses = [r["id"] for r in rows if r["expected"] != "none" and r["actual"] in ("none", "error")]
    return {
        "cases": len(rows),
        "alerts": len(positives),
        "precision": (len(positives) - len(false_positives)) / len(positives) if positives else None,
        "misses": misses,
        "false_positive_examples": false_positives,
        "errors": [r["id"] for r in rows if r["actual"] == "error"],
        "class_matches": sum(r["actual"] == r["expected"] for r in rows),
    }


def regression():
    rows = []
    for line in (ROOT / "eval/reports/v020-nonwishlist-regression.jsonl").read_text().splitlines():
        old = json.loads(line)
        if "id" not in old:
            continue
        deal, now = Deal.model_validate(old["deal"]), datetime.fromisoformat(old["timestamp"])
        if reason := deal.unavailable(now):
            decision = Decision(kind="none", reason=reason)
        elif excluded(deal, Config()):
            decision = Decision(kind="none", reason="Excluded category")
        else:
            ex = normalise_extraction(Extraction.model_validate(old["extraction"]), deal)
            decision = decide(deal, ex, CaseHistory(old["history"]), Config(), now)
        rows.append(
            {
                "id": old["id"],
                "expected": old["expected"],
                "previous": old["actual"],
                "actual": decision.kind,
                "reason": decision.reason,
            }
        )
    summary = metrics(rows)
    summary["previous_alerts"] = sum(r["previous"] != "none" for r in rows)
    summary["changed_classes"] = [r["id"] for r in rows if r["previous"] != r["actual"]]
    return {"mode": "recorded v0.2 extractions replayed offline", "rows": rows, "summary": summary}


async def evaluate(live=False):
    rows = []
    for line in (ROOT / "eval/wishlist_descriptions.jsonl").read_text().splitlines():
        case = json.loads(line)

        def handler(req):
            schema = json.loads(req.content)["format"]["title"]
            key = {
                "Extraction": "mock_extraction",
                "Interpretation": "mock_interpretation",
                "FitResult": "mock_fit",
            }[schema]
            return httpx.Response(200, json={"message": {"content": json.dumps(case[key])}})

        # Fixed clock ensures community-signal boundaries are repeatable in both modes.
        now = datetime.fromisoformat("2026-10-14T00:00:00+00:00")
        deal = Deal(
            node_id=case["id"],
            title=case["title"],
            summary=case["summary"],
            votes_pos=case["votes_pos"],
            published=now,
            deal_url=f"https://www.ozbargain.com.au/node/{case['id']}",
        )
        try:
            async with httpx.AsyncClient(transport=None if live else httpx.MockTransport(handler)) as client:
                _, decision = await judge(
                    client, Config(), deal, CaseHistory([]), now, parse_lines(case["wishlist"])
                )
            rows.append(
                {
                    "id": case["id"],
                    "label": case["label"],
                    "expected": case["expected"],
                    "actual": "error" if decision.wishlist_error else decision.kind,
                    "decision": decision.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "id": case["id"],
                    "expected": case["expected"],
                    "actual": "error",
                    "error": type(exc).__name__,
                }
            )
    return {
        "mode": "live Ollama" if live else "authored mocked Ollama responses; NOT live-model accuracy",
        "rows": rows,
        "summary": metrics(rows),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"description_cases": await evaluate(args.live), "recorded_regression": regression()}
    raw = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(raw)
    print(json.dumps({k: v["summary"] for k, v in result.items()}, indent=2))
    return bool(
        result["description_cases"]["summary"]["errors"]
        or result["description_cases"]["summary"]["class_matches"]
        != result["description_cases"]["summary"]["cases"]
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
