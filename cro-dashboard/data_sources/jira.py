import datetime
from collections import defaultdict
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


def _extract_target_transitions(ticket: dict, target_column: str, current_year: int) -> list[tuple]:
    """
    Walk a ticket's changelog and return (month, key, summary) tuples for every
    transition into target_column that happened this year.
    """
    story_key = ticket.get("key", "UNKNOWN")
    summary = ticket.get("fields", {}).get("summary", "No Summary")
    results = []

    for entry in ticket.get("changelog", {}).get("histories", []):
        change_date = _parse_jira_timestamp(entry.get("created", ""))
        if not change_date or change_date.year != current_year:
            continue

        for change in entry.get("items", []):
            if change.get("field") != "status":
                continue
            if change.get("toString", "").lower() == target_column.lower():
                results.append((change_date.strftime("%B"), story_key, summary))

    return results


# ─────────────────────────────────────────
# Fetch helpers (one per Jira API pattern)
# ─────────────────────────────────────────

def _fetch_via_jql(jira_url: str, auth: HTTPBasicAuth, epic: str, current_year: int) -> list[dict]:
    """POST-based JQL search — used for Tesco Mobile (epic-scoped)."""
    search_url = f"{jira_url}/rest/api/3/search/jql"

    jql_variants = [
        f"parentEpic = {epic} AND issuetype = Story AND updated >= {current_year}-01-01",
        f'"Epic Link" = {epic} AND issuetype = Story AND updated >= {current_year}-01-01',
    ]

    for jql in jql_variants:
        payload: dict = {
            "jql": jql,
            "maxResults": 1000,
            "fields": ["summary", "status"],
            "expand": "changelog",
        }
        all_issues: list[dict] = []

        while True:
            resp = requests.post(search_url, auth=auth, json=payload, timeout=30)
            if resp.status_code != 200:
                break

            data = resp.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)

            next_token = data.get("nextPageToken")
            if not next_token or data.get("isLast"):
                break
            payload["nextPageToken"] = next_token
            payload.pop("startAt", None)

        # Accept the result if we got a 200 (even zero results)
        if resp.status_code == 200:
            return all_issues

    return []


def _fetch_via_agile_board(jira_url: str, auth: HTTPBasicAuth, board_id, label_filter: Optional[str] = None) -> list[dict]:
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
            "fields": "summary,status,issuetype,labels",
        }
        if label_filter:
            params["jql"] = f'labels = "{label_filter}"'

        resp = requests.get(search_url, auth=auth, params=params, timeout=30)
        if resp.status_code != 200:
            break

        data = resp.json()
        issues = data.get("issues", [])

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
    target_column: str,
    target_per_month: int,
    mode: str,          # "jql" | "agile"
    epic: Optional[str],
    label_filter: Optional[str] = None,
) -> dict:
    """
    Unified Jira velocity fetcher.

    mode="jql"    → Tesco Mobile pattern (POST JQL, epic-scoped)
    mode="agile"  → Avis pattern (GET agile board endpoint)
    """
    auth = HTTPBasicAuth(email, api_token)
    current_year = datetime.datetime.now().year
    current_month = datetime.datetime.now().strftime("%B")
    current_month_idx = datetime.datetime.now().month

    tickets = (
        _fetch_via_jql(jira_url, auth, epic, current_year)
        if mode == "jql"
        else _fetch_via_agile_board(jira_url, auth, board_id, label_filter)
    )

    # month → list of (key, summary)
    moved_to_target: dict[str, list] = defaultdict(list)

    for ticket in tickets:
        for month, key, summary in _extract_target_transitions(ticket, target_column, current_year):
            moved_to_target[month].append((key, summary))

    # Build monthly summary
    monthly_data = []
    ytd_count = 0

    for i in range(current_month_idx):
        month = MONTHS_ORDER[i]
        count = len(moved_to_target.get(month, []))
        monthly_data.append({"month": month[:3], "count": count, "target": target_per_month})
        ytd_count += count

    current_count = len(moved_to_target.get(current_month, []))
    ytd_target = target_per_month * current_month_idx

    current_items = [
        f"{key} — {summary}"
        for key, summary in moved_to_target.get(current_month, [])
    ]

    return {
        "current_month_count": current_count,
        "target_per_month": target_per_month,
        "ytd_count": ytd_count,
        "ytd_target": ytd_target,
        "monthly_data": monthly_data,
        "current_month_items": current_items,
        "all_month_items": {
            MONTHS_ORDER[i]: [
                f"{key} — {summary}"
                for key, summary in moved_to_target.get(MONTHS_ORDER[i], [])
            ]
            for i in range(current_month_idx)
            if moved_to_target.get(MONTHS_ORDER[i])
        },
        "status": _velocity_status(current_count, target_per_month),
    }
