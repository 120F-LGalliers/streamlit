import datetime
import re
from collections import defaultdict
from statistics import mean, median as _median

import requests
import streamlit as st

from config import TRELLO_TARGET_COLUMNS, TRELLO_FULL_LAUNCH_COLUMN, TRELLO_WIN_COLUMNS

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

    all_month_items = {
        month: sorted(cards)
        for month, cards in moved_to_target.items()
        if cards
    }

    return {
        "current_month_count": current_count,
        "target_per_month": target_per_month,
        "ytd_count": ytd_count,
        "ytd_target": ytd_target,
        "monthly_data": monthly_data,
        "current_month_items": sorted(moved_to_target.get(current_month, set())),
        "all_month_items": all_month_items,
        "status": _velocity_status(current_count, target_per_month),
    }


@st.cache_data(ttl=1800, show_spinner=False)
def get_trello_transition_times(
    api_key: str,
    token: str,
    board_id: str,
    transitions: tuple,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict]:
    """
    Calculate average and median days cards spend moving between defined column pairs.
    Only cards that entered the start column within start_date..end_date are counted.
    Returns a list of dicts: {from_col, to_col, avg_days, median_days, count}.
    """
    since_iso = datetime.datetime(start_date.year, 1, 1).isoformat() + "Z"

    all_actions: list[dict] = []
    before = None

    while True:
        params = {
            "key": api_key,
            "token": token,
            "filter": "updateCard:idList",
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

    # Build per-card timelines: card_id → [(datetime, list_name), ...]
    card_timelines: dict[str, list] = defaultdict(list)

    all_actions.sort(key=lambda x: x.get("date", ""))

    for action in all_actions:
        data = action.get("data", {})
        card = data.get("card", {})
        list_after = data.get("listAfter", {})
        date_str = action.get("date")

        if not (card and list_after and date_str):
            continue

        cid = card.get("id")
        if not cid:
            continue

        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            continue

        card_timelines[cid].append((dt, list_after.get("name", "")))

    results = []
    for from_col, to_col in transitions:
        durations: list[float] = []

        for cid, moves in card_timelines.items():
            moves_sorted = sorted(moves, key=lambda x: x[0])

            # Use the first time each card entered these columns
            first_from = next((dt for dt, col in moves_sorted if col == from_col), None)
            first_to = next((dt for dt, col in moves_sorted if col == to_col), None)

            if (
                first_from is not None
                and first_to is not None
                and first_to > first_from
                and start_date <= first_from.date() <= end_date
            ):
                durations.append((first_to - first_from).total_seconds() / 86400)

        if durations:
            results.append({
                "from_col": from_col,
                "to_col": to_col,
                "avg_days": mean(durations),
                "median_days": _median(durations),
                "count": len(durations),
            })
        else:
            results.append({
                "from_col": from_col,
                "to_col": to_col,
                "avg_days": None,
                "median_days": None,
                "count": 0,
            })

    return results


@st.cache_data(ttl=1800, show_spinner=False)
def get_trello_win_rate(api_key: str, token: str, board_id: str) -> dict:
    """
    Calculate monthly and overall win rate for TSB.

    Win rate = cards reaching a WIN_COLUMN ÷ cards reaching Full Launch.

    Uses a single paginated board-level actions call rather than one call per
    card (the original script's approach), cutting API usage from O(n cards)
    to O(1-2 paginated requests).
    """
    current_year = datetime.datetime.utcnow().year
    since_iso = datetime.datetime(current_year, 1, 1).isoformat() + "Z"

    all_actions: list[dict] = []
    before = None

    while True:
        params = {
            "key": api_key,
            "token": token,
            "filter": "updateCard:idList",
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

    all_actions.sort(key=lambda x: x.get("date", ""))

    # Fetch all cards to build a label lookup — one extra call, labels aren't in action events
    cards_resp = requests.get(
        f"https://api.trello.com/1/boards/{board_id}/cards",
        params={"key": api_key, "token": token, "fields": "id,labels"},
        timeout=30,
    )
    cards_resp.raise_for_status()
    card_labels: dict[str, list[str]] = {
        c["id"]: [lbl["name"] for lbl in c.get("labels", []) if lbl.get("name")]
        for c in cards_resp.json()
    }

    _all_tracked = {TRELLO_FULL_LAUNCH_COLUMN} | set(TRELLO_WIN_COLUMNS)

    # Track first time each card enters each tracked column to avoid double-counting
    card_counted: dict[str, set] = defaultdict(set)
    monthly_full_launch: dict[str, list] = defaultdict(list)
    monthly_winners: dict[str, list] = defaultdict(list)
    # label → {concluded, winners} counts
    by_label_concluded: dict[str, int] = defaultdict(int)
    by_label_winners: dict[str, int] = defaultdict(int)

    for action in all_actions:
        data = action.get("data", {})
        card = data.get("card", {})
        list_after = data.get("listAfter", {})
        date_str = action.get("date")

        if not (card and list_after and date_str):
            continue

        list_name = list_after.get("name", "")
        if list_name not in _all_tracked:
            continue

        cid = card.get("id")
        if not cid or list_name in card_counted[cid]:
            continue  # already counted this card for this column

        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            continue

        if dt.year != current_year:
            continue

        month = dt.strftime("%B")
        card_name = _normalize_card_name(card.get("name", "Unknown"))
        labels = card_labels.get(cid, []) or ["No Label"]
        card_counted[cid].add(list_name)

        if list_name == TRELLO_FULL_LAUNCH_COLUMN:
            monthly_full_launch[month].append(card_name)
            for lbl in labels:
                by_label_concluded[lbl] += 1
        elif list_name in TRELLO_WIN_COLUMNS:
            monthly_winners[month].append(card_name)
            for lbl in labels:
                by_label_winners[lbl] += 1

    # Build month-by-month summary (only months with at least one entry)
    monthly_data = []
    for month in MONTHS_ORDER:
        fl_cards = monthly_full_launch.get(month, [])
        w_cards = monthly_winners.get(month, [])
        if not fl_cards and not w_cards:
            continue
        fl_count = len(fl_cards)
        w_count = len(w_cards)
        monthly_data.append({
            "month": month[:3],
            "full_launch": fl_count,
            "winners": w_count,
            "win_rate": (w_count / fl_count * 100) if fl_count else 0,
        })

    total_fl = sum(len(v) for v in monthly_full_launch.values())
    total_w = sum(len(v) for v in monthly_winners.values())

    # Build label breakdown sorted by concluded count descending
    all_labels = sorted(
        set(by_label_concluded) | set(by_label_winners),
        key=lambda l: -by_label_concluded.get(l, 0),
    )
    by_label = {
        lbl: {
            "concluded": by_label_concluded.get(lbl, 0),
            "winners": by_label_winners.get(lbl, 0),
            "win_rate": (
                by_label_winners.get(lbl, 0) / by_label_concluded[lbl] * 100
                if by_label_concluded.get(lbl, 0) else 0
            ),
        }
        for lbl in all_labels
    }

    return {
        "overall_win_rate": (total_w / total_fl * 100) if total_fl else 0,
        "total_full_launch": total_fl,
        "total_winners": total_w,
        "monthly_data": monthly_data,
        "monthly_full_launch": dict(monthly_full_launch),
        "monthly_winners": dict(monthly_winners),
        "concluded_label": "Full Launch",
        "by_label": by_label,
    }
