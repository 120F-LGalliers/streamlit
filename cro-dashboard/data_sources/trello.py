import datetime
import re
from collections import defaultdict

import requests
import streamlit as st

from config import TRELLO_TARGET_COLUMNS

MONTHS_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _normalize_card_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = name.replace("TSB 120", "TSB120")
    return name


def _velocity_status(count: int, target: int) -> str:
    if count > target:
        return "exceeding"
    elif count == target:
        return "on_track"
    return "behind"


@st.cache_data(ttl=1800, show_spinner=False)
def get_trello_velocity(api_key: str, token: str, board_id: str, target_per_month: int) -> dict:
    """
    Fetch all board actions for the current year and identify cards that
    moved into any of TRELLO_TARGET_COLUMNS.  Returns structured velocity data.
    """
    current_year = datetime.datetime.utcnow().year
    current_month = datetime.datetime.utcnow().strftime("%B")
    current_month_idx = datetime.datetime.utcnow().month

    since_iso = datetime.datetime(current_year, 1, 1).isoformat() + "Z"

    all_actions: list[dict] = []
    before = None

    while True:
        params = {
            "key": api_key,
            "token": token,
            "filter": "createCard,updateCard:idList",
            "since": since_iso,
            "limit": 1000,
        }
        if before:
            params["before"] = before

        resp = requests.get(
            f"https://api.trello.com/1/boards/{board_id}/actions",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        actions = resp.json()
        if not actions:
            break
        all_actions.extend(actions)
        before = actions[-1]["date"]

    # Sort chronologically so deduplication is deterministic
    all_actions.sort(key=lambda x: x.get("date", ""))

    # moved_to_target: month → set of unique card names
    moved_to_target: dict[str, set] = defaultdict(set)

    for action in all_actions:
        data = action.get("data", {})
        list_after = data.get("listAfter", {})
        card = data.get("card", {})
        action_date = action.get("date")

        if not (card and action_date and list_after):
            continue

        list_name = list_after.get("name", "")
        if list_name not in TRELLO_TARGET_COLUMNS:
            continue

        try:
            action_dt = datetime.datetime.strptime(action_date, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            continue

        if action_dt.year != current_year:
            continue

        month = action_dt.strftime("%B")
        card_name = _normalize_card_name(card.get("name", "Unknown"))
        moved_to_target[month].add(card_name)

    # Build month-by-month summary up to (and including) the current month
    monthly_data = []
    ytd_count = 0

    for i in range(current_month_idx):
        month = MONTHS_ORDER[i]
        count = len(moved_to_target.get(month, set()))
        monthly_data.append({"month": month[:3], "count": count, "target": target_per_month})
        ytd_count += count

    current_count = len(moved_to_target.get(current_month, set()))
    ytd_target = target_per_month * current_month_idx

    return {
        "current_month_count": current_count,
        "target_per_month": target_per_month,
        "ytd_count": ytd_count,
        "ytd_target": ytd_target,
        "monthly_data": monthly_data,
        "current_month_items": sorted(moved_to_target.get(current_month, set())),
        "all_month_items": {
            MONTHS_ORDER[i]: sorted(moved_to_target.get(MONTHS_ORDER[i], set()))
            for i in range(current_month_idx)
            if moved_to_target.get(MONTHS_ORDER[i])
        },
        "status": _velocity_status(current_count, target_per_month),
    }
