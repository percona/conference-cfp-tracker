# Conference CFP Tracker

Automatically discover open Call for Papers (CFPs), keep a local JSON database, and sync them every day.

Edith built the original tracker to sync those CFPs into Notion. This fork keeps her pipeline and points the same steps at Jira project **SPEAK** instead. The Notion script is still in `scripts/sync_notion.py`. The daily job runs `scripts/sync_jira.py`.

---

## How it works

Edith's Notion flow:

```
developers.events (public API)
        ↓
  Fetch & filter open CFPs daily
        ↓
  Local JSON database (data/events.json)
        ↓
  Sync to Notion (create / update / close)
        ↓
  Team reviews → sets status to "Slacked"
```

The same flow, now in Jira:

```
developers.events (public API)
        ↓
  Fetch & filter open CFPs daily
        ↓
  Local JSON database (data/events.json)
        ↓
  Sync to Jira SPEAK (create / update / close)
        ↓
  Team reviews on the Conference card
```

The pipeline runs every day via GitHub Actions. developers.events already lists only public, community tech conferences that have a CFP. Closed CFPs are not imported. An optional `--filter` flag can further limit events by technology and country (`scripts/filters.py`). It is off by default, so the daily run imports every open CFP, the same way the Notion sync did.

### What the sync does

SPEAK already has conferences the team added by hand. The sync only touches issues it created itself (label `cfp-sync`, **CFP Source** = `developers.events`). Everything else is left alone. That is the same rule as Notion's `[CFP] Source = developers.events`.

- **Create**: a new open CFP becomes a Conference with **CFP Status** `Open`, **CFP Source** `developers.events`, and labels `developers-events` and `cfp-sync`.
- **Update**: only source-owned fields are touched: **CFP Deadline**, **CFP Link**, and **Technology**. **Start date**, **Finish Date**, and **Due date** are updated when the event is rescheduled, unless a manager has already edited the card. Manual triage (`Slacked`, `Manual`, notes, assignee) is kept.
- **Reconcile**: a `cfp-sync` conference that disappears from the open feed gets **CFP Status** `Closed`. If nobody has triaged it (Jira status still `Open`, CFP Status still `Open`, no assignee), the Jira status moves to `Closed` too. Cards a manager already moved are not pulled back.

Matching is URL-based (lowercase host, no `www`, no query string, no trailing slash). If a conference changes its URL, a new issue is created and the old one is closed. Two CFPs that share one URL become one card; the later deadline is kept.

---

## Data source

CFP data comes from [**developers.events**](https://developers.events/), an open-source project created by [**Aurélie Vache**](https://www.linkedin.com/in/aurelievache/) and maintained by her and the community. Thank you Aurélie for making this data publicly available.

The project consumes two public JSON feeds:

- `https://developers.events/all-events.json`
- `https://developers.events/all-cfps.json`

---

## What lands in Jira

New conferences are created in project **SPEAK**, issue type **Conference**.

| Jira field | Source |
|---|---|
| Summary | Conference name |
| Conf URL | Event URL |
| CFP Link / CFP Deadline | Open CFP |
| Start date / Finish Date / Due date | Event dates. Due date matches Finish, or Start for a one-day event |
| City / Country / Offline-Online | Location |
| Technology | `tech` and `topic` tags |
| CFP Source | `developers.events` |
| CFP Status | `Open` while the CFP is in the feed, `Closed` when it drops out |
| Labels | `developers-events`, `cfp-sync` |

`cfp-sync` marks cards this job created. Conferences already in SPEAK are not overwritten.

On later runs, for those cards only:

- **CFP Deadline**, **CFP Link**, and **Technology** are updated from the feed.
- **Start date**, **Finish Date**, and **Due date** are updated when the event is rescheduled, unless the Jira history already has an edit from a manager.
- When the CFP leaves the open feed, **CFP Status** becomes `Closed`. If nobody has triaged the card (Jira status still `Open`, CFP Status still `Open`, no assignee), the Jira status moves to `Closed` as well. `Slacked` and `Manual` are left in place while the CFP is still open.

Matching is by conference URL (lowercase host, no `www`, no query string, no trailing slash). Two CFPs that share one URL become one card; the later deadline is kept.

---

## Setup

**Requirements:** Python 3.11+

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
export JIRA_URL='https://percona.atlassian.net'
export JIRA_USER='you@percona.com'
export JIRA_TOKEN='...'
```

For GitHub Actions, add `JIRA_URL`, `JIRA_USER`, and `JIRA_TOKEN` as repository secrets.

---

## Testing locally

Preview without writing:

```bash
python -m scripts.sync_jira --limit 10
```

Write to Jira:

```bash
python -m scripts.sync_jira --apply --limit 10
python -m scripts.sync_jira --apply
```

Refresh the JSON database on its own:

```bash
python -m scripts.main --limit 10
```

To try the technology and country filter:

```bash
python -m scripts.sync_jira --filter
```

---

## Automated daily run

`.github/workflows/daily-update.yml` runs every day at 04:00 UTC:

1. Refreshes `data/events.json`
2. Syncs open CFPs to Jira and closes ones that dropped out of the feed
3. Commits JSON changes back to the repo

Trigger it manually: **Actions → Daily CFP Update → Run workflow**.

---

## Repo layout

```
data/                 # Local JSON database (events.json)
scripts/
  main.py             # Fetch + merge pipeline
  fetch_data.py       # Pulls from developers.events for the JSON database
  merge_diff.py       # Compares and saves to the local DB
  open_cfps.py        # Open-CFP fetch used by the Jira sync
  sync_jira.py        # Syncs to Jira SPEAK
  filters.py          # Optional technology and country lists
  sync_notion.py      # Original Notion sync
.github/workflows/
  daily-update.yml    # Scheduled GitHub Action
```

---

## Credits

- [**Aurélie Vache**](https://www.linkedin.com/in/aurelievache/), creator of [developers.events](https://developers.events/), the open-source platform that makes this project possible.
- The [developers.events community](https://github.com/scraly/developers-conferences-agenda) for maintaining the conference data.
- Edith, who built the original Notion sync this fork is based on.
