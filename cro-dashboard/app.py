import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from data_sources.harvest import get_harvest_data
from data_sources.jira import get_jira_velocity
from data_sources.monday_com import get_monday_velocity
from data_sources.trello import get_trello_velocity

st.set_page_config(
    page_title="CRO Performance Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

STATUS_ICON = {
    "overburning":  "🔴",
    "on_track":     "🟡",
    "underburning": "🟢",
    "exceeding":    "🚀",
    "behind":       "⚠️",
}

STATUS_LABEL = {
    "overburning":  "Overburning",
    "on_track":     "On Track",
    "underburning": "Underburning",
    "exceeding":    "Exceeding Target",
    "behind":       "Behind Target",
}

BURN_BAR_COLOR = {
    "overburning":  "#ef4444",
    "on_track":     "#f59e0b",
    "underburning": "#10b981",
}


def render_hours(harvest_data: dict) -> None:
    month_progress = harvest_data["month_progress"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Billable hours", f"{harvest_data['total_billable']:.1f}h")
    c2.metric("Non-billable hours", f"{sum(harvest_data['non_billable'].values()):.1f}h")
    c3.metric("Month complete", f"{month_progress:.0f}%")

    st.caption(f"Based on working days — {month_progress:.0f}% of the month has passed")
    st.progress(month_progress / 100)

    if not harvest_data["task_groups"]:
        st.info("No billable hours logged yet this month.")
        return

    st.divider()

    for tg in harvest_data["task_groups"]:
        status = tg["status"]
        icon = STATUS_ICON[status]
        bar_color = BURN_BAR_COLOR[status]

        fig = go.Figure(go.Bar(
            x=[tg["utilization"]],
            y=[tg["group"]],
            orientation="h",
            marker_color=bar_color,
            text=f"{tg['utilization']:.0f}%",
            textposition="inside",
            insidetextanchor="start",
            width=0.5,
        ))
        fig.add_vline(
            x=month_progress,
            line_dash="dot",
            line_color="#94a3b8",
            annotation_text="Today",
            annotation_position="top",
            annotation_font_size=10,
        )
        fig.update_layout(
            height=55,
            margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(range=[0, max(100, tg["utilization"] + 5)], visible=False),
            yaxis=dict(visible=False),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )

        left, right = st.columns([5, 2])
        with left:
            st.markdown(f"**{icon} {tg['group']}** — {tg['hours']:.1f}h of {tg['budgeted']:.1f}h budgeted")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with right:
            st.markdown("<br>", unsafe_allow_html=True)
            projected = tg.get("projected", 0)
            proj_delta = tg.get("projected_delta", 0)
            budgeted = tg["budgeted"]

            if budgeted > 0:
                if projected > budgeted * 1.05:
                    proj_color = "#ef4444"
                    proj_icon = "🔴"
                elif projected < budgeted * 0.95:
                    proj_color = "#f59e0b"
                    proj_icon = "🟡"
                else:
                    proj_color = "#10b981"
                    proj_icon = "🟢"

                delta_str = (
                    f"+{proj_delta:.1f}h over budget"
                    if proj_delta > 0
                    else f"{abs(proj_delta):.1f}h under budget"
                )
                st.markdown(
                    f"**{proj_icon} Projected: {projected:.1f}h**  \n"
                    f"<span style='color:{proj_color};font-size:0.85em'>{delta_str}</span>",
                    unsafe_allow_html=True,
                )
                current_rate = tg.get("daily_rate", 0)
                required_rate = tg.get("required_daily_rate", 0)
                st.caption(f"Need {required_rate:.1f}h/day · burning {current_rate:.1f}h/day")

        tasks = tg.get("tasks", {})
        if tasks:
            with st.expander(f"Task breakdown — {len(tasks)} task type{'s' if len(tasks) != 1 else ''}"):
                for task_name, task_hours in tasks.items():
                    pct = (task_hours / tg["hours"] * 100) if tg["hours"] > 0 else 0
                    st.markdown(
                        f"&nbsp;&nbsp;**{task_name}** &nbsp; {task_hours:.1f}h &nbsp; "
                        f"<span style='color:#94a3b8'>({pct:.0f}% of group)</span>",
                        unsafe_allow_html=True,
                    )


def render_velocity(velocity_data: dict, client_name: str) -> None:
    current = velocity_data["current_month_count"]
    target = velocity_data["target_per_month"]
    ytd = velocity_data["ytd_count"]
    ytd_target = velocity_data["ytd_target"]
    month_label = datetime.date.today().strftime("%b")

    c1, c2 = st.columns(2)
    c1.metric(
        f"This month ({month_label})",
        f"{current} / {target}",
        delta=f"{current - target:+d} vs target",
        delta_color="normal",
    )
    c2.metric(
        "Year to date",
        f"{ytd} / {ytd_target}",
        delta=f"{ytd - ytd_target:+d} vs target",
        delta_color="normal",
    )

    monthly = velocity_data.get("monthly_data", [])
    if monthly:
        df = pd.DataFrame(monthly)
        bar_colors = [
            "#10b981" if row["count"] >= row["target"] else "#ef4444"
            for _, row in df.iterrows()
        ]
        max_count = int(df["count"].max()) if not df.empty else target
        fig = go.Figure()
        fig.add_hline(
            y=target,
            line_dash="dash",
            line_color="#94a3b8",
            annotation_text=f"Target ({target})",
            annotation_position="top right",
            annotation_font_size=11,
        )
        fig.add_trace(go.Bar(
            x=df["month"],
            y=df["count"],
            marker_color=bar_colors,
            text=df["count"],
            textposition="outside",
        ))
        fig.update_layout(
            height=260,
            margin=dict(l=0, r=50, t=10, b=0),
            showlegend=False,
            yaxis=dict(
                title="Experiments",
                gridcolor="#f1f5f9",
                range=[0, max(max_count, target) * 1.25],
            ),
            xaxis=dict(title=""),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    items = velocity_data.get("current_month_items", [])
    if items:
        with st.expander(f"This month's experiments ({len(items)})"):
            for item in items:
                st.write(f"• {item}")
    else:
        st.caption("No experiments have hit the target column yet this month.")

    all_month_items = velocity_data.get("all_month_items", {})
    if all_month_items:
        with st.expander("Full year breakdown by month"):
            for month, month_items in all_month_items.items():
                st.markdown(f"**{month}** — {len(month_items)} experiment{'s' if len(month_items) != 1 else ''}")
                for item in month_items:
                    st.write(f"• {item}")
                st.divider()


def load_client_data(client_name: str, cfg: dict) -> tuple:
    try:
        harvest_data = get_harvest_data(
            cfg["harvest_project_id"],
            st.secrets["harvest"]["account_id"],
            st.secrets["harvest"]["access_token"],
        )
    except Exception as exc:
        st.error(f"Could not load Harvest data: {exc}")
        harvest_data = None

    pm = cfg["pm_tool"]
    velocity_data = None

    try:
        if pm == "trello":
            velocity_data = get_trello_velocity(
                st.secrets["trello"]["api_key"],
                st.secrets["trello"]["token"],
                st.secrets["trello"]["board_id"],
                cfg["velocity_target_per_month"],
            )
        elif pm == "monday":
            velocity_data = get_monday_velocity(
                st.secrets["monday"]["api_key"],
                st.secrets["monday"]["board_id"],
                cfg["velocity_target_per_month"],
            )
        elif pm == "jira_tesco":
            velocity_data = get_jira_velocity(
                jira_url=st.secrets["jira_tesco"]["url"],
                email=st.secrets["jira_tesco"]["email"],
                api_token=st.secrets["jira_tesco"]["api_token"],
                board_id=None,
                target_column=config.JIRA_TESCO_TARGET_COLUMN,
                target_per_month=cfg["velocity_target_per_month"],
                mode="jql",
                epic=config.JIRA_TESCO_TARGET_EPIC,
            )
        elif pm == "jira_avis":
            velocity_data = get_jira_velocity(
                jira_url=st.secrets["jira_avis"]["url"],
                email=st.secrets["jira_avis"]["email"],
                api_token=st.secrets["jira_avis"]["api_token"],
                board_id=str(config.JIRA_AVIS_BOARD_ID),
                target_column=config.JIRA_AVIS_TARGET_COLUMN,
                target_per_month=cfg["velocity_target_per_month"],
                mode="agile",
                epic=None,
                label_filter=config.JIRA_AVIS_LABEL_FILTER,
            )
    except Exception as exc:
        st.error(f"Could not load velocity data: {exc}")

    return harvest_data, velocity_data


def main() -> None:
    header_col, refresh_col = st.columns([5, 1])
    with header_col:
        st.title("📊 CRO Performance Dashboard")
        st.caption(
            f"{datetime.datetime.now().strftime('%A, %d %B %Y')}  ·  "
            "Data cached for 30 minutes"
        )
    with refresh_col:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    st.divider()

    client_names = list(config.CLIENTS.keys())
    tab_labels = [f"{cfg['icon']} {name}" for name, cfg in config.CLIENTS.items()]
    tabs = st.tabs(tab_labels)

    for tab, client_name in zip(tabs, client_names):
        cfg = config.CLIENTS[client_name]

        with tab:
            with st.spinner(f"Loading {client_name} data…"):
                harvest_data, velocity_data = load_client_data(client_name, cfg)

            hours_col, velocity_col = st.columns([3, 2], gap="large")

            with hours_col:
                st.subheader("🕐 Hours")
                if harvest_data:
                    render_hours(harvest_data)
                else:
                    st.warning("Hours data unavailable.")

            with velocity_col:
                st.subheader("🚀 Experiment Velocity")
                st.caption(f"Target: {cfg['velocity_target_per_month']} experiments / month")
                if velocity_data:
                    render_velocity(velocity_data, client_name)
                else:
                    st.warning("Velocity data unavailable.")


if __name__ == "__main__":
    main()
