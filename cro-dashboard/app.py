import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from data_sources.harvest import get_harvest_data, get_combined_harvest_data
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


def render_hours(harvest_data: dict, key_prefix: str = "") -> None:
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
            st.plotly_chart(
                fig,
                key=f"{key_prefix}_{tg['group']}",
                use_container_width=True,
                config={"displayModeBar": False},
            )
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
        per_project = tg.get("per_project", {})
        has_split = len(per_project) > 1

        expander_label = f"Task breakdown — {len(tasks)} task type{'s' if len(tasks) != 1 else ''}"
        if has_split:
            expander_label += " · project split"

        if tasks or has_split:
            with st.expander(expander_label):
                if has_split:
                    split_parts = []
                    for pid, proj_hours in per_project.items():
                        label = config.PROJECT_LABELS.get(pid, f"Project {pid}")
                        pct = (proj_hours / tg["hours"] * 100) if tg["hours"] > 0 else 0
                        split_parts.append(f"**{label}**: {proj_hours:.1f}h ({pct:.0f}%)")
                    st.markdown(" &nbsp;·&nbsp; ".join(split_parts), unsafe_allow_html=True)
                    if tasks:
                        st.divider()

                for task_name, task_hours in tasks.items():
                    pct = (task_hours / tg["hours"] * 100) if tg["hours"] > 0 else 0
                    st.markdown(
                        f"&nbsp;&nbsp;**{task_name}** &nbsp; {task_hours:.1f}h &nbsp; "
                        f"<span style='color:#94a3b8'>({pct:.0f}% of group)</span>",
                        unsafe_allow_html=True,
                    )


_MONTH_ABB_TO_FULL = {m[:3]: m for m in [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]}

# Categorical palette for non-A/B activity types
_TYPE_COLORS = [
    "#475569", "#64748b", "#7c3aed", "#b45309", "#0369a1",
    "#be123c", "#065f46", "#92400e", "#1e3a5f",
]


