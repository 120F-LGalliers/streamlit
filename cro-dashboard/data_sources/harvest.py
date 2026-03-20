import datetime
import requests
import streamlit as st

from config import TASK_GROUPS, TEAM_MEMBERS, PROJECT_BUDGETS

BASE_URL = "https://api.harvestapp.com/v2"


def _calculate_month_progress() -> float:
    """Return how far through the current month we are, based on working days."""
    today = datetime.date.today()
    first_day = today.replace(day=1)

    if today.month == 12:
        first_day_next = today.replace(year=today.year + 1, month=1, day=1)
    else:
        first_day_next = today.replace(month=today.month + 1, day=1)

    total_days = (first_day_next - first_day).days
    weekdays = {0, 1, 2, 3, 4}

    weekdays_in_month = sum(
        1 for d in range(total_days)
        if (first_day + datetime.timedelta(days=d)).weekday() in weekdays
    )
    weekdays_passed = sum(
        1 for d in range(today.day)
        if (first_day + datetime.timedelta(days=d)).weekday() in weekdays
    )

    return (weekdays_passed / weekdays_in_month * 100) if weekdays_in_month > 0 else 0.0


def _get_burn_status(utilization_pct: float, month_progress: float) -> str:
    if utilization_pct < month_progress - 5:
        return "underburning"
    elif utilization_pct > month_progress + 5:
        return "overburning"
    return "on_track"


@st.cache_data(ttl=1800, show_spinner=False)
def get_harvest_data(project_id: int, account_id: str, access_token: str) -> dict:
    """
    Fetch and process all time entries for the current month for a given project.
    Returns structured data ready for the dashboard to render.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Harvest-Account-Id": account_id,
        "User-Agent": "CRO-Dashboard/1.0",
    }

    today = datetime.date.today()
    first_day = today.replace(day=1)

    params = {
        "project_id": project_id,
        "from": first_day.strftime("%Y-%m-%d"),
        "to": today.strftime("%Y-%m-%d"),
        "per_page": 100,
    }

    all_entries = []
    page = 1
    while True:
        params["page"] = page
        resp = requests.get(f"{BASE_URL}/time_entries", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("time_entries", [])
        if not entries:
            break
        all_entries.extend(entries)
        if len(entries) < 100:
            break
        page += 1

    # Aggregate hours
    billable: dict[str, float] = {}
    non_billable: dict[str, float] = {}
    total_hours = 0.0
    total_billable = 0.0

    for entry in all_entries:
        user_name = entry["user"]["name"]
        task_id = entry["task"]["id"]
        hours = entry["hours"]
        total_hours += hours

        task_group = TASK_GROUPS.get(task_id, "Other")
        if task_group == "Other" and user_name in TEAM_MEMBERS:
            task_group = TEAM_MEMBERS[user_name]

        if entry["billable"]:
            total_billable += hours
            billable[task_group] = billable.get(task_group, 0.0) + hours
        else:
            non_billable[task_group] = non_billable.get(task_group, 0.0) + hours

    month_progress = _calculate_month_progress()
    budget = PROJECT_BUDGETS.get(project_id, {})

    task_groups_data = []
    for group in sorted(billable):
        hours = billable[group]
        budgeted = budget.get(group, 0.0)
        utilization = (hours / budgeted * 100) if budgeted > 0 else 0.0
        remaining = budgeted - hours

        task_groups_data.append({
            "group": group,
            "hours": hours,
            "budgeted": budgeted,
            "utilization": utilization,
            "remaining": remaining,
            "status": _get_burn_status(utilization, month_progress),
        })

    return {
        "task_groups": task_groups_data,
        "total_hours": total_hours,
        "total_billable": total_billable,
        "non_billable": non_billable,
        "month_progress": month_progress,
        "billable_pct": (total_billable / total_hours * 100) if total_hours > 0 else 0.0,
    }
