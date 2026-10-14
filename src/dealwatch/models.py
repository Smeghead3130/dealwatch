import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow():
    return datetime.now(timezone.utc)


class Deal(BaseModel):
    node_id: int
    title: str
    summary: str = ""
    categories: list[str] = []
    category_types: list[str] = []
    deal_url: str
    merchant_url: str | None = None
    image: str | None = None
    published: datetime
    expiry: datetime | None = None
    starting: datetime | None = None
    status: str | None = None
    votes_pos: int = 0
    votes_neg: int = 0
    clicks: int = 0
    comments: int = 0

    def unavailable(self, now=None):
        now = now or utcnow()
        if (
            self.status in {"expired", "out-of-stock", "outofstock"}
            or (self.expiry and self.expiry <= now)
            or re.search(r"^\s*\[(?:expired|out of stock|oos)\]", self.title, re.I)
        ):
            return "expired"
        if self.starting and self.starting > now:
            return "upcoming"
        if self.status == "upcoming" and not self.starting:
            return "upcoming without start time"
        return None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Reference(StrictModel):
    source: str
    price: Decimal = Field(gt=0)
    quote: str
    same_product: bool


class Extraction(StrictModel):
    brand: str | None = None
    model: str | None = None
    variant: str = ""
    identity_quote: str = ""
    price: Decimal | None = Field(default=None, ge=0)
    price_quote: str = ""
    currency: str = "AUD"
    quantity: int = Field(1, ge=1, le=10000)
    condition: Literal["new", "refurb", "open-box", "used", "unknown"] = "unknown"
    bundle: list[str] = []
    shipping: Decimal | None = Field(default=None, ge=0)
    shipping_quote: str = ""
    membership: str | None = None
    coupon: str | None = None
    cashback: str | None = None
    deposit: bool = False
    subscription: bool = False
    imported: bool = False
    marketplace: bool = False
    accessory: bool = False
    references: list[Reference] = Field(default_factory=list, max_length=5)
    signal: Literal["none", "price_error", "clearance", "lowest_ever", "free_tiny_cost"] = "none"
    signal_quote: str = ""


class Requirement(StrictModel):
    text: str = Field(min_length=1, max_length=240)
    hard: bool = True


class Interpretation(StrictModel):
    # Field order matters for schema-constrained generation: requirements are written before
    # the label, otherwise small models anchor on the first enum value (seen live on qwen3.5:9b).
    requirements: list[Requirement] = Field(default_factory=list, max_length=12)
    accessory_need: bool = False
    kind: Literal["need_description", "specific_product"]


class RequirementFit(StrictModel):
    index: int = Field(ge=0)
    status: Literal["met", "not_met", "unknown"]
    quote: str = Field(default="", max_length=600)
    judgement: str = Field(default="", max_length=240)


class FitResult(StrictModel):
    requirements: list[RequirementFit] = Field(max_length=12)
    overall: Literal["fits", "partial", "no"]


class WishlistItem(BaseModel):
    id: str
    text: str
    product: str
    target: Decimal | None = None
    strict_target: bool = False
    interpretation: Interpretation | None = None


class WishlistMatch(BaseModel):
    item: WishlistItem
    reason: str
    fit: FitResult | None = None


class Decision(BaseModel):
    kind: Literal["none", "possible", "evidence", "wishlist_target", "wishlist_match"]
    reason: str
    effective_price: Decimal | None = None
    observations: int = 0
    sources: list[str] = []
    restrictions: list[str] = []
    model: str = ""
    latency_ms: int = 0
    wishlist_matches: list[WishlistMatch] = Field(default_factory=list)
    wishlist_error: str | None = None
    wishlist_applied: bool = False
    trigger: str = "feed"
    availability: str | None = None
    value_score: Decimal = Decimal(0)
