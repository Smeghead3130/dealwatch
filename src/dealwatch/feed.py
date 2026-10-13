import hashlib
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from lxml import etree, html

from .models import Deal

NS = {"ozb": "https://www.ozbargain.com.au"}


def clean_html(value):
    try:
        root = html.fromstring(value or "<p></p>")
        for el in root.xpath("//script|//style"):
            el.drop_tree()
        return " ".join(root.text_content().split())[:12000]
    except (ValueError, etree.ParserError):
        return ""


def iso(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_feed(data: bytes) -> list[Deal]:
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected RSS channel")
    deals = []
    for item in root.findall("channel/item"):
        link = item.findtext("link", "")
        node = re.search(r"/node/(\d+)", link)
        if not node:
            continue
        meta = item.find("ozb:meta", NS)
        attr = meta.attrib if meta is not None else {}
        status = item.find("ozb:title-msg", NS)

        def integer(key):
            try:
                return max(0, int(attr.get(key, 0)))
            except ValueError:
                return 0

        try:
            published = parsedate_to_datetime(item.findtext("pubDate", ""))
            if not published.tzinfo:
                published = published.replace(tzinfo=timezone.utc)
            deals.append(
                Deal(
                    node_id=int(node[1]),
                    title=item.findtext("title", ""),
                    summary=clean_html(item.findtext("description", "")),
                    deal_url=link,
                    categories=[x.text or "" for x in item.findall("category")],
                    category_types=[
                        urlsplit(x.get("domain", "")).path.strip("/").split("/")[0]
                        for x in item.findall("category")
                    ],
                    merchant_url=attr.get("url"),
                    image=attr.get("image"),
                    published=published,
                    expiry=iso(attr.get("expiry")),
                    starting=iso(attr.get("starting")),
                    status=status.get("type") if status is not None else None,
                    votes_pos=integer("votes-pos"),
                    votes_neg=integer("votes-neg"),
                    clicks=integer("click-count"),
                    comments=integer("comment-count"),
                )
            )
        except (ValueError, TypeError):
            continue
    if root.findall("channel/item") and not deals:
        raise ValueError("Feed items could not be parsed")
    return deals


def revision_hash(deal):
    content = deal.model_dump(mode="json", exclude={"votes_pos", "votes_neg", "clicks", "comments"})
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def retry_delay(headers, failures, now):
    raw = headers.get("retry-after", "")
    try:
        requested = float(raw)
    except ValueError:
        try:
            requested = (parsedate_to_datetime(raw) - now).total_seconds()
        except (ValueError, TypeError):
            requested = 0
    return max(0, requested, min(1800, 120 * 2 ** min(failures - 1, 8)))


async def poll_once(client, db, config, now=None):
    from .models import utcnow

    now = now or utcnow()
    headers = {"User-Agent": config.user_agent}
    for key, header in (("etag", "If-None-Match"), ("last_modified", "If-Modified-Since")):
        if value := db.meta(key):
            headers[header] = value
    response = await client.get(config.feed_url, headers=headers, timeout=30)
    if response.status_code == 304:
        db.set_meta("last_poll", now.isoformat())
        db.release_upcoming(now)
        return 0
    response.raise_for_status()
    deals = parse_feed(response.content)
    db.ingest(deals, now)
    db.set_meta("etag", response.headers.get("etag", ""))
    db.set_meta("last_modified", response.headers.get("last-modified", ""))
    db.set_meta("last_poll", now.isoformat())
    return len(deals)
