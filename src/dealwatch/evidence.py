import json
import re
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from .extract import source_text


def norm(s):
    return " ".join(str(s).casefold().split())


def amounts(s):
    return [
        Decimal(x.replace(",", ""))
        for x in re.findall(r"(?:\$|AUD\s*)(\d[\d,]*(?:\.\d{1,2})?)(?!\d)", s, re.I)
    ]


def supported(quote, text):
    return bool(quote and norm(quote) in norm(text))


def identity(ex):
    if not ex.brand or not ex.model or ex.condition == "unknown":
        return None
    # Conservative exact identity; extra terms prevent incompatible price bases.
    return json.dumps(
        [
            norm(ex.brand),
            norm(ex.model),
            norm(ex.variant),
            ex.condition,
            ex.quantity,
            sorted(norm(s) for s in ex.bundle),
            ex.currency,
            bool(ex.membership),
            bool(ex.coupon),
            ex.imported,
            ex.marketplace,
            "delivered" if ex.shipping is not None else "shipping-unknown",
        ],
        separators=(",", ":"),
    )


class EvidenceSource(Protocol):
    def observations(self, extraction, deal, now, config) -> list[tuple[Decimal, str]]: ...


class LocalHistory:
    def __init__(self, db):
        self.db = db

    def observations(self, extraction, deal, now, config):
        key = identity(extraction)
        if not key:
            return []
        rows = self.db.conn.execute(
            "SELECT price,source FROM observations WHERE identity=? AND node!=? "
            "AND ts>=? AND ts<? ORDER BY ts",
            (key, deal.node_id, (now - timedelta(days=config.history_days)).isoformat(), now.isoformat()),
        )
        return [(Decimal(row[0]), row[1]) for row in rows]


def quoted_references(ex, deal):
    text = source_text(deal)
    result = []
    for ref in ex.references:
        if not ref.same_product or not supported(ref.quote, text):
            continue
        if (
            not ref.source.strip()
            or norm(ref.source) not in norm(ref.quote)
            or ref.price not in amounts(ref.quote)
        ):
            continue
        if norm(ref.source) in {"rrp", "msrp", "retail", "normal", "was", "retailer", "store"}:
            continue
        if re.search(
            r"\b(?:rrp|msrp|recommended retail|possibly|could|would|hypothetical|price.match)\b",
            ref.quote,
            re.I,
        ):
            continue
        result.append(ref)
    return result
