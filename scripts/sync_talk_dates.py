#!/usr/bin/env python3
"""Keep SPEAK Talk calendar dates aligned.

Managers often create a Talk and fill only one of Start date or Time.
Jira's calendar uses the built-in Due date.

- Due date follows Start date.
- If Time is set and Start date is empty, Start date becomes the date part
  of Time, and Due date follows that day.
- A Talk with neither Start date nor Time is left alone.

Usage:
  python -m scripts.sync_talk_dates           # dry-run
  python -m scripts.sync_talk_dates --apply   # write to Jira
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

_raw_jira_url = (os.getenv("JIRA_URL") or "").rstrip("/")
if _raw_jira_url and not _raw_jira_url.startswith(("http://", "https://")):
    _raw_jira_url = f"https://{_raw_jira_url}"
JIRA_URL = _raw_jira_url
JIRA_USER = os.getenv("JIRA_USER")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

PROJECT_KEY = "SPEAK"
START_DATE = "customfield_11901"
TIME = "customfield_11931"

JQL = (
    f'project = {PROJECT_KEY} AND issuetype = Talk '
    'AND ("Start date" IS NOT EMPTY OR Time IS NOT EMPTY) '
    "ORDER BY updated DESC"
)

_http = requests.Session()
_http.trust_env = False


def require_env() -> None:
    missing = [
        name
        for name, value in (("JIRA_URL", JIRA_URL), ("JIRA_USER", JIRA_USER), ("JIRA_TOKEN", JIRA_TOKEN))
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")


def auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(JIRA_USER or "", JIRA_TOKEN or "")


def headers() -> dict[str, str]:
    return {"Accept": "application/json", "Content-Type": "application/json"}


def date_only(value: Any) -> str:
    """YYYY-MM-DD from a Jira date or datetime. Empty when the value has no date."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def search_talks() -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    token = None
    while True:
        payload: dict[str, Any] = {
            "jql": JQL,
            "maxResults": 100,
            "fields": ["summary", "duedate", START_DATE, TIME],
        }
        if token:
            payload["nextPageToken"] = token
        response = _http.post(
            f"{JIRA_URL}/rest/api/3/search/jql",
            headers=headers(),
            auth=auth(),
            json=payload,
            timeout=120,
        )
        if not response.ok:
            raise RuntimeError(f"{response.status_code} {response.text[:500]}")
        data = response.json()
        batch = data.get("issues") or []
        issues.extend(batch)
        token = data.get("nextPageToken")
        if not token or not batch:
            break
    return issues


def update_issue(key: str, fields: dict[str, str]) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Align Talk Due date with Start date.")
    parser.add_argument("--apply", action="store_true", help="Write to Jira. Default is dry-run.")
    args = parser.parse_args()

    require_env()
    started = datetime.now(timezone.utc)
    print(f"Talk date sync started at {started.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if not args.apply:
        print("Dry-run. Pass --apply to write.")

    issues = search_talks()
    print(f"Talks with Start date or Time: {len(issues)}")

    updated = already = errors = 0
    for issue in issues:
        key = issue.get("key") or "?"
        fields = issue.get("fields") or {}
        summary = fields.get("summary") or ""
        start = date_only(fields.get(START_DATE))
        time_date = date_only(fields.get(TIME))
        due = date_only(fields.get("duedate"))

        payload: dict[str, str] = {}
        if not start and time_date:
            start = time_date
            payload[START_DATE] = time_date
        if start and due != start:
            payload["duedate"] = start
        if not payload:
            already += 1
            continue

        bits = []
        if START_DATE in payload:
            bits.append(f"start←time {payload[START_DATE]}")
        if "duedate" in payload:
            bits.append(f"due {due or 'empty'} → {payload['duedate']}")
        print(f"{'SET' if args.apply else 'WOULD'} {key} {' | '.join(bits)} | {summary}")
        if not args.apply:
            updated += 1
            continue
        try:
            update_issue(key, payload)
            updated += 1
            time.sleep(0.2)
        except Exception as exc:
            errors += 1
            print(f"  ERROR {key}: {exc}")

    print("\nSummary:")
    print(f"| talks: {len(issues)}")
    print(f"| updated: {updated}")
    print(f"| already ok: {already}")
    print(f"| errors: {errors}")
    if not args.apply:
        print("| dry-run")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
