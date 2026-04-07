import datetime
import re
import requests
import streamlit as st

from config import TASK_GROUPS, TEAM_MEMBERS, PROJECT_BUDGETS

BASE_URL = "https://api.harvestapp.com/v2"


def _get_working_day_stats(year: int = None, month: int = None) -> dict:
    """Return working day counts and month progress percentage for the given month."""
    today = datetime.date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    first_day = datetime.date(year, month, 1)
    if month == 12:
        first_day_next = datetime.date(year + 1, 1, 1)
    else:
        first_day_next = datetime.date(year, month + 1, 1)

    total_days = (first_day_next - first_day).days
    weekdays = {0, 1, 2, 3, 4}

    total_working = sum(
        1 for d in range(total_days)
        if (first_day + datetime.timedelta(days=d)).weekday() in weekdays
    )

    is_past = (year, month) < (today.year, today.month)
    is_current = (year == today.year and month == today.month)

    if is_past:
        elapsed = total_working
        remaining = 0
        progress_pct = 100.0
    elif is_current:
        elapsed = sum(
            1 for d in range(today.day)
            if (first_day + datetime.timedelta(days=d)).weekday() in weekdays
        )
        remaining = total_working - elapsed
        progress_pct = (elapsed / total_working * 100) if total_working > 0 else 0.0
    else:
        elapsed = 0
        remaining = total_working
        progress_pct = 0.0

    return {
        "total": total_working,
        "elapsed": elapsed,
        "remaining": remaining,
        "progress_pct": progress_pct,
        "is_complete": is_past,
    }


def _get_burn_status(utilization_pct: float, month_progress: float) -> str:
    if utilization_pct < month_progress - 5:
        return "underburning"
    elif utilization_pct > month_progress + 5:
        return "overburning"
    return "on_track"


@st.cache_data(ttl=3600, show_spinner=False)
def _get_harvest_client_projects(reference_project_id: int, account_id: str, access_token: str) -> list[dict]:
    """
    Fetch all projects (active + inactive) for the same Harvest client as reference_project_id.
    Used to resolve the correct project IDs when browsing historical months.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Harvest-Account-Id": account_id,
        "User-Agent": "CRO-Dashboard/1.0",
    }
    # Resolve client ID from the reference project
    resp = requests.get(f"{BASE_URL}/projects/{reference_project_id}", headers=headers, timeout=30)
    if resp.status_code != 200:
        return []
    client_id = resp.json().get("project", {}).get("client", {}).get("id")
    if not client_id:
        return []

    # Fetch all projects for this client — request active and inactive separately
    # because the Harvest API defaults to active-only when is_active is omitted.
    all_projects: list[dict] = []
    seen_ids: set[int] = set()
    for is_active in ("true", "false"):
        page = 1
        while True:
            resp = requests.get(
                f"{BASE_URL}/projects",
                headers=headers,
                params={
                    "client_id": client_id,
                    "is_active": is_active,
                    "per_page": 100,
                    "page": page,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                break
            batch = resp.json().get("projects", [])
            for proj in batch:
                if proj["id"] not in seen_ids:
                    seen_ids.add(proj["id"])
                    all_projects.append(proj)
            if len(batch) < 100:
                break
            page += 1
    return all_projects


@st.cache_data(ttl=3600, show_spinner=False)
def get_project_ids_for_month(
    current_project_ids: tuple[int, ...],
    year: int,
    month: int,
    account_id: str,
    access_token: str,
) -> tuple[int, ...]:
    """
    Find the Harvest project IDs for the same client(s) that cover the requested year/month.

    Strategy (in priority order):
      1. Projects whose starts_on / ends_on date range overlaps the requested month.
      2. Projects with no dates set but whose name contains the requested year (e.g. "Avis 2025").
      3. Fall back to current_project_ids if no match found.
    """
    today = datetime.date.today()
    # Current month — no remapping needed
    if year == today.year and month == today.month:
        return current_project_ids

    month_start = datetime.date(year, month, 1)
    month_end = (
        datetime.date(year + 1, 1, 1) if month == 12
        else datetime.date(year, month + 1, 1)
    ) - datetime.timedelta(days=1)

    date_matches: list[int] = []
    name_matches: list[int] = []
    seen: set[int] = set()

    for ref_pid in current_project_ids:
        try:
            projects = _get_harvest_client_projects(ref_pid, account_id, access_token)
        except Exception:
            continue

        for proj in projects:
            pid = proj["id"]
            if pid in seen:
                continue

            starts_str = proj.get("starts_on")
            ends_str = proj.get("ends_on")

            if starts_str or ends_str:
                # Match by date range
                try:
                    starts = datetime.date.fromisoformat(starts_str) if starts_str else datetime.date.min
                except ValueError:
                    starts = datetime.date.min
                try:
                    ends = datetime.date.fromisoformat(ends_str) if ends_str else datetime.date.max
                except ValueError:
                    ends = datetime.date.max

                if starts <= month_end and ends >= month_start:
                    date_matches.append(pid)
                    seen.add(pid)
            else:
                # No dates — fall back to year in project name
                m = re.search(r'\b(20\d{2})\b', proj.get("name", ""))
                if m and int(m.group(1)) == year:
                    name_matches.append(pid)
                    seen.add(pid)

    result = date_matches or name_matches
    return tuple(result) if result else current_project_ids


@st.cache_data(ttl=3600, show_spinner=False)
def get_harvest_project_date_range(
    current_project_ids: tuple[int, ...],
    account_id: str,
    access_token: str,
) -> tuple[datetime.date, datetime.date]:
    """
    Return (earliest_start_date, today) across all projects for the given client(s).
    Used to set the bounds of the month picker in the UI.
    Falls back to one year ago if no start dates are found.
    """
    today = datetime.date.today()
    earliest = today

    for ref_pid in current_project_ids:
        try:
            projects = _get_harvest_client_projects(ref_pid, account_id, access_token)
            for proj in projects:
                # Prefer starts_on, fall back to created_at (first 10 chars = YYYY-MM-DD)
                starts_str = proj.get("starts_on") or (proj.get("created_at") or "")[:10]
                if starts_str:
                    try:
                        starts = datetime.date.fromisoformat(starts_str)
                        if starts < earliest:
                            earliest = starts
                    except ValueError:
                        pass
        except Exception:
            pass

    if earliest == today:
        earliest = today.replace(year=today.year - 1)

    return earliest, today


@st.cache_data(ttl=1800, show_spinner=False)
def get_harvest_data(project_id: int, account_id: str, access_token: str,
                     year: int = None, month: int = None) -> dict:
    """
    Fetch and process all time entries for the given month for a project.
    Defaults to the current month. Returns structured data ready for the dashboard.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Harvest-Account-Id": account_id,
        "User-Agent": "CRO-Dashboard/1.0",
    }

    today = datetime.date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    first_day = datetime.date(year, month, 1)
    if month == 12:
        last_day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    to_date = today if (year == today.year and month == today.month) else last_day

    params = {
        "project_id": project_id,
        "from": first_day.strftime("%Y-%m-%d"),
        "to": to_date.strftime("%Y-%m-%d"),
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

    wd = _get_working_day_stats(year=year, month=month)
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
        "is_complete": wd.get("is_complete", False),
        "billable_pct": (total_billable / total_hours * 100) if total_hours > 0 else 0.0,
    }


