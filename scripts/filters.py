"""Keep developers.events CFPs that match Percona's stack and the countries already used in SPEAK.

An event is imported only when both are true:
- technology: a tag or the conference name contains a database / Kubernetes / Linux term
- country: it is in the SPEAK country list, or the feed has no country (online events)
"""

from __future__ import annotations

import re
from typing import Any

# Whole tokens. "data" is included so DataSaturday / SQL data events stay;
# it does not match inside unrelated words.
TECH_TOKENS = frozenset(
    {
        "mysql",
        "mariadb",
        "percona",
        "postgres",
        "postgresql",
        "mongodb",
        "mongo",
        "database",
        "databases",
        "sql",
        "kubernetes",
        "k8s",
        "kubecon",
        "cloud-native",
        "linux",
        "observability",
        "data",
    }
)

# Countries that already show up as a real slice of the SPEAK conference catalog.
COUNTRIES = frozenset(
    {
        "usa",
        "germany",
        "uk",
        "france",
        "netherlands",
        "india",
        "russia",
        "italy",
        "spain",
        "canada",
        "poland",
        "belgium",
        "japan",
        "ukraine",
        "brazil",
        "australia",
        "switzerland",
        "norway",
        "sweden",
        "ireland",
        "israel",
        "portugal",
        "mexico",
        "czechia",
        "austria",
        "denmark",
        "finland",
        "hungary",
        "romania",
        "singapore",
        "greece",
    }
)

_COUNTRY_ALIASES = {
    "united states": "usa",
    "united states of america": "usa",
    "us": "usa",
    "u.s.": "usa",
    "u.s.a.": "usa",
    "united kingdom": "uk",
    "great britain": "uk",
    "england": "uk",
    "the netherlands": "netherlands",
    "holland": "netherlands",
    "czech republic": "czechia",
    "russian federation": "russia",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.casefold()))


def technology_hits(event: dict[str, Any]) -> list[str]:
    blob = " ".join(
        [
            str(event.get("name") or ""),
            " ".join(str(tag) for tag in (event.get("tags") or [])),
        ]
    )
    tokens = _tokens(blob)
    found = [
        term
        for term in sorted(TECH_TOKENS)
        if term in tokens or any(term in token for token in tokens)
    ]
    return found


def canonical_country(event: dict[str, Any]) -> str:
    raw = str(event.get("country") or "").strip().casefold()
    if not raw:
        return ""
    return _COUNTRY_ALIASES.get(raw, raw)


def country_allowed(event: dict[str, Any]) -> bool:
    country = canonical_country(event)
    if not country or country in {"online", "virtual", "remote"}:
        return True
    return country in COUNTRIES


def event_matches(event: dict[str, Any]) -> bool:
    return bool(technology_hits(event)) and country_allowed(event)
