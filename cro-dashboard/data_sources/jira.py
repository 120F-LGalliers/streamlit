import datetime
from collections import defaultdict
from statistics import mean, median as _median
from typing import Optional

import requests
import streamlit as st
from requests.auth import HTTPBasicAuth

MONTHS_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _parse_jira_timestamp(s: str) -> Optional[datetime.datetime]:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    # Handle rare timezone colon edge case (e.g. +05:30 → +0530)
    try:
        return datetime.datetime.strptime(s[:-3] + s[-2:], "%Y-%m-%dT%H:%M:%S.%f%z")
    except Exception:
        return None


def _velocity_status(count: int, target: int) -> str:
    if count > target:
        return "exceeding"
    elif count == target:
        return "on_track"
    return "behind"


def _extract_target_transitions(
    ticket: dict,
    target_columns: list[str],
    current_year: int,
) -> list[tuple]:
    """
    Walk a ticket's changelog and return (month, key, summary) tuples for every
    transition into any of target_columns that happened this year.
    """
    story_key = ticket.get("key", "UNKNOWN")
    summary = ticket.get("fields", {}).get("summary", "No Summary")
    normalised_targets = {c.lower() for c in target_columns}
    results = []

    for entry in ticket.get("changelog", {}).get("histories", []):
        change_date = _parse_jira_timestamp(entry.get("created", ""))
        if not change_date or change_date.year != current_year:
            continue

        for change in entry.get("items", []):
            if change.get("field") != "status":
                continue
            if change.get("toString", "").lower() in normalised_targets:
                results.append((change_date.strftime("%B"), story_key, summary))

    return results


# ─────────────────────────────────────────
# Fetch helpers (one per Jira API pattern)
# ─────────────────────────────────────────

