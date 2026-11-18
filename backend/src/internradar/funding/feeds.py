"""RSS feeds of Indian startup funding news (all verified on 2026-11-18)."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

FEEDS = {
    "entrackr": "https://entrackr.com/rss",
    "inc42": "https://inc42.com/feed/",
    "yourstory": "https://yourstory.com/category/funding/feed",
    "indianstartupnews": "https://indianstartupnews.com/rss",
}

# TechCrunch and others reject browser-like user agents but accept an honest bot string.
USER_AGENT = "InternRadar/0.1 (+https://github.com/Chhavi07-arch/internradar)"


@dataclass(frozen=True, slots=True)
class FeedItem:
    feed: str
    title: str
    url: str
    published_at: datetime | None


def _published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def parse_rss(feed: str, content: bytes) -> list[FeedItem]:
    items = []
    for item in ET.fromstring(content).iter("item"):
        title = " ".join((item.findtext("title") or "").split())
        url = (item.findtext("link") or "").strip()
        if title and url:
            items.append(FeedItem(feed, title, url, _published(item.findtext("pubDate"))))
    return items


def fetch_feed(client: httpx.Client, feed: str, url: str) -> list[FeedItem]:
    response = client.get(url)
    response.raise_for_status()
    return parse_rss(feed, response.content)