def render_velocity(velocity_data: dict, client_name: str) -> None:
    current = velocity_data["current_month_count"]
    target = velocity_data["target_per_month"]
    ytd = velocity_data["ytd_count"]
    ytd_target = velocity_data["ytd_target"]
    month_label = datetime.date.today().strftime("%b")
    current_month_full = datetime.date.today().strftime("%B")

    activity_breakdown = velocity_data.get("activity_type_breakdown", {})
    has_breakdown = bool(activity_breakdown)

    # Metrics — counts are always A/B only when breakdown is present
    c1, c2 = st.columns(2)
    c1.metric(
        f"A/B tests ({month_label})" if has_breakdown else f"This month ({month_label})",
        f"{current} / {target}",
        delta=f"{current - target:+d} vs target",
        delta_color="normal",
    )
    c2.metric(
        "A/B YTD" if has_breakdown else "Year to date",
        f"{ytd} / {ytd_target}",
        delta=f"{ytd - ytd_target:+d} vs target",
        delta_color="normal",
    )

    monthly = velocity_data.get("monthly_data", [])
    if monthly:
        df = pd.DataFrame(monthly)
        ab_counts = df["count"].tolist()
        ab_colors = ["#10b981" if c >= target else "#ef4444" for c in ab_counts]

        fig = go.Figure()
        fig.add_hline(
            y=target,
            line_dash="dash",
            line_color="#94a3b8",
            annotation_text=f"A/B target ({target})" if has_breakdown else f"Target ({target})",
            annotation_position="top right",
            annotation_font_size=11,
        )

        if has_breakdown:
            # Stacked bar: A/B (primary) + other work (muted) on top
            other_counts = []
            for row in df.itertuples():
                full = _MONTH_ABB_TO_FULL.get(row.month, row.month)
                total = sum(activity_breakdown.get(full, {}).values())
                other_counts.append(max(0, total - row.count))

            fig.add_trace(go.Bar(
                name="A/B tests",
                x=df["month"],
                y=ab_counts,
                marker_color=ab_colors,
                text=ab_counts,
                textposition="inside",
            ))
            fig.add_trace(go.Bar(
                name="Other work",
                x=df["month"],
                y=other_counts,
                marker_color="#475569",
                opacity=0.55,
                text=[v if v > 0 else "" for v in other_counts],
                textposition="inside",
            ))
            stacked_max = max((a + o) for a, o in zip(ab_counts, other_counts)) if ab_counts else target
            y_range = [0, max(stacked_max, target) * 1.3]
            fig.update_layout(barmode="stack", showlegend=True,
                              legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                          xanchor="right", x=1, font=dict(size=11)))
        else:
            fig.add_trace(go.Bar(
                x=df["month"],
                y=ab_counts,
                marker_color=ab_colors,
                text=ab_counts,
                textposition="outside",
            ))
            max_count = int(df["count"].max()) if not df.empty else target
            y_range = [0, max(max_count, target) * 1.25]
            fig.update_layout(showlegend=False)

        fig.update_layout(
            height=270,
            margin=dict(l=0, r=50, t=10, b=0),
            yaxis=dict(
                title="Items completed",
                gridcolor="#1e293b",
                range=y_range,
            ),
            xaxis=dict(title=""),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key=f"vel_{client_name}", config={"displayModeBar": False})

    # Current month work-type breakdown (Dominos only / when breakdown exists)
    if has_breakdown:
        current_types = activity_breakdown.get(current_month_full, {})
        if current_types:
            sorted_types = sorted(current_types.items(), key=lambda x: -x[1])
            type_labels = [t for t, _ in sorted_types]
            type_counts_vals = [c for _, c in sorted_types]
            bar_colors = [
                "#10b981" if t == config.MONDAY_AB_TYPE_LABEL else _TYPE_COLORS[i % len(_TYPE_COLORS)]
                for i, t in enumerate(type_labels)
            ]
            fig_bd = go.Figure(go.Bar(
                x=type_counts_vals,
                y=type_labels,
                orientation="h",
                marker_color=bar_colors,
                text=type_counts_vals,
                textposition="auto",
            ))
            fig_bd.update_layout(
                height=max(130, len(type_labels) * 32),
                margin=dict(l=0, r=30, t=0, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
                xaxis=dict(visible=False),
                yaxis=dict(visible=True, autorange="reversed"),
            )
            st.caption(f"Work type breakdown — {current_month_full}")
            st.plotly_chart(fig_bd, use_container_width=True,
                            key=f"wtype_{client_name}", config={"displayModeBar": False})

    items = velocity_data.get("current_month_items", [])
    label = "A/B tests" if has_breakdown else "experiments"
    if items:
        with st.expander(f"This month's {label} ({len(items)})"):
            for item in items:
                st.write(f"• {item}")
    else:
        st.caption(f"No {label} have hit the target column yet this month.")

    all_month_items = velocity_data.get("all_month_items", {})
    if all_month_items:
        with st.expander(f"Full year {label} breakdown"):
            for month, month_items in all_month_items.items():
                extra = ""
                if has_breakdown and month in activity_breakdown:
                    total_work = sum(activity_breakdown[month].values())
                    other_n = total_work - len(month_items)
                    if other_n > 0:
                        extra = f" · {other_n} other items"
                st.markdown(f"**{month}** — {len(month_items)} A/B test{'s' if len(month_items) != 1 else ''}{extra}")
                for item in month_items:
                    st.write(f"• {item}")
                st.divider()

    if has_breakdown:
        other_this_month = velocity_data.get("current_month_other_items", {})
        if other_this_month:
            total_other = sum(len(v) for v in other_this_month.values())
            with st.expander(f"This month's other work ({total_other})"):
                for wtype, names in other_this_month.items():
                    st.markdown(f"**{wtype}** — {len(names)}")
                    for name in names:
                        st.write(f"• {name}")

        all_other = velocity_data.get("all_month_other_items", {})
        if all_other:
            with st.expander("Full year other work breakdown"):
                for month, types in all_other.items():
                    total = sum(len(v) for v in types.values())
                    st.markdown(f"**{month}** — {total} item{'s' if total != 1 else ''}")
                    for wtype, names in types.items():
                        st.markdown(f"&nbsp;&nbsp;*{wtype}* ({len(names)})",
                                    unsafe_allow_html=True)
                        for name in names:
                            st.write(f"&nbsp;&nbsp;&nbsp;&nbsp;• {name}")
                    st.divider()


def load_client_data(client_name: str, cfg: dict) -> tuple:
    try:
        project_ids = tuple(
            cfg.get("harvest_project_ids") or [cfg["harvest_project_id"]]
        )
        harvest_data = get_combined_harvest_data(
            project_ids,
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
                board_id=None,
                target_column=config.JIRA_AVIS_TARGET_COLUMN,
                target_per_month=cfg["velocity_target_per_month"],
                mode="jql",
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
                    render_hours(harvest_data, key_prefix=client_name)
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
