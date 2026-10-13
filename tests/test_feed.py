from datetime import timedelta

import httpx

from dealwatch.feed import parse_feed, poll_once, retry_delay, revision_hash


def test_real_fixture(feed_bytes):
    deals = parse_feed(feed_bytes)
    assert len(deals) == 30
    iphone = next(d for d in deals if d.node_id == 978852)
    assert iphone.merchant_url.startswith("https://www.costco.com.au/")
    assert iphone.votes_pos == 18 and iphone.clicks == 172
    assert iphone.starting and iphone.expiry and iphone.image
    assert "brand" in iphone.category_types
    assert {d.status for d in deals} >= {None, "expired", "upcoming"}
    assert all("<p>" not in d.summary for d in deals)


def test_namespace_prefix_is_irrelevant(feed_bytes):
    changed = feed_bytes.replace(b"xmlns:ozb=", b"xmlns:x=").replace(b"ozb:", b"x:")
    assert parse_feed(changed) == parse_feed(feed_bytes)


async def test_conditional_get_and_304(db, config, feed_bytes, now):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                content=feed_bytes,
                headers={"ETag": '"abc"', "Last-Modified": "Mon, 12 Oct 2026 00:00:00 GMT"},
            )
        assert request.headers["if-none-match"] == '"abc"'
        assert "if-modified-since" in request.headers
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await poll_once(client, db, config, now) == 30
        assert await poll_once(client, db, config, now + timedelta(seconds=120)) == 0
    assert db.conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 30
    assert len(requests) == 2


def test_revisions_ignore_votes_but_detect_price(deal):
    assert revision_hash(deal) == revision_hash(deal.model_copy(update={"votes_pos": 500}))
    assert revision_hash(deal) != revision_hash(
        deal.model_copy(update={"title": deal.title.replace("100", "90")})
    )


def test_retry_after(now):
    assert retry_delay({"retry-after": "3600"}, 1, now) == 3600
    assert retry_delay({}, 20, now) == 1800
    assert retry_delay({"retry-after": "Tue, 13 Oct 2026 15:00:00 GMT"}, 1, now) == 3600
