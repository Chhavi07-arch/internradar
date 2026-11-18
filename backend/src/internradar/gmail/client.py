"""Minimal read-only Gmail REST client plus MIME decoding."""

import base64
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"


@dataclass(frozen=True, slots=True)
class Email:
    gmail_id: str
    thread_id: str
    sender: str  # bare address, lowercased
    subject: str
    received_at: datetime
    body: str  # plain text; links kept as "text (url)"


class GmailClient:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30)
        self._headers = {"Authorization": f"Bearer {token}"}

    def profile_email(self) -> str:
        return self._get("/profile")["emailAddress"]

    def list_ids(self, query: str, limit: int = 500) -> list[str]:
        ids: list[str] = []
        page_token = None
        while len(ids) < limit:
            params = {"q": query, "maxResults": min(100, limit - len(ids))}
            if page_token:
                params["pageToken"] = page_token
            data = self._get("/messages", params)
            ids += [m["id"] for m in data.get("messages", [])]
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids

    def get(self, gmail_id: str) -> Email:
        return parse_message(self._get(f"/messages/{gmail_id}", {"format": "full"}))

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._http.get(API + path, params=params, headers=self._headers)
        response.raise_for_status()
        return response.json()


def parse_message(message: dict[str, Any]) -> Email:
    payload = message["payload"]
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, rich = _bodies(payload)
    # Prefer the plain part unless it lost the links that only the HTML part carries.
    body = plain if plain and ("http" in plain or not rich) else html_to_text(rich or "")
    return Email(
        gmail_id=message["id"],
        thread_id=message["threadId"],
        sender=parseaddr(headers.get("from", ""))[1].lower(),
        subject=headers.get("subject", "").strip(),
        received_at=datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC),
        body=body.strip(),
    )


def _bodies(part: dict[str, Any]) -> tuple[str | None, str | None]:
    """First text/plain and first text/html body found anywhere in the MIME tree."""
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data and mime in ("text/plain", "text/html"):
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        return (text, None) if mime == "text/plain" else (None, text)
    plain = rich = None
    for child in part.get("parts", []):
        child_plain, child_rich = _bodies(child)
        plain, rich = plain or child_plain, rich or child_rich
    return plain, rich


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    # Keep every link target: "Apply here" alone is useless, the URL is the opportunity.
    text = re.sub(
        r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        # Parentheses, not angle brackets: the tag stripping below would delete "<url>".
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})",
        text,
    )
    text = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h\d)\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
