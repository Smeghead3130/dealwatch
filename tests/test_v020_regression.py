import json
from datetime import datetime
from pathlib import Path

from dealwatch.__main__ import CaseHistory
from dealwatch.models import Deal, Decision, Extraction
from dealwatch.value import decide, excluded


def test_all_40_nonwishlist_decisions_match_v010(config):
    """Replay the saved live-extraction inputs against the frozen v0.1.0 decision outputs."""
    rows = [
        json.loads(line)
        for line in Path("eval/reports/v020-nonwishlist-regression.jsonl").read_text().splitlines()
    ]
    cases = [row for row in rows if "id" in row]
    assert {row["id"] for row in cases} == set(range(1, 41))
    for row in cases:
        now = datetime.fromisoformat(row["timestamp"])
        deal = Deal.model_validate(row["deal"])
        if reason := deal.unavailable(now):
            actual = Decision(kind="none", reason=reason)
        elif excluded(deal, config):
            actual = Decision(kind="none", reason="Excluded category")
        else:
            actual = decide(
                deal, Extraction.model_validate(row["extraction"]), CaseHistory(row["history"]), config, now
            )
        assert (
            actual.model_dump(mode="json", include=set(row["legacy_decision"])) == row["legacy_decision"]
        ), row["id"]
        assert not actual.wishlist_matches and not actual.wishlist_applied
