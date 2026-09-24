#!/usr/bin/env python3
"""Sync open developers.events CFPs into Jira project SPEAK.

Same pipeline as this repo's Notion sync, with Jira as the destination:

- Fetch only CFPs that are still open (untilDate in the future).
- By default import every open CFP. Pass --filter to keep only events whose
  technology matches the Percona list and whose country is one SPEAK already tracks.
- Create a Conference when that URL is not already in SPEAK.
- Update CFP Deadline, CFP Link, and Technology only on issues this sync created
  (label ``cfp-sync``). Existing conferences are left alone.
- Start date, Finish Date, and Due date are refreshed when the event is
  rescheduled, unless the Jira history already has an edit from a manager.
- When a synced CFP drops out of the open feed, set CFP Status to Closed.
  If nobody has triaged the card (workflow still Open, CFP Status still Open,
  no assignee), also move the Jira status to Closed.

Historical closed CFPs are never imported.

Usage:
  python -m scripts.sync_jira              # dry-run
  python -m scripts.sync_jira --apply      # write to Jira
  python -m scripts.sync_jira --apply --limit 5
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from scripts.filters import event_matches
from scripts.open_cfps import fetch_open_cfps, normalize_url, sanitize_label

_raw_jira_url = (os.getenv("JIRA_URL") or "").rstrip("/")
if _raw_jira_url and not _raw_jira_url.startswith(("http://", "https://")):
    _raw_jira_url = f"https://{_raw_jira_url}"
JIRA_URL = _raw_jira_url
JIRA_USER = os.getenv("JIRA_USER")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

PROJECT_KEY = "SPEAK"
ISSUE_TYPE = "Conference"
OWNED_LABEL = "cfp-sync"
SOURCE_LABEL = "developers-events"

CONF_URL = "customfield_11932"
CFP_LINK = "customfield_11919"
CFP_DEADLINE = "customfield_11918"
START_DATE = "customfield_11901"
FINISH_DATE = "customfield_11924"
CITY = "customfield_11920"
COUNTRY = "customfield_11921"
OFFLINE_ONLINE = "customfield_11936"
TECHNOLOGY = "customfield_13521"
CFP_SOURCE = "customfield_13508"
CFP_STATUS = "customfield_13509"

_http = requests.Session()
_http.trust_env = False


def require_env() -> None:
    missing = [name for name, value in (("JIRA_URL", JIRA_URL), ("JIRA_USER", JIRA_USER), ("JIRA_TOKEN", JIRA_TOKEN)) if not value]
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")


def auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(JIRA_USER or "", JIRA_TOKEN or "")


def headers() -> dict[str, str]:
    return {"Accept": "application/json", "Content-Type": "application/json"}


def jira_search(jql: str, fields: list[str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    token = None
    while True:
        payload: dict[str, Any] = {"jql": jql, "maxResults": 100, "fields": fields}
        if token:
            payload["nextPageToken"] = token
        response = _http.post(
            f"{JIRA_URL}/rest/api/3/search/jql",
            headers=headers(),
            auth=auth(),
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        batch = data.get("issues") or []
        issues.extend(batch)
        token = data.get("nextPageToken")
        if not token or not batch:
            break
    return issues


def option(value: str) -> dict[str, str]:
    return {"value": value}


def text_to_adf(text: str) -> dict[str, Any]:
    content = []
    for line in text.split("\n"):
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
        )
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph", "content": []}]}


def is_online(event: dict[str, Any]) -> bool:
    blob = " ".join(
        str(event.get(key) or "") for key in ("location", "city", "country", "name")
    ).casefold()
    return any(marker in blob for marker in ("online", "virtual", "remote", "webinar"))


def city_value(event: dict[str, Any]) -> str | None:
    city = str(event.get("city") or "").strip()
    if not city:
        location = str(event.get("location") or "").strip()
        if "(" in location:
            city = location.split("(", 1)[0].strip(" ,")
        else:
            city = location
    if not city or is_online({"location": city, "city": city, "country": "", "name": ""}):
        return None
    return city


def country_labels(event: dict[str, Any]) -> list[str] | None:
    country = str(event.get("country") or "").strip()
    if not country:
        location = str(event.get("location") or "")
        if "(" in location and location.endswith(")"):
            country = location[location.rfind("(") + 1 : -1].strip()
    label = sanitize_label(country)
    if not label or label.casefold() in {"online", "virtual", "remote"}:
        return None
    return [label]


def tech_labels(event: dict[str, Any]) -> list[str] | None:
    labels = []
    for name in event.get("tags") or []:
        label = sanitize_label(name)
        if label:
            labels.append(label)
    deduped = list(dict.fromkeys(labels))
    return deduped or None


def description(event: dict[str, Any]) -> dict[str, Any]:
    lines = ["Imported from developers.events."]
    location = str(event.get("location") or "").strip()
    if location:
        lines.append(f"Location: {location}")
    links = event.get("cfp_urls") or ([event["cfp_url"]] if event.get("cfp_url") else [])
    for link in links:
        lines.append(f"CFP: {link}")
    return text_to_adf("\n".join(lines))


def create_fields(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("event_start")
    finish = event.get("event_end") or start
    fields: dict[str, Any] = {
        "project": {"key": PROJECT_KEY},
        "issuetype": {"name": ISSUE_TYPE},
        "summary": str(event.get("name") or "")[:255],
        CONF_URL: event.get("hyperlink"),
        CFP_SOURCE: option("developers.events"),
        CFP_STATUS: option("Open"),
        "labels": [SOURCE_LABEL, OWNED_LABEL],
        "description": description(event),
    }
    if event.get("cfp_url"):
        fields[CFP_LINK] = event["cfp_url"]
    if event.get("cfp_close"):
        fields[CFP_DEADLINE] = event["cfp_close"]
    if start:
        fields[START_DATE] = start
    if finish:
        fields[FINISH_DATE] = finish
        fields["duedate"] = finish
    city = city_value(event)
    if city:
        fields[CITY] = city
    country = country_labels(event)
    if country:
        fields[COUNTRY] = country
    fields[OFFLINE_ONLINE] = option("Online" if is_online(event) else "Offline")
    tech = tech_labels(event)
    if tech:
        fields[TECHNOLOGY] = tech
    return fields


def update_fields(
    event: dict[str, Any],
    existing_tech: list[str] | None,
    existing_cfp_status: str | None,
) -> dict[str, Any]:
    """Source-owned fields only, same idea as the Notion sync."""
    fields: dict[str, Any] = {}
    if event.get("cfp_close"):
        fields[CFP_DEADLINE] = event["cfp_close"]
    if event.get("cfp_url"):
        fields[CFP_LINK] = event["cfp_url"]
    incoming = tech_labels(event) or []
    merged = list(dict.fromkeys([*(existing_tech or []), *incoming]))
    if merged:
        fields[TECHNOLOGY] = merged
    # Re-open only a card we previously closed. Leave Slacked / Manual alone.
    if (existing_cfp_status or "Open") == "Closed":
        fields[CFP_STATUS] = option("Open")
    return fields


def load_conferences() -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    issues = jira_search(
        f'project = {PROJECT_KEY} AND issuetype = "{ISSUE_TYPE}" ORDER BY key ASC',
        [
            "summary",
            "labels",
            "status",
            "assignee",
            CONF_URL,
            CFP_STATUS,
            CFP_DEADLINE,
            CFP_LINK,
            TECHNOLOGY,
            START_DATE,
            FINISH_DATE,
            "duedate",
        ],
    )
    by_url: dict[str, dict[str, Any]] = {}
    by_name_start: dict[tuple[str, str], str] = {}
    for issue in issues:
        fields = issue.get("fields") or {}
        url_key = normalize_url(fields.get(CONF_URL))
        record = {
            "key": issue["key"],
            "summary": fields.get("summary") or "",
            "labels": fields.get("labels") or [],
            "url_key": url_key,
            "status": ((fields.get("status") or {}) or {}).get("name") or "",
            "assignee": (fields.get("assignee") or {}).get("accountId"),
            "cfp_status": ((fields.get(CFP_STATUS) or {}) or {}).get("value"),
            "cfp_deadline": fields.get(CFP_DEADLINE),
            "cfp_link": fields.get(CFP_LINK),
            "technology": fields.get(TECHNOLOGY) or [],
            "start": fields.get(START_DATE),
            "finish": fields.get(FINISH_DATE),
            "due": fields.get("duedate"),
        }
        if url_key and url_key not in by_url:
            by_url[url_key] = record
        name = (fields.get("summary") or "").strip().casefold()
        start = fields.get(START_DATE) or ""
        if name and start:
            by_name_start.setdefault((name, start), issue["key"])
    return by_url, by_name_start


_sync_account_id: str | None = None

# Fields this sync writes after create. Anything else in the changelog is a person.
_SYNC_FIELD_IDS = {CFP_DEADLINE, CFP_LINK, TECHNOLOGY, CFP_STATUS}


def sync_account_id() -> str:
    global _sync_account_id
    if _sync_account_id:
        return _sync_account_id
    response = _http.get(
        f"{JIRA_URL}/rest/api/3/myself",
        headers=headers(),
        auth=auth(),
        timeout=60,
    )
    response.raise_for_status()
    _sync_account_id = (response.json() or {}).get("accountId") or ""
    return _sync_account_id


def schedule_changes(event: dict[str, Any], existing: dict[str, Any]) -> dict[str, str]:
    """Start, finish, and calendar due date when developers.events moved the event."""
    start = event.get("event_start")
    finish = event.get("event_end") or start
    fields: dict[str, str] = {}
    if start and existing.get("start") != start:
        fields[START_DATE] = start
    if finish and existing.get("finish") != finish:
        fields[FINISH_DATE] = finish
    if finish and existing.get("due") != finish:
        fields["duedate"] = finish
    return fields


def _change_is_ours(item: dict[str, Any], *, author_is_sync: bool) -> bool:
    if not author_is_sync:
        return False
    field_id = item.get("fieldId") or ""
    if field_id in _SYNC_FIELD_IDS:
        return True
    if field_id == "status" or item.get("field") == "status":
        return (item.get("toString") or "") == "Closed"
    return False


def manager_edited(issue_key: str) -> bool:
    """True when the changelog has an edit this sync did not make."""
    account_id = sync_account_id()
    start_at = 0
    while True:
        response = _http.get(
            f"{JIRA_URL}/rest/api/3/issue/{issue_key}/changelog",
            headers=headers(),
            auth=auth(),
            params={"startAt": start_at, "maxResults": 100},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        histories = data.get("values") or []
        for history in histories:
            author = ((history.get("author") or {}).get("accountId")) or ""
            ours = bool(account_id) and author == account_id
            for item in history.get("items") or []:
                if not _change_is_ours(item, author_is_sync=ours):
                    return True
        if data.get("isLast", True) or not histories:
            return False
        start_at += len(histories)


def create_issue(fields: dict[str, Any]) -> dict[str, Any]:
    response = _http.post(
        f"{JIRA_URL}/rest/api/3/issue",
        headers=headers(),
        auth=auth(),
        json={"fields": fields},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"{response.status_code} {response.text[:500]}")
    return response.json()


def update_issue(key: str, fields: dict[str, Any]) -> None:
    response = _http.put(
        f"{JIRA_URL}/rest/api/3/issue/{key}",
        headers=headers(),
        auth=auth(),
        params={"notifyUsers": "false"},
        json={"fields": fields},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"{response.status_code} {response.text[:500]}")


def owned(record: dict[str, Any]) -> bool:
    return OWNED_LABEL in (record.get("labels") or [])


# Manager already touched the card. Leave the Jira workflow status alone.
TRIAGED_CFP_STATUSES = frozenset({"Slacked", "Manual", "Not Applicable"})


def untouched(record: dict[str, Any]) -> bool:
    """True when managers have not moved, triaged, or assigned the conference."""
    if (record.get("status") or "") != "Open":
        return False
    if record.get("assignee"):
        return False
    cfp_status = record.get("cfp_status") or "Open"
    return cfp_status not in TRIAGED_CFP_STATUSES


def transition_to_closed(key: str) -> None:
    response = _http.get(
        f"{JIRA_URL}/rest/api/3/issue/{key}/transitions",
        headers=headers(),
        auth=auth(),
        timeout=60,
    )
    response.raise_for_status()
    transition_id = next(
        (
            item["id"]
            for item in response.json().get("transitions") or []
            if ((item.get("to") or {}).get("name") == "Closed")
        ),
        None,
    )
    if not transition_id:
        raise RuntimeError(f"{key} has no transition to Closed")
    response = _http.post(
        f"{JIRA_URL}/rest/api/3/issue/{key}/transitions",
        headers=headers(),
        auth=auth(),
        json={"transition": {"id": transition_id}},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"{response.status_code} {response.text[:500]}")


def delete_issue(key: str) -> None:
    response = _http.delete(
        f"{JIRA_URL}/rest/api/3/issue/{key}",
        headers=headers(),
        auth=auth(),
        timeout=60,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"{response.status_code} {response.text[:500]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync open developers.events CFPs to Jira SPEAK.")
    parser.add_argument("--apply", action="store_true", help="Write to Jira. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=None, help="Create at most N new conferences.")
    parser.add_argument("--no-reconcile", action="store_true", help="Do not mark missing cfp-sync issues Closed.")
    parser.add_argument(
        "--filter",
        action="store_true",
        help="Import only CFPs that match the technology and country lists in cfp/filters.py.",
    )
    args = parser.parse_args()

    require_env()
    started = datetime.now(timezone.utc)
    print(f"CFP sync started at {started.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if not args.apply:
        print("Dry-run. Pass --apply to write.")

    fetched = fetch_open_cfps()
    if args.filter:
        events = [event for event in fetched if event_matches(event)]
        kept = {event["url_key"] for event in events}
        filtered_keys = {event["url_key"] for event in fetched if event["url_key"] not in kept}
    else:
        events = fetched
        filtered_keys = set()
    by_url, by_name_start = load_conferences()
    filter_note = f" | after tech+country filter: {len(events)}" if args.filter else ""
    print(f"Open CFPs: {len(fetched)}{filter_note} | Jira conferences with URL: {len(by_url)}")

    created = updated = skipped = closed = removed = errors = 0
    open_keys = {event["url_key"] for event in events}
    creates_done = 0

    for event in events:
        url_key = event["url_key"]
        existing = by_url.get(url_key)
        if existing is None:
            name_key = (str(event.get("name") or "").strip().casefold(), event.get("event_start") or "")
            other_key = by_name_start.get(name_key) if name_key[0] and name_key[1] else None
            if other_key:
                print(f"SKIP name+date already in Jira: {event['name']} ({other_key})")
                skipped += 1
                continue
            if args.limit is not None and creates_done >= args.limit:
                continue
            print(f"CREATE {event['name']} | {event.get('cfp_close')} | {event.get('hyperlink')}")
            creates_done += 1
            if not args.apply:
                created += 1
                continue
            try:
                issue = create_issue(create_fields(event))
                print(f"  -> {issue.get('key')}")
                created += 1
                time.sleep(0.2)
            except Exception as exc:
                errors += 1
                print(f"  ERROR {event['name']}: {exc}")
            continue

        if not owned(existing):
            skipped += 1
            continue

        fields = update_fields(event, existing.get("technology"), existing.get("cfp_status"))
        date_fields = schedule_changes(event, existing)
        if date_fields and manager_edited(existing["key"]):
            print(
                f"SKIP dates {existing['key']} {event['name']} "
                f"({existing.get('start')} → {event.get('event_start')}; manager edited history)"
            )
            date_fields = {}
        elif date_fields:
            fields.update(date_fields)
        cfp_changed = (
            existing.get("cfp_deadline") != event.get("cfp_close")
            or normalize_url(existing.get("cfp_link")) != normalize_url(event.get("cfp_url"))
            or existing.get("cfp_status") == "Closed"
        )
        if not cfp_changed and not date_fields:
            continue
        moved = ""
        if date_fields:
            moved = f" dates {existing.get('start')} → {event.get('event_start')}"
        print(f"UPDATE {existing['key']} {event['name']}{moved}")
        if not args.apply:
            updated += 1
            continue
        try:
            update_issue(existing["key"], fields)
            updated += 1
            time.sleep(0.2)
        except Exception as exc:
            errors += 1
            print(f"  ERROR {existing['key']}: {exc}")

    if not args.no_reconcile:
        for record in by_url.values():
            if not owned(record):
                continue
            if record["url_key"] in open_keys:
                continue
            # Created earlier, but technology or country is outside the filter.
            if record["url_key"] in filtered_keys:
                print(f"DELETE {record['key']} {record['summary']} (outside tech/country filter)")
                if not args.apply:
                    removed += 1
                    continue
                try:
                    delete_issue(record["key"])
                    removed += 1
                    time.sleep(0.2)
                except Exception as exc:
                    errors += 1
                    print(f"  ERROR {record['key']}: {exc}")
                continue
            if record.get("cfp_status") == "Closed":
                continue
            also_workflow = untouched(record)
            note = " + Jira status Closed" if also_workflow else " (Jira status left as-is)"
            print(f"CLOSE {record['key']} {record['summary']}{note}")
            if not args.apply:
                closed += 1
                continue
            try:
                update_issue(record["key"], {CFP_STATUS: option("Closed")})
                if also_workflow:
                    transition_to_closed(record["key"])
                closed += 1
                time.sleep(0.2)
            except Exception as exc:
                errors += 1
                print(f"  ERROR {record['key']}: {exc}")

    print("\nSummary:")
    print(f"| open cfps: {len(fetched)}")
    if args.filter:
        print(f"| matched filter: {len(events)}")
    print(f"| created: {created}")
    print(f"| updated: {updated}")
    print(f"| skipped (already in Jira, not owned): {skipped}")
    print(f"| removed: {removed}")
    print(f"| closed: {closed}")
    print(f"| errors: {errors}")
    if not args.apply:
        print("| dry-run")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
