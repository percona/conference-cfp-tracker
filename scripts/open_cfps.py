"""Fetch open CFPs from developers.events.

Same rules as conference-cfp-tracker:
- only CFPs whose untilDate is still in the future
- developers.events already limits the feed to public, community tech conferences
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import requests

EVENTS_URL = os.getenv("ALL_EVENTS_URL") or "https://developers.events/all-events.json"
CFPS_URL = os.getenv("ALL_CFPS_URL") or "https://developers.events/all-cfps.json"

_http = requests.Session()
_http.trust_env = False


def normalize_url(url: str | None) -> str:
    """Lowercase host, drop query/fragment, www, and trailing slash."""
    if not url:
        return ""
    try:
        parts = urlsplit(str(url).strip())
        scheme = (parts.scheme or "https").lower()
        netloc = (parts.netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = (parts.path or "").rstrip("/")
        return f"{scheme}://{netloc}{path}"
    except Exception:
        text = str(url).strip().lower()
        text = text.split("?", 1)[0].split("#", 1)[0]
        return text.rstrip("/")


def to_iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else value
    return None


def tag_names(tags: Any, *, keys: tuple[str, ...] = ("tech", "topic")) -> list[str]:
    """Technology labels from developers.events tags. Language/location stay out."""
    names: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        if isinstance(tag, str):
            value = tag
        elif isinstance(tag, dict):
            if keys and tag.get("key") not in keys:
                continue
            value = tag.get("value") or tag.get("name") or ""
        else:
            continue
        text = str(value).strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        names.append(text)
    return names


def _date_range(value: Any) -> tuple[Any, Any]:
    if isinstance(value, (list, tuple)):
        start = value[0] if len(value) >= 1 else None
        end = value[1] if len(value) >= 2 else None
        return start, end
    return None, None


def fetch_open_cfps() -> list[dict[str, Any]]:
    events_resp = _http.get(EVENTS_URL, timeout=60)
    events_resp.raise_for_status()
    cfps_resp = _http.get(CFPS_URL, timeout=60)
    cfps_resp.raise_for_status()
    events_raw = events_resp.json()
    cfps_raw = cfps_resp.json()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    open_cfps = [c for c in cfps_raw if c.get("untilDate") and c["untilDate"] > now_ms]

    by_link: dict[str, dict[str, Any]] = {}
    for event in events_raw:
        key = normalize_url(event.get("hyperlink")) or (event.get("name") or "").strip().casefold()
        if key:
            by_link[key] = event

    cleaned: list[dict[str, Any]] = []
    for cfp in open_cfps:
        conf = cfp.get("conf") or {}
        key = normalize_url(conf.get("hyperlink")) or (conf.get("name") or "").strip().casefold()
        event = by_link.get(key) or {}
        conf_start, conf_end = _date_range(conf.get("date"))
        ev_start, ev_end = _date_range(event.get("date"))
        hyperlink = conf.get("hyperlink") or event.get("hyperlink") or ""
        item = {
            "name": conf.get("name") or event.get("name") or "",
            "hyperlink": hyperlink,
            "url_key": normalize_url(hyperlink),
            "cfp_url": cfp.get("link") or "",
            "cfp_close": to_iso_date(cfp.get("untilDate")),
            "event_start": to_iso_date(conf_start or ev_start),
            "event_end": to_iso_date(conf_end or ev_end),
            "location": conf.get("location") or event.get("location") or "",
            "city": event.get("city") or "",
            "country": event.get("country") or "",
            "source": "developers.events",
            "tags": tag_names(event.get("tags")),
        }
        if item["name"] and item["url_key"]:
            cleaned.append(item)
    return _dedupe_by_url(cleaned)


def _dedupe_by_url(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One conference per URL. Keep the CFP that closes later; remember the other links."""
    by_url: dict[str, dict[str, Any]] = {}
    for event in events:
        key = event["url_key"]
        current = by_url.get(key)
        if current is None:
            event["cfp_urls"] = [event["cfp_url"]] if event.get("cfp_url") else []
            by_url[key] = event
            continue
        links = list(current.get("cfp_urls") or [])
        if event.get("cfp_url") and event["cfp_url"] not in links:
            links.append(event["cfp_url"])
        # Prefer the later deadline so a still-open track is not hidden by an earlier one.
        if (event.get("cfp_close") or "") > (current.get("cfp_close") or ""):
            event["cfp_urls"] = links
            by_url[key] = event
        else:
            current["cfp_urls"] = links
    return list(by_url.values())


def sanitize_label(value: str) -> str:
    text = str(value).strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text