def get_combined_harvest_data(project_ids: tuple, account_id: str, access_token: str,
                               year: int = None, month: int = None) -> dict:
    """
    Fetch multiple Harvest projects and merge into a single result.
    Hours and budgets are summed across all projects; projections are
    recalculated on the combined totals.
    For a single project ID this is equivalent to get_harvest_data.
    """
    if len(project_ids) == 1:
        return get_harvest_data(project_ids[0], account_id, access_token, year=year, month=month)

    results = [get_harvest_data(pid, account_id, access_token, year=year, month=month) for pid in project_ids]

    # Sum budgets across ALL projects directly from PROJECT_BUDGETS, regardless of whether
    # any hours have been logged — prevents a project with 0 hours in a group from losing
    # its budget contribution.
    combined_budgets: dict[str, float] = {}
    for pid in project_ids:
        for group, budget in PROJECT_BUDGETS.get(pid, {}).items():
            combined_budgets[group] = combined_budgets.get(group, 0.0) + budget

    # Merge task groups: sum hours and task-level breakdowns
    merged: dict[str, dict] = {}
    for pid, result in zip(project_ids, results):
        for tg in result["task_groups"]:
            g = tg["group"]
            if g not in merged:
                merged[g] = {
                    "group": g, "hours": 0.0,
                    "tasks": {}, "per_project": {}, "per_project_tasks": {},
                }
            merged[g]["hours"] += tg["hours"]
            merged[g]["per_project"][pid] = tg["hours"]
            merged[g]["per_project_tasks"][pid] = tg["tasks"]  # already sorted by hours desc
            for task_name, task_hours in tg["tasks"].items():
                merged[g]["tasks"][task_name] = merged[g]["tasks"].get(task_name, 0.0) + task_hours

    # Recalculate all derived fields on combined totals
    wd = _get_working_day_stats(year=year, month=month)
    month_progress = wd["progress_pct"]

    task_groups_data = []
    for group, data in sorted(merged.items()):
        hours = data["hours"]
        budgeted = combined_budgets.get(group, 0.0)
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
            "per_project_tasks": data.get("per_project_tasks", {}),
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
        "is_complete": wd.get("is_complete", False),
        "billable_pct": (total_billable / total_hours * 100) if total_hours > 0 else 0.0,
    }