def _fetch_via_jql(
    jira_url: str,
    auth: HTTPBasicAuth,
    current_year: int,
    epic: Optional[str] = None,
    label: Optional[str] = None,
) -> list[dict]:
    """
    POST-based JQL search.
    - epic  → Tesco Mobile pattern (parentEpic / Epic Link, tries both)
    - label → Avis pattern (labels = "X") — returns all historical issues,
              unlike the agile board endpoint which only returns active issues
    """
    search_url = f"{jira_url}/rest/api/3/search/jql"

    if epic:
        # Run BOTH epic-link patterns and union by issue key.
        # Some tickets use the newer parentEpic field, others the older "Epic Link"
        # custom field — returning early on the first 200 misses tickets only in the second.
        jql_variants = [
            f"parentEpic = {epic} AND issuetype = Story AND updated >= {current_year}-01-01",
            f'"Epic Link" = {epic} AND issuetype = Story AND updated >= {current_year}-01-01',
        ]
        merged: dict[str, dict] = {}  # key → issue, deduplicates across both queries

        for jql in jql_variants:
            payload: dict = {
                "jql": jql,
                "maxResults": 1000,
                "fields": ["summary", "status"],
                "expand": "changelog",
            }
            while True:
                resp = requests.post(search_url, auth=auth, json=payload, timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                for issue in data.get("issues", []):
                    merged[issue["key"]] = issue  # last write wins but changelogs are identical
                next_token = data.get("nextPageToken")
                if not next_token or data.get("isLast"):
                    break
                payload["nextPageToken"] = next_token
                payload.pop("startAt", None)

        return list(merged.values())

    elif label:
        jql_variants = [
            f'labels = "{label}" AND issuetype = Story AND updated >= {current_year}-01-01',
        ]
        all_issues: list[dict] = []

        for jql in jql_variants:
            payload = {
                "jql": jql,
                "maxResults": 1000,
                "fields": ["summary", "status"],
                "expand": "changelog",
            }
            while True:
                resp = requests.post(search_url, auth=auth, json=payload, timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                all_issues.extend(data.get("issues", []))
                next_token = data.get("nextPageToken")
                if not next_token or data.get("isLast"):
                    break
                payload["nextPageToken"] = next_token
                payload.pop("startAt", None)

        return all_issues

    return []


def _fetch_via_agile_board(
    jira_url: str,
    auth: HTTPBasicAuth,
    board_id,
    label_filter: Optional[str] = None,
) -> list[dict]:
    """GET-based agile board endpoint — used for Avis."""
    search_url = f"{jira_url}/rest/agile/1.0/board/{board_id}/issue"
    all_issues: list[dict] = []
    start_at = 0
    max_results = 50

    while True:
        params = {
            "startAt": start_at,
            "maxResults": max_results,
            "expand": "changelog",
            "fields": "summary,status,issuetype",
        }
        if label_filter:
            params["jql"] = f'labels = "{label_filter}" AND issuetype = Story'
        resp = requests.get(search_url, auth=auth, params=params, timeout=30)
        if resp.status_code != 200:
            break

        data = resp.json()
        issues = data.get("issues", [])

        # Agile board returns all issue types — keep only Stories
        stories = [
            i for i in issues
            if i.get("fields", {}).get("issuetype", {}).get("name", "").lower() == "story"
        ]
        all_issues.extend(stories)

        if start_at + max_results >= data.get("total", 0):
            break
        start_at += max_results

    return all_issues


# ─────────────────────────────────────────
# Public cached function
# ─────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def get_jira_velocity(
    jira_url: str,
    email: str,
    api_token: str,
    board_id: Optional[str],
    target_columns: tuple[str, ...],    # tuple so it's hashable for st.cache_data
    target_per_month: int,
    mode: str,                          # "jql" | "agile"
    epic: Optional[str],
    label_filter: Optional[str] = None,
) -> dict:
    """
    Unified Jira velocity fetcher.

    mode="jql"   + epic         → Tesco Mobile (POST JQL, epic-scoped)
    mode="jql"   + label_filter → Avis (POST JQL, label-scoped — full history)
    mode="agile"                → agile board endpoint (active issues only, kept for reference)

    target_columns is a tuple of status names that count as a completed experiment.
    A ticket is counted at most once per month even if it transitions through
    multiple target columns (e.g. straight to Run, bypassing Ready to Publish).
    """
    auth = HTTPBasicAuth(email, api_token)
    current_year = datetime.datetime.now().year
    current_month = datetime.datetime.now().strftime("%B")
    current_month_idx = datetime.datetime.now().month

    if mode == "jql":
        tickets = _fetch_via_jql(
            jira_url, auth, current_year,
            epic=epic,
            label=label_filter,
        )
    else:
        tickets = _fetch_via_agile_board(jira_url, auth, board_id, label_filter=label_filter)

    # month → {key: summary} — dict ensures each ticket counted once per month
    # even if it hits multiple target columns.
    # seen_keys ensures each ticket is counted at most once globally across all months
    # (prevents a ticket cycling through target columns in different months being double-counted).
    moved_to_target: dict[str, dict[str, str]] = defaultdict(dict)
    seen_keys: set[str] = set()

    for ticket in tickets:
        for month, key, summary in _extract_target_transitions(
            ticket, list(target_columns), current_year
        ):
            if key not in seen_keys:
                moved_to_target[month].setdefault(key, summary)  # first transition wins
                seen_keys.add(key)

    # Build monthly summary
    monthly_data = []
    ytd_count = 0

    for i in range(current_month_idx):
        month = MONTHS_ORDER[i]
        count = len(moved_to_target.get(month, {}))
        monthly_data.append({"month": month[:3], "count": count, "target": target_per_month})
        ytd_count += count

    current_count = len(moved_to_target.get(current_month, {}))
    ytd_target = target_per_month * current_month_idx

    current_items = [
        f"{key} — {summary}"
        for key, summary in moved_to_target.get(current_month, {}).items()
    ]

    all_month_items = {
        month: [f"{key} — {summary}" for key, summary in items.items()]
        for month, items in moved_to_target.items()
        if items
    }

    return {
        "current_month_count": current_count,
        "target_per_month": target_per_month,
        "ytd_count": ytd_count,
        "ytd_target": ytd_target,
        "monthly_data": monthly_data,
        "current_month_items": current_items,
        "all_month_items": all_month_items,
        "status": _velocity_status(current_count, target_per_month),
    }


def _build_jira_status_timeline(ticket: dict) -> list[tuple]:
    """
    Return a sorted list of (datetime, status_name) tuples representing every
    time this ticket entered a new status.  The initial status at creation time
    is inferred from the first changelog entry's fromString.
    """
    histories = sorted(
        ticket.get("changelog", {}).get("histories", []),
        key=lambda h: (
            _parse_jira_timestamp(h.get("created", ""))
            or datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
        ),
    )

    # Infer initial status from the first status-field change
    initial_status: Optional[str] = None
    for entry in histories:
        for change in entry.get("items", []):
            if change.get("field") == "status":
                initial_status = change.get("fromString")
                break
        if initial_status:
            break

    if not initial_status:
        # No status transitions recorded — use current status
        initial_status = ticket.get("fields", {}).get("status", {}).get("name", "")

    tl: list[tuple] = []

    # Anchor the initial status at the issue's creation time
    created_dt = _parse_jira_timestamp(ticket.get("fields", {}).get("created", ""))
    if created_dt and initial_status:
        tl.append((created_dt, initial_status))

    # Walk the changelog for every subsequent status transition
    for entry in histories:
        change_dt = _parse_jira_timestamp(entry.get("created", ""))
        if not change_dt:
            continue
        for change in entry.get("items", []):
            if change.get("field") == "status":
                to_status = change.get("toString", "")
                if to_status:
                    tl.append((change_dt, to_status))

    return sorted(tl, key=lambda x: x[0])


@st.cache_data(ttl=1800, show_spinner=False)
def get_jira_transition_times(
    jira_url: str,
    email: str,
    api_token: str,
    transitions: tuple,          # tuple of (from_status, to_status) pairs
    start_date: datetime.date,
    end_date: datetime.date,
    epic: Optional[str] = None,
    label_filter: Optional[str] = None,
) -> list[dict]:
    """
    Calculate average and median days between defined Jira status transitions.
    Uses the issue changelog; the initial status is anchored to the creation date.
    Only tickets where the start status was entered within start_date..end_date are counted.
    Returns a list of dicts: {from_col, to_col, avg_days, median_days, count}.
    """
    auth = HTTPBasicAuth(email, api_token)
    current_year = start_date.year
    search_url = f"{jira_url}/rest/api/3/search/jql"

    def _fetch(jql: str) -> list[dict]:
        tickets: list[dict] = []
        payload: dict = {
            "jql": jql,
            "maxResults": 1000,
            "fields": ["summary", "status", "created"],
            "expand": "changelog",
        }
        while True:
            resp = requests.post(search_url, auth=auth, json=payload, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            tickets.extend(data.get("issues", []))
            next_token = data.get("nextPageToken")
            if not next_token or data.get("isLast"):
                break
            payload["nextPageToken"] = next_token
            payload.pop("startAt", None)
        return tickets

    # Build ticket pool
    if epic:
        merged: dict[str, dict] = {}
        for jql in [
            f"parentEpic = {epic} AND issuetype = Story AND updated >= {current_year}-01-01",
            f'"Epic Link" = {epic} AND issuetype = Story AND updated >= {current_year}-01-01',
        ]:
            for t in _fetch(jql):
                merged[t["key"]] = t
        tickets = list(merged.values())
    elif label_filter:
        tickets = _fetch(
            f'labels = "{label_filter}" AND issuetype = Story AND updated >= {current_year}-01-01'
        )
    else:
        tickets = []

    results = []
    for from_status, to_status in transitions:
        durations: list[float] = []
        from_lower = from_status.lower()
        to_lower = to_status.lower()

        for ticket in tickets:
            tl = _build_jira_status_timeline(ticket)

            # Use first time the ticket entered each status (case-insensitive)
            first_from = next((dt for dt, s in tl if s.lower() == from_lower), None)
            first_to = next((dt for dt, s in tl if s.lower() == to_lower), None)

            if (
                first_from is not None
                and first_to is not None
                and first_to > first_from
                and start_date <= first_from.date() <= end_date
            ):
                durations.append((first_to - first_from).total_seconds() / 86400)

        if durations:
            results.append({
                "from_col": from_status,
                "to_col": to_status,
                "avg_days": mean(durations),
                "median_days": _median(durations),
                "count": len(durations),
            })
        else:
            results.append({
                "from_col": from_status,
                "to_col": to_status,
                "avg_days": None,
                "median_days": None,
                "count": 0,
            })

    return results


@st.cache_data(ttl=1800, show_spinner=False)
def get_jira_win_rate(
    jira_url: str,
    email: str,
    api_token: str,
    win_statuses: tuple,
    concluded_statuses: tuple,
    epic: Optional[str] = None,
    label_filter: Optional[str] = None,
) -> dict:
    """
    Calculate monthly and overall win rate from Jira changelogs.
    For each ticket, the FIRST time it enters any concluded status determines
    whether it counts as a win or a loss.
    Win rate = tickets first reaching a win status ÷ tickets first reaching any concluded status.
    """
    auth = HTTPBasicAuth(email, api_token)
    current_year = datetime.datetime.now().year
    search_url = f"{jira_url}/rest/api/3/search/jql"

    concluded_set = {s.lower() for s in concluded_statuses}
    win_set = {s.lower() for s in win_statuses}

    def _fetch(jql: str) -> list[dict]:
        tickets: list[dict] = []
        payload: dict = {
            "jql": jql,
            "maxResults": 1000,
            "fields": ["summary", "status", "created"],
            "expand": "changelog",
        }
        while True:
            resp = requests.post(search_url, auth=auth, json=payload, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            tickets.extend(data.get("issues", []))
            next_token = data.get("nextPageToken")
            if not next_token or data.get("isLast"):
                break
            payload["nextPageToken"] = next_token
            payload.pop("startAt", None)
        return tickets

    # Build ticket pool
    if epic:
        merged: dict[str, dict] = {}
        for jql in [
            f"parentEpic = {epic} AND issuetype = Story AND updated >= {current_year}-01-01",
            f'"Epic Link" = {epic} AND issuetype = Story AND updated >= {current_year}-01-01',
        ]:
            for t in _fetch(jql):
                merged[t["key"]] = t
        tickets = list(merged.values())
    elif label_filter:
        tickets = _fetch(
            f'labels = "{label_filter}" AND issuetype = Story AND updated >= {current_year}-01-01'
        )
    else:
        tickets = []

    monthly_concluded: dict[str, list] = defaultdict(list)
    monthly_winners: dict[str, list] = defaultdict(list)

    for ticket in tickets:
        key = ticket.get("key", "")
        summary = ticket.get("fields", {}).get("summary", "")
        label = f"{key} — {summary}"

        tl = _build_jira_status_timeline(ticket)

        # Find the FIRST time this ticket entered any concluded status this year
        for dt, status in sorted(tl, key=lambda x: x[0]):
            if dt.year != current_year:
                continue
            if status.lower() in concluded_set:
                month = dt.strftime("%B")
                monthly_concluded[month].append(label)
                if status.lower() in win_set:
                    monthly_winners[month].append(label)
                break

    # Build month-by-month summary
    monthly_data = []
    for month in MONTHS_ORDER:
        c = monthly_concluded.get(month, [])
        w = monthly_winners.get(month, [])
        if not c and not w:
            continue
        monthly_data.append({
            "month": month[:3],
            "full_launch": len(c),
            "winners": len(w),
            "win_rate": (len(w) / len(c) * 100) if c else 0,
        })

    total_c = sum(len(v) for v in monthly_concluded.values())
    total_w = sum(len(v) for v in monthly_winners.values())

    return {
        "overall_win_rate": (total_w / total_c * 100) if total_c else 0,
        "total_full_launch": total_c,
        "total_winners": total_w,
        "monthly_data": monthly_data,
        "monthly_full_launch": dict(monthly_concluded),
        "monthly_winners": dict(monthly_winners),
        "concluded_label": "Concluded",
    }
