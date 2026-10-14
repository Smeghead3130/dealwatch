"""Prepare explicitly mocked replay inputs from an RSS capture and the user brief."""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from dealwatch.feed import parse_feed
from dealwatch.models import Deal, Extraction

recorded = {
    x["node"]: x
    for x in map(json.loads, Path("eval/reports/live-five-results.jsonl").read_text().splitlines())
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--feed", type=Path, default=Path("tests/fixtures/ozb_feed_2026-10-14.xml"))
parser.add_argument("--captured-at", default="2026-10-14T05:29:43+00:00")
args = parser.parse_args()
feed = parse_feed(args.feed.read_bytes())
rows = []
for d in feed:
    if d.node_id in recorded and d.title == recorded[d.node_id]["title"]:
        ex = recorded[d.node_id]["extraction"]
        provenance = "recorded v0.2 live extraction; captured feed"
    else:
        name = re.split(r"\$|\s+@\s+", d.title, maxsplit=1)[0].strip()
        ex = Extraction(model=name, identity_quote=name, price=None, condition="new").model_dump(mode="json")
        if re.search(r"US\$", d.title):
            ex["currency"] = "USD"
        if "Delivered" in d.title:
            ex.update(shipping="0", shipping_quote="Delivered")
        if "Subscription" in d.title:
            ex["subscription"] = True
        if d.node_id == 978840:
            ex.update(
                model="Invisible",
                brand="Durex",
                variant="Reg/Wide Fit Condoms Pack of 10",
                identity_quote="Durex Invisible Reg/Wide Fit Condoms Pack of 10",
                membership="Prime required for free delivery; non-subscription price used",
                shipping=None,
            )
        provenance = "captured public feed; deliberately minimal mocked extraction, not live inference"
    rows.append(
        {
            "node": d.node_id,
            "deal": d.model_dump(mode="json"),
            "extraction": ex,
            "provenance": provenance,
            "timestamp": args.captured_at,
            "source_fixture": str(args.feed),
        }
    )
for captured in json.loads(Path("tests/fixtures/ozb_nodes_2026-10-14.json").read_text())["nodes"]:
    node, title = captured["node"], captured["title"]
    if any(row["node"] == node for row in rows):
        continue
    # Only title is captured. Timestamps and extraction fields are authored replay inputs,
    # never claimed to be a complete historical/live model reconstruction.
    model = {
        978840: "Durex Invisible Reg/Wide Fit Condoms Pack of 10",
        978861: "1kg SO",
        978865: "Leisure Suit Larry - Wet Dreams Saga Bundle",
        978890: "Cadbury Variety Jumbo Chocolate Box 110 Pieces 1.56kg",
    }[node]
    d = Deal(
        node_id=node,
        title=title,
        published=datetime(2026, 10, 14, tzinfo=timezone.utc),
        deal_url=captured["url"],
    )
    ex = Extraction(model=model, identity_quote=model, price=None, condition="new")
    if node == 978865:
        ex.membership = "Game Pass required"
    if node == 978840:
        ex.membership = "Prime required for free delivery; non-subscription price used"
    if node == 978861:
        ex.variant = "Brazil or Peru"

    rows.append(
        {
            "node": node,
            "deal": d.model_dump(mode="json"),
            "extraction": ex.model_dump(mode="json"),
            "provenance": "fresh public node title; authored extraction and publication time, not live-model output",
            "source_fixture": "tests/fixtures/ozb_nodes_2026-10-14.json",
            "timestamp": args.captured_at,
        }
    )
Path("eval/replay_v030.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
