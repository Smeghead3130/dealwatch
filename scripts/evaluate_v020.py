"""Live evaluation only; no database, Keep credentials or notifications are used."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx

from dealwatch.__main__ import CaseHistory
from dealwatch.config import Config
from dealwatch.judge import judge
from dealwatch.models import Deal, Decision, utcnow
from dealwatch.value import excluded
from dealwatch.wishlist import parse_lines

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("legacy_value", ROOT / "eval/baseline/value_v010.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
CORE = {"kind", "reason", "effective_price", "observations", "sources", "restrictions"}


async def evaluate():
    config = Config(_env_file=None, dry_run=True)
    reports = ROOT / "eval/reports"
    failures = 0
    async with httpx.AsyncClient() as client:
        for dataset, report in [("cases", "v020-nonwishlist-regression"), ("wishlist", "v020-wishlist-live")]:
            rows = []
            with (reports / f"{report}.jsonl").open("w") as output:
                for line in (ROOT / f"eval/{dataset}.jsonl").read_text().splitlines():
                    case = json.loads(line)
                    now = utcnow()
                    deal = Deal(
                        node_id=case["id"],
                        title=case["title"],
                        summary=case.get("summary", ""),
                        published=now,
                        status=case.get("status"),
                        categories=case.get("categories", []),
                        deal_url=f"https://www.ozbargain.com.au/node/{case['id']}",
                    )
                    ex, decision = await judge(
                        client,
                        config,
                        deal,
                        CaseHistory(case.get("history", [])),
                        now,
                        parse_lines(case.get("wishlist", [])),
                    )
                    row = {
                        "id": case["id"],
                        "timestamp": now.isoformat(),
                        "expected": case["expected"],
                        "actual": decision.kind,
                        "deal": deal.model_dump(mode="json"),
                        "history": case.get("history", []),
                        "extraction": ex.model_dump(mode="json") if ex else None,
                        "decision": decision.model_dump(mode="json"),
                    }
                    if dataset == "cases":
                        if reason := deal.unavailable(now):
                            baseline = Decision(kind="none", reason=reason)
                        elif excluded(deal, config):
                            baseline = Decision(kind="none", reason="Excluded category")
                        else:
                            baseline = legacy.decide(
                                deal, ex, CaseHistory(case.get("history", [])), config, now
                            )
                        row["legacy_decision"] = baseline.model_dump(mode="json", include=CORE)
                        row["passed"] = row["legacy_decision"] == decision.model_dump(
                            mode="json", include=CORE
                        )
                    else:
                        row["label"] = case["label"]
                        row["wishlist"] = case["wishlist"]
                        row["expected_match"] = case["expected_match"]
                        row["actual_match"] = bool(decision.wishlist_matches)
                        row["passed"] = (
                            decision.kind == case["expected"]
                            and row["actual_match"] == case["expected_match"]
                        )
                    failures += not row["passed"]
                    rows.append(row)
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    print(json.dumps({k: row[k] for k in ("id", "expected", "actual", "passed")}), flush=True)
                summary = {
                    "dataset": dataset,
                    "cases": len(rows),
                    "passed": sum(r["passed"] for r in rows),
                    "label_class_matches": sum(r["actual"] == r["expected"] for r in rows),
                    "endpoint": config.ollama_url,
                    "model": config.llm_model,
                }
                output.write(json.dumps({"summary": summary}) + "\n")
                print(json.dumps({"summary": summary}), flush=True)
    return bool(failures)


if __name__ == "__main__":
    sys.exit(asyncio.run(evaluate()))
