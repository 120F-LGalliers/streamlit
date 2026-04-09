import datetime
import json
from collections import defaultdict
from statistics import mean, median as _median
from typing import Optional

import requests
import streamlit as st

from config import (
    MONDAY_TARGET_STATUSES,
    MONDAY_ACTIVITY_TYPE_COLUMN_TITLE,
    MONDAY_AB_TYPE_LABEL,
)

API_URL = "https://api.monday.com/v2"

MONTHS_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _parse_monday_timestamp(created_at: str) -> Optional[datetime.datetime]:
    try:
        if str(created_at).isdigit():
            ts = int(created_at)
            if len(str(created_at)) > 10:
                ts = ts / (10 ** (len(str(created_at)) - 10))
            return datetime.datetime.utcfromtimestamp(ts)
        return datetime.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def _get_activity_type_column_id(api_key: str, board_id: str, column_title: str) -> Optional[str]:
    """Find the Monday column ID whose title matches column_title."""
    query = f"""
    {{
      boards(ids: [{board_id}]) {{
        columns {{
          id
          title
        }}
      }}
    }}
    """
    try:
        resp = requests.post(
            API_URL,
            json={"query": query},
            headers={"Authorization": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        boards = resp.json().get("data", {}).get("boards", [])
        if boards:
            for col in boards[0].get("columns", []):
                if col.get("title", "").lower() == column_title.lower():
                    return col["id"]
    except Exception:
        pass
    return None


def _batch_fetch_item_data(
    api_key: str,
    pulse_ids: list[str],
    activity_col_id: Optional[str],
) -> dict[str, dict]:
    """Fetch item names and Activity Type values in one GraphQL request."""
    if not pulse_ids:
        return {}

    ids_str = ", ".join(pulse_ids)
    # Filter to just the Activity Type column if we know its ID
    col_filter = f'(ids: ["{activity_col_id}"])' if activity_col_id else ""

    query = f"""
    {{
      items(ids: [{ids_str}]) {{
        id
        name
        column_values{col_filter} {{
          id
          text
        }}
      }}
    }}
    """
    try:
        resp = requests.post(
            API_URL,
            json={"query": query},
            headers={"Authorization": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("items", [])
        result = {}
        for item in items:
            activity_type = "Unknown"
            for cv in item.get("column_values", []):
                text = cv.get("text", "").strip()
                if text:
                    activity_type = text
                    break
            result[str(item["id"])] = {
                "name": item["name"],
                "activity_type": activity_type,
            }
        return result
    except Exception:
        return {}


def _velocity_status(count: int, target: int) -> str:
    if count > target:
        return "exceeding"
    elif count == target:
        return "on_track"
    return "behind"


@st.cache_data(ttl=1800, show_spinner=False)
def get_monday_velocity(api_key: str, board_id: str, target_per_month: int) -> dict:
    """
    Page through all activity logs for the board, find items that moved into
    any of MONDAY_TARGET_STATUSES this year, and return structured velocity data
    including a per-type breakdown keyed by MONDAY_ACTIVITY_TYPE_COLUMN_TITLE.

    The velocity target and main counts apply to A/B tests only
    (MONDAY_AB_TYPE_LABEL). Other work types are captured in
    `activity_type_breakdown` for display purposes.
    """
    current_year = datetime.datetime.now().year
    current_month = datetime.datetime.now().strftime("%B")
    current_month_idx = datetime.datetime.now().month

    # Auto-discover the Activity Type column ID from the board schema
    activity_col_id = _get_activity_type_column_id(
        api_key, board_id, MONDAY_ACTIVITY_TYPE_COLUMN_TITLE
    )

    # Page through activity logs
    activity_logs: list[dict] = []
    page = 1

    while True:
        query = f"""
        {{
          boards(ids: [{board_id}]) {{
            activity_logs(limit: 100, page: {page}) {{
              id
              created_at
              data
            }}
          }}
        }}
        """
        resp = requests.post(
            API_URL,
            json={"query": query},
            headers={"Authorization": api_key},
            timeout=30,
        )
        resp.raise_for_status()

        boards = resp.json().get("data", {}).get("boards", [])
        if not boards:
            break

        logs = boards[0].get("activity_logs", [])
        if not logs:
            break

        activity_logs.extend(logs)
        page += 1

    # First pass: collect pulse IDs for relevant status transitions
    parsed: list[dict] = []
    relevant_ids: set[str] = set()

    for log in activity_logs:
        raw_data = log.get("data")
        created_at = log.get("created_at")

        if not (raw_data and created_at):
            continue

        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        except json.JSONDecodeError:
            continue

        if data.get("column_id") != "status":
            continue

        status = data.get("value", {}).get("label", {}).get("text", "")
        if status not in MONDAY_TARGET_STATUSES:
            continue

        dt = _parse_monday_timestamp(created_at)
        if not dt or dt.year != current_year:
            continue

        pulse_id = str(data.get("pulse_id", ""))
        relevant_ids.add(pulse_id)
        parsed.append({"pulse_id": pulse_id, "month": dt.strftime("%B")})

    # Batch fetch names + Activity Type for all relevant items
    item_data = _batch_fetch_item_data(api_key, list(relevant_ids), activity_col_id)

    # Aggregate: month → {pulse_id: activity_type}  (deduplicates per item per month)
    moved_to_target: dict[str, dict[str, str]] = defaultdict(dict)
    for entry in parsed:
        pid = entry["pulse_id"]
        atype = item_data.get(pid, {}).get("activity_type", "Unknown")
        moved_to_target[entry["month"]][pid] = atype

    # Build monthly summary (counts are A/B-only; breakdown covers all types)
    monthly_data = []
    ytd_count = 0
    activity_type_breakdown: dict[str, dict[str, int]] = {}

    for i in range(current_month_idx):
        month = MONTHS_ORDER[i]
        month_items = moved_to_target.get(month, {})

        type_counts: dict[str, int] = defaultdict(int)
        for atype in month_items.values():
            type_counts[atype] += 1

        ab_count = type_counts.get(MONDAY_AB_TYPE_LABEL, 0)
        monthly_data.append({"month": month[:3], "count": ab_count, "target": target_per_month})
        ytd_count += ab_count

        if type_counts:
            activity_type_breakdown[month] = dict(type_counts)

    # Current month
    current_items_map = moved_to_target.get(current_month, {})
    current_ab_count = sum(
        1 for atype in current_items_map.values() if atype == MONDAY_AB_TYPE_LABEL
    )
    ytd_target = target_per_month * current_month_idx

    current_items = sorted(
        item_data.get(pid, {}).get("name", f"Item {pid}")
        for pid, atype in current_items_map.items()
        if atype == MONDAY_AB_TYPE_LABEL
    )

    all_month_items = {
        month: sorted(
            item_data.get(pid, {}).get("name", f"Item {pid}")
            for pid, atype in pids_map.items()
            if atype == MONDAY_AB_TYPE_LABEL
        )
        for month, pids_map in moved_to_target.items()
        if any(atype == MONDAY_AB_TYPE_LABEL for atype in pids_map.values())
    }

    # Other work: grouped by activity type for current month and full year
    _other_this_month: dict[str, list[str]] = defaultdict(list)
    for pid, atype in current_items_map.items():
        if atype != MONDAY_AB_TYPE_LABEL:
            _other_this_month[atype].append(item_data.get(pid, {}).get("name", f"Item {pid}"))
    current_month_other_items = {k: sorted(v) for k, v in sorted(_other_this_month.items())}

    all_month_other_items: dict[str, dict[str, list[str]]] = {}
    for month, pids_map in moved_to_target.items():
        _other: dict[str, list[str]] = defaultdict(list)
        for pid, atype in pids_map.items():
            if atype != MONDAY_AB_TYPE_LABEL:
                _other[atype].append(item_data.get(pid, {}).get("name", f"Item {pid}"))
        if _other:
            all_month_other_items[month] = {k: sorted(v) for k, v in sorted(_other.items())}

    return {
        "current_month_count": current_ab_count,
        "target_per_month": target_per_month,
        "ytd_count": ytd_count,
        "ytd_target": ytd_target,
        "monthly_data": monthly_data,
        "current_month_items": current_items,
        "all_month_items": all_month_items,
        "current_month_other_items": current_month_other_items,
        "all_month_other_items": all_month_other_items,
        "activity_type_breakdown": activity_type_breakdown,
        "status": _velocity_status(current_ab_count, target_per_month),
    }


@st.cache_data(ttl=1800, show_spinner=False)
def get_monday_transition_times(
    api_key: str,
    board_id: str,
    transitions: tuple,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[dict]:
    """
    Calculate average and median days items spend moving between defined status pairs.
    Only items that entered the start status within start_date..end_date are counted.
    Returns a list of dicts: {from_col, to_col, avg_days, median_days, count}.
    """
    current_year = start_date.year

    # Page through all activity logs for the board
    activity_logs: list[dict] = []
    page = 1

    while True:
        query = f"""
        {{
          boards(ids: [{board_id}]) {{
            activity_logs(limit: 100, page: {page}) {{
              id
              created_at
              data
            }}
          }}
        }}
        """
        resp = requests.post(
            API_URL,
            json={"query": query},
            headers={"Authorization": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        boards = resp.json().get("data", {}).get("boards", [])
        if not boards:
            break
        logs = boards[0].get("activity_logs", [])
        if not logs:
            break
        activity_logs.extend(logs)
        page += 1

    # Build per-item status timelines: pulse_id → [(datetime, status), ...]
    card_timelines: dict[str, list] = defaultdict(list)

    for log in activity_logs:
        raw_data = log.get("data")
        created_at = log.get("created_at")
        if not (raw_data and created_at):
            continue

        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        except json.JSONDecodeError:
            continue

        if data.get("column_id") != "status":
            continue

        status = data.get("value", {}).get("label", {}).get("text", "")
        if not status:
            continue

        dt = _parse_monday_timestamp(created_at)
        if not dt or dt.year != current_year:
            continue

        pulse_id = str(data.get("pulse_id", ""))
        if pulse_id:
            card_timelines[pulse_id].append((dt, status))

    results = []
    for from_status, to_status in transitions:
        durations: list[float] = []

        for pulse_id, moves in card_timelines.items():
            moves_sorted = sorted(moves, key=lambda x: x[0])

            # Use the first time each item entered these statuses
            first_from = next((dt for dt, s in moves_sorted if s == from_status), None)
            first_to = next((dt for dt, s in moves_sorted if s == to_status), None)

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
def get_monday_win_rate(
    api_key: str,
    board_id: str,
    win_columns: tuple,
    concluded_columns: tuple,
) -> dict:
    """
    Calculate monthly and overall win rate for a Monday board.
    For each item, the FIRST time it enters any concluded column determines
    whether it counts as a win or a loss.
    Win rate = items first reaching a win column ÷ items first reaching any concluded column.
    """
    current_year = datetime.datetime.now().year

    concluded_set = {s.lower() for s in concluded_columns}
    win_set = {s.lower() for s in win_columns}

    # Page through activity logs (same pattern as velocity / transition times)
    activity_logs: list[dict] = []
    page = 1
    while True:
        query = f"""
        {{
          boards(ids: [{board_id}]) {{
            activity_logs(limit: 100, page: {page}) {{
              id
              created_at
              data
            }}
          }}
        }}
        """
        resp = requests.post(
            API_URL,
            json={"query": query},
            headers={"Authorization": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        boards = resp.json().get("data", {}).get("boards", [])
        if not boards:
            break
        logs = boards[0].get("activity_logs", [])
        if not logs:
            break
        activity_logs.extend(logs)
        page += 1

    # Build per-item status timeline: pulse_id → [(datetime, status), ...]
    item_timelines: dict[str, list] = defaultdict(list)
    for log in activity_logs:
        raw_data = log.get("data")
        created_at = log.get("created_at")
        if not (raw_data and created_at):
            continue
        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        except json.JSONDecodeError:
            continue
        if data.get("column_id") != "status":
            continue
        status = data.get("value", {}).get("label", {}).get("text", "")
        if not status:
            continue
        dt = _parse_monday_timestamp(created_at)
        if not dt or dt.year != current_year:
            continue
        pulse_id = str(data.get("pulse_id", ""))
        if pulse_id:
            item_timelines[pulse_id].append((dt, status))

    # Find each item's FIRST concluded status
    item_outcomes: dict[str, tuple] = {}  # pulse_id → (month, is_win)
    for pulse_id, moves in item_timelines.items():
        for dt, status in sorted(moves, key=lambda x: x[0]):
            if status.lower() in concluded_set:
                item_outcomes[pulse_id] = (dt.strftime("%B"), status.lower() in win_set)
                break

    # Batch-fetch item names
    relevant_ids = list(item_outcomes.keys())
    item_data = _batch_fetch_item_data(api_key, relevant_ids, None) if relevant_ids else {}

    monthly_concluded: dict[str, list] = defaultdict(list)
    monthly_winners: dict[str, list] = defaultdict(list)
    for pulse_id, (month, is_win) in item_outcomes.items():
        name = item_data.get(pulse_id, {}).get("name", f"Item {pulse_id}")
        monthly_concluded[month].append(name)
        if is_win:
            monthly_winners[month].append(name)

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
