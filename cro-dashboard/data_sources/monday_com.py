import datetime
import json
from collections import defaultdict

import requests
import streamlit as st

from config import MONDAY_TARGET_STATUSES

API_URL = "https://api.monday.com/v2"

MONTHS_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _parse_monday_timestamp(created_at: str) -> datetime.datetime | None:
    """Handle both Unix timestamps and ISO strings returned by the Monday API."""
    try:
        if str(created_at).isdigit():
            ts = int(created_at)
            if len(str(created_at)) > 10:
                ts = ts / (10 ** (len(str(created_at)) - 10))
            return datetime.datetime.utcfromtimestamp(ts)
        return datetime.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def _batch_fetch_pulse_names(api_key: str, pulse_ids: list[str]) -> dict[str, str]:
    """Fetch item names in one request rather than one call per item."""
    if not pulse_ids:
        return {}

    ids_str = ", ".join(pulse_ids)
    query = f"""
    {{
      items(ids: [{ids_str}]) {{
        id
        name
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
        return {str(item["id"]): item["name"] for item in items}
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
    any of MONDAY_TARGET_STATUSES this year, and return structured velocity data.
    """
    current_year = datetime.datetime.now().year
    current_month = datetime.datetime.now().strftime("%B")
    current_month_idx = datetime.datetime.now().month

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

    # First pass: collect relevant pulse IDs for batch name lookup
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

    # Batch fetch item names
    pulse_names = _batch_fetch_pulse_names(api_key, list(relevant_ids))

    # Aggregate: deduplicate by pulse_id within each month
    moved_to_target: dict[str, set] = defaultdict(set)
    for entry in parsed:
        moved_to_target[entry["month"]].add(entry["pulse_id"])

    # Build monthly summary
    monthly_data = []
    ytd_count = 0

    for i in range(current_month_idx):
        month = MONTHS_ORDER[i]
        count = len(moved_to_target.get(month, set()))
        monthly_data.append({"month": month[:3], "count": count, "target": target_per_month})
        ytd_count += count

    current_count = len(moved_to_target.get(current_month, set()))
    ytd_target = target_per_month * current_month_idx

    current_items = sorted(
        pulse_names.get(pid, f"Item {pid}")
        for pid in moved_to_target.get(current_month, set())
    )

    return {
        "current_month_count": current_count,
        "target_per_month": target_per_month,
        "ytd_count": ytd_count,
        "ytd_target": ytd_target,
        "monthly_data": monthly_data,
        "current_month_items": current_items,
        "all_month_items": {
            MONTHS_ORDER[i]: sorted(
                pulse_names.get(pid, f"Item {pid}")
                for pid in moved_to_target.get(MONTHS_ORDER[i], set())
            )
            for i in range(current_month_idx)
            if moved_to_target.get(MONTHS_ORDER[i])
        },
        "status": _velocity_status(current_count, target_per_month),
    }
