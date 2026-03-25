import datetime
import requests
import streamlit as st

from config import TASK_GROUPS, TEAM_MEMBERS, PROJECT_BUDGETS

BASE_URL = "https://api.harvestapp.com/v2"


def _get_working_day_stats() -> dict:
    """Return working day counts and month progress percentage."""
    today = datetime.date.today()
    first_day = today.replace(day=1)

    if today.month == 12:
        first_day_next = today.replace(year=today.year + 1, month=1, day=1)
    else:
        first_day_next = today.replace(month=today.month + 1, day=1)

    total_days = (first_day_next - first_day).days
    weekdays = {0, 1, 2, 3, 4}

    total_working = sum(
        1 for d in range(total_days)
        if (first_day + datetime.timedelta(days=d)).weekday() in weekdays
    )
    elapsed = sum(
        1 for d in range(today.day)
        if (first_day + datetime.timedelta(days=d)).weekday() in weekdays
    )
    remaining = total_working - elapsed

    return {
        "total": total_working,
        "elapsed": elapsed,
        "remaining": remaining,
        "progress_pct": (elapsed / total_working * 100) if total_working > 0 else 0.0,
    }


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
    billable_tasks: dict[str, dict[str, float]] = {}  # group -> {task_name: hours}
    non_billable: dict[str, float] = {}
    total_hours = 0.0
    total_billable = 0.0

    for entry in all_entries:
        user_name = entry["user"]["name"]
        task_id = entry["task"]["id"]
        task_name = entry["task"]["name"]
        hours = entry["hours"]
        total_hours += hours

        task_group = TASK_GROUPS.get(task_id, "Other")
        if task_group == "Other" and user_name in TEAM_MEMBERS:
            task_group = TEAM_MEMBERS[user_name]

        if entry["billable"]:
            total_billable += hours
            billable[task_group] = billable.get(task_group, 0.0) + hours
            if task_group not in billable_tasks:
                billable_tasks[task_group] = {}
            billable_tasks[task_group][task_name] = (
                billable_tasks[task_group].get(task_name, 0.0) + hours
            )
        else:
            non_billable[task_group] = non_billable.get(task_group, 0.0) + hours

    wd = _get_working_day_stats()
    month_progress = wd["progress_pct"]
    budget = PROJECT_BUDGETS.get(project_id, {})

    task_groups_data = []
    for group in sorted(billable):
        hours = billable[group]
        budgeted = budget.get(group, 0.0)
        utilization = (hours / budgeted * 100) if budgeted > 0 else 0.0
        remaining = budgeted - hours

        # Projection: extrapolate current daily burn rate to end of month
        daily_rate = (hours / wd["elapsed"]) if wd["elapsed"] > 0 else 0.0
        projected = round(daily_rate * wd["total"], 1)
        projected_delta = round(projected - budgeted, 1)

        # Required pace: how many hours/day needed to finish exactly on budget
        required_daily_rate = (max(remaining, 0) / wd["remaining"]) if wd["remaining"] > 0 else 0.0

        # Sort tasks within this group by hours descending
        tasks = dict(
            sorted(billable_tasks.get(group, {}).items(), key=lambda x: x[1], reverse=True)
        )

        task_groups_data.append({
            "group": group,
            "hours": hours,
            "budgeted": budgeted,
            "utilization": utilization,
            "remaining": remaining,
            "status": _get_burn_status(utilization, month_progress),
            "tasks": tasks,
            "daily_rate": round(daily_rate, 2),
            "projected": projected,
            "projected_delta": projected_delta,
            "required_daily_rate": round(required_daily_rate, 2),
        })

    return {
        "task_groups": task_groups_data,
        "total_hours": total_hours,
        "total_billable": total_billable,
        "non_billable": non_billable,
        "month_progress": month_progress,
        "billable_pct": (total_billable / total_hours * 100) if total_hours > 0 else 0.0,
    }


def get_combined_harvest_data(project_ids: tuple, account_id: str, access_token: str) -> dict:
    """
    Fetch multiple Harvest projects and merge into a single result.
    Hours and budgets are summed across all projects; projections are
    recalculated on the combined totals.
    For a single project ID this is equivalent to get_harvest_data.
    """
    if len(project_ids) == 1:
        return get_harvest_data(project_ids[0], account_id, access_token)

    results = [get_harvest_data(pid, account_id, access_token) for pid in project_ids]

    # Merge task groups: sum hours, budgets, task-level breakdowns, and per-project split
    merged: dict[str, dict] = {}
    for pid, result in zip(project_ids, results):
        for tg in result["task_groups"]:
            g = tg["group"]
            if g not in merged:
                merged[g] = {"group": g, "hours": 0.0, "budgeted": 0.0, "tasks": {}, "per_project": {}}
            merged[g]["hours"] += tg["hours"]
            merged[g]["budgeted"] += tg["budgeted"]
            merged[g]["per_project"][pid] = tg["hours"]
            for task_name, task_hours in tg["tasks"].items():
                merged[g]["tasks"][task_name] = merged[g]["tasks"].get(task_name, 0.0) + task_hours

    # Recalculate all derived fields on combined totals
    wd = _get_working_day_stats()
    month_progress = wd["progress_pct"]

    task_groups_data = []
    for group, data in sorted(merged.items()):
        hours = data["hours"]
        budgeted = data["budgeted"]
        utilization = (hours / budgeted * 100) if budgeted > 0 else 0.0
        remaining = budgeted - hours
        daily_rate = (hours / wd["elapsed"]) if wd["elapsed"] > 0 else 0.0
        projected = round(daily_rate * wd["total"], 1)
        projected_delta = round(projected - budgeted, 1)
        required_daily_rate = (max(remaining, 0) / wd["remaining"]) if wd["remaining"] > 0 else 0.0
        tasks = dict(sorted(data["tasks"].items(), key=lambda x: x[1], reverse=True))

        task_groups_data.append({
            "group": group,
            "hours": hours,
            "budgeted": budgeted,
            "utilization": utilization,
            "remaining": remaining,
            "status": _get_burn_status(utilization, month_progress),
            "tasks": tasks,
            "daily_rate": round(daily_rate, 2),
            "projected": projected,
            "projected_delta": projected_delta,
            "required_daily_rate": round(required_daily_rate, 2),
            "per_project": data.get("per_project", {}),
        })

    total_hours = sum(r["total_hours"] for r in results)
    total_billable = sum(r["total_billable"] for r in results)

    combined_non_billable: dict[str, float] = {}
    for result in results:
        for group, hrs in result["non_billable"].items():
            combined_non_billable[group] = combined_non_billable.get(group, 0.0) + hrs

    return {
        "task_groups": task_groups_data,
        "total_hours": total_hours,
        "total_billable": total_billable,
        "non_billable": combined_non_billable,
        "month_progress": month_progress,
        "billable_pct": (total_billable / total_hours * 100) if total_hours > 0 else 0.0,
    }
