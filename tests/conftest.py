from datetime import datetime, timezone
from pathlib import Path

import pytest

from dealwatch.config import Config
from dealwatch.db import Store
from dealwatch.models import Deal, Extraction


@pytest.fixture
def now():
    return datetime(2026, 10, 13, 14, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path):
    return Config(_env_file=None, data_dir=tmp_path, dry_run=False)


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "dealwatch.db")
    yield store
    store.close()


@pytest.fixture
def deal(now):
    return Deal(
        node_id=123,
        title="Acme X1 8TB HDD $100 Delivered — Price Error?",
        published=now,
        summary="Was $200 at JB Hi-Fi last week.",
        deal_url="https://www.ozbargain.com.au/node/123",
        merchant_url="https://example.com/item",
    )


@pytest.fixture
def extraction():
    return Extraction(
        brand="Acme",
        model="X1",
        variant="8TB",
        identity_quote="Acme X1 8TB HDD",
        price=100,
        price_quote="$100",
        condition="new",
        shipping=0,
        shipping_quote="Delivered",
        signal="price_error",
        signal_quote="Price Error",
    )


@pytest.fixture
def feed_bytes():
    return (Path(__file__).parent / "fixtures/ozb_feed_2026-10-13.xml").read_bytes()
