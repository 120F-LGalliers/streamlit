import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from data_sources.harvest import get_harvest_data, get_combined_harvest_data, get_project_ids_for_month, get_harvest_project_date_range
from data_sources.jira import get_jira_velocity, get_jira_transition_times, get_jira_win_rate
from data_sources.monday_com import get_monday_velocity, get_monday_transition_times, get_monday_win_rate
from data_sources.trello import get_trello_velocity, get_trello_transition_times, get_trello_win_rate

st.set_page_config(
    page_title="CRO Performance Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

STATUS_ICON = {
    "overburning":  "🟡",  # amber — overburning rate but not yet over budget
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
    "overburning":  "#f59e0b",  # amber — bar stays calm; projection warning shown separately
    "on_track":     "#f59e0b",
    "underburning": "#10b981",
}

# Quarter picker — maps label → (start_month, end_month)
QUARTER_MONTHS = {
    "Q1 (Jan–Mar)": (1, 3),
    "Q2 (Apr–Jun)": (4, 6),
    "Q3 (Jul–Sep)": (7, 9),
    "Q4 (Oct–Dec)": (10, 12),
}
_QUARTER_OPTS = list(QUARTER_MONTHS.keys())


def _quarter_date_range(start_q: str, end_q: str) -> tuple:
    """Return (start_date, end_date) covering the selected quarter range in the current year."""
    year = datetime.date.today().year
    start_month = QUARTER_MONTHS[start_q][0]
    end_month = QUARTER_MONTHS[end_q][1]
    start_date = datetime.date(year, start_month, 1)
    # Last day of end_month without importing calendar
    end_date = (
        datetime.date(year, end_month + 1, 1) - datetime.timedelta(days=1)
        if end_month < 12
        else datetime.date(year, 12, 31)
    )
    return start_date, end_date


def render_hours(harvest_data: dict, key_prefix: str = "") -> None:
    month_progress = harvest_data["month_progress"]

    is_complete = harvest_data.get("is_complete", False)

    c1, c2, c3 = st.columns(3)
    c1.metric("Billable hours", f"{harvest_data['total_billable']:.1f}h")
    c2.metric("Non-billable hours", f"{sum(harvest_data['non_billable'].values()):.1f}h")
    c3.metric("Month complete", f"{month_progress:.0f}%")

    if is_complete:
        st.caption("Complete month — all working days elapsed")
    else:
        st.caption(f"Based on working days — {month_progress:.0f}% of the month has passed")

    # Dual-bar overview: time elapsed vs budget consumed
    total_budget = sum(tg["budgeted"] for tg in harvest_data.get("task_groups", []))
    if total_budget > 0:
        budget_pct = harvest_data["total_billable"] / total_budget * 100
        x_max = max(115, budget_pct * 1.08)

        if harvest_data["total_billable"] > total_budget:
            budget_color = "#ef4444"   # over budget
        elif budget_pct > month_progress + 5:
            budget_color = "#f59e0b"   # overburning but not yet over
        else:
            budget_color = "#10b981"   # on track or under

        fig_ov = go.Figure()
        fig_ov.add_trace(go.Bar(
            x=[month_progress],
            y=["Time elapsed"],
            orientation="h",
            marker_color="#3b82f6",
            text=f"{month_progress:.0f}%",
            textposition="inside",
            insidetextanchor="start",
            width=0.45,
        ))
        fig_ov.add_trace(go.Bar(
            x=[budget_pct],
            y=["Budget used"],
            orientation="h",
            marker_color=budget_color,
            text=f"{budget_pct:.0f}%  ·  {harvest_data['total_billable']:.0f}h of {total_budget:.0f}h",
            textposition="inside",
            insidetextanchor="start",
            width=0.45,
        ))
        fig_ov.add_vline(
            x=100,
            line_dash="dot",
            line_color="#94a3b8",
            annotation_text="Budget",
            annotation_position="top right",
            annotation_font_size=9,
        )
        fig_ov.update_layout(
            height=90,
            margin=dict(l=0, r=50, t=16, b=0),
            xaxis=dict(range=[0, x_max], visible=False),
            yaxis=dict(visible=True, automargin=True),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )
        st.plotly_chart(fig_ov, use_container_width=True,
                        key=f"budget_overview_{key_prefix}",
                        config={"displayModeBar": False})
    else:
        st.progress(month_progress / 100)

    if not harvest_data["task_groups"]:
        if is_complete:
            st.info("No billable hours were recorded for this month.")
        else:
            st.info("No billable hours logged yet this month.")
        return

    st.divider()

    # Consistent x-axis across all bars so the "Today" line lands at the same visual position
    max_utilization = max((tg["utilization"] for tg in harvest_data["task_groups"]), default=100)
    x_max = max(100, max_utilization + 5)

    for tg in harvest_data["task_groups"]:
        status = tg["status"]
        icon = STATUS_ICON[status]
        bar_color = BURN_BAR_COLOR[status]

        # Red only when hours have actually exceeded the budget; amber/green otherwise
        actual_bar_color = "#ef4444" if tg["hours"] > tg["budgeted"] else bar_color

        fig = go.Figure(go.Bar(
            x=[tg["utilization"]],
            y=[tg["group"]],
            orientation="h",
            marker_color=actual_bar_color,
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
            xaxis=dict(range=[0, x_max], visible=False),
            yaxis=dict(visible=False),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )

        projected = tg.get("projected", 0)
        already_over = tg["hours"] > tg["budgeted"]
        if is_complete:
            overrun_warning = ""  # red bar already signals over budget
        else:
            overrun_warning = (
                " &nbsp; <span style='color:#ef4444;font-size:0.8em'>⚠️ projected overrun</span>"
                if projected > tg["budgeted"] * 1.05 and not already_over else ""
            )

        left, right = st.columns([5, 2])
        with left:
            st.markdown(
                f"**{icon} {tg['group']}** — {tg['hours']:.1f}h of {tg['budgeted']:.1f}h budgeted{overrun_warning}",
                unsafe_allow_html=True,
            )
            st.plotly_chart(
                fig,
                key=f"{key_prefix}_{tg['group']}",
                use_container_width=True,
                config={"displayModeBar": False},
            )
        with right:
            st.markdown("<br>", unsafe_allow_html=True)
            proj_delta = tg.get("projected_delta", 0)
            budgeted = tg["budgeted"]

            if budgeted > 0:
                if projected > budgeted:
                    proj_color = "#ef4444"
                    proj_icon = "🔴"
                elif projected >= budgeted * 0.90:
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
                proj_label = "Final" if is_complete else "Projected"
                st.markdown(
                    f"**{proj_icon} {proj_label}: {projected:.1f}h**  \n"
                    f"<span style='color:{proj_color};font-size:0.85em'>{delta_str}</span>",
                    unsafe_allow_html=True,
                )
                if not is_complete:
                    current_rate = tg.get("daily_rate", 0)
                    required_rate = tg.get("required_daily_rate", 0)
                    st.caption(f"Need {required_rate:.1f}h/day · burning {current_rate:.1f}h/day")

        tasks = tg.get("tasks", {})
        per_project = tg.get("per_project", {})
        per_project_tasks = tg.get("per_project_tasks", {})
        has_split = len(per_project) > 1

        expander_label = f"Task breakdown — {len(tasks)} task type{'s' if len(tasks) != 1 else ''}"
        if has_split:
            expander_label += " · project split"

        if tasks or has_split:
            with st.expander(expander_label):
                if has_split:
                    # Per-project section: each project's hours and its own task list
                    for pid, proj_hours in per_project.items():
                        proj_label = config.PROJECT_LABELS.get(pid, f"Project {pid}")
                        pct = (proj_hours / tg["hours"] * 100) if tg["hours"] > 0 else 0
                        st.markdown(f"**{proj_label}** — {proj_hours:.1f}h ({pct:.0f}%)")
                        for task_name, task_hours in per_project_tasks.get(pid, {}).items():
                            task_pct = (task_hours / proj_hours * 100) if proj_hours > 0 else 0
                            st.markdown(
                                f"&nbsp;&nbsp;{task_name} &nbsp; {task_hours:.1f}h &nbsp;"
                                f"<span style='color:#94a3b8'>({task_pct:.0f}%)</span>",
                                unsafe_allow_html=True,
                            )
                    st.divider()
                    st.markdown("**Combined**")

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


def render_velocity(velocity_data: dict, client_name: str, velocity_period: str = "month") -> None:
    target = velocity_data["target_per_month"]
    ytd = velocity_data["ytd_count"]
    month_label = datetime.date.today().strftime("%b")
    current_month_full = datetime.date.today().strftime("%B")

    activity_breakdown = velocity_data.get("activity_type_breakdown", {})
    has_breakdown = bool(activity_breakdown)
    monthly = velocity_data.get("monthly_data", [])

    if velocity_period == "quarter":
        current_quarter = (datetime.date.today().month - 1) // 3 + 1
        ytd_target = current_quarter * target
        period_label = f"Q{current_quarter}"

        # Aggregate monthly data into quarters
        quarterly_bars = []
        for q in range(1, current_quarter + 1):
            q_start = (q - 1) * 3
            q_count = sum(
                monthly[i]["count"] for i in range(q_start, q_start + 3) if i < len(monthly)
            )
            quarterly_bars.append({"quarter": f"Q{q}", "count": q_count})
        current = quarterly_bars[-1]["count"] if quarterly_bars else 0
    else:
        current = velocity_data["current_month_count"]
        ytd_target = velocity_data["ytd_target"]
        period_label = month_label
        quarterly_bars = []

    # Metrics — counts are always A/B only when breakdown is present
    c1, c2 = st.columns(2)
    c1.metric(
        f"A/B tests ({period_label})" if has_breakdown else f"This {velocity_period} ({period_label})",
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

    if velocity_period == "quarter" and quarterly_bars:
        # Quarterly bar chart
        q_labels = [b["quarter"] for b in quarterly_bars]
        q_counts = [b["count"] for b in quarterly_bars]
        q_colors = ["#10b981" if c >= target else "#ef4444" for c in q_counts]
        fig = go.Figure()
        fig.add_hline(
            y=target,
            line_dash="dash",
            line_color="#94a3b8",
            annotation_text=f"Target ({target}/quarter)",
            annotation_position="top right",
            annotation_font_size=11,
        )
        fig.add_trace(go.Bar(
            x=q_labels,
            y=q_counts,
            marker_color=q_colors,
            text=q_counts,
            textposition="outside",
        ))
        fig.update_layout(
            height=270,
            showlegend=False,
            margin=dict(l=0, r=50, t=10, b=0),
            yaxis=dict(
                title="Experiments",
                gridcolor="#1e293b",
                range=[0, max(max(q_counts, default=0), target) * 1.4],
                dtick=1,
            ),
            xaxis=dict(title=""),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key=f"vel_{client_name}", config={"displayModeBar": False})

    elif monthly:
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
                title="Experiments",
                gridcolor="#1e293b",
                range=y_range,
            ),
            xaxis=dict(title=""),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key=f"vel_{client_name}", config={"displayModeBar": False})

    all_month_items = velocity_data.get("all_month_items", {})
    label = "A/B tests" if has_breakdown else "experiments"

    # Month picker + work-type breakdown (Dominos only / when breakdown exists)
    if has_breakdown:
        all_months_ordered = list(_MONTH_ABB_TO_FULL.values())
        available_months = [m for m in all_months_ordered if m in activity_breakdown]
        if available_months:
            default_idx = (
                available_months.index(current_month_full)
                if current_month_full in available_months
                else len(available_months) - 1
            )
            selected_month = st.selectbox(
                "View month",
                options=available_months,
                index=default_idx,
                key=f"month_picker_{client_name}",
            )
        else:
            selected_month = current_month_full

        current_types = activity_breakdown.get(selected_month, {})
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
            st.caption(f"Work type breakdown — {selected_month}")
            st.plotly_chart(fig_bd, use_container_width=True,
                            key=f"wtype_{client_name}", config={"displayModeBar": False})

        # Items for selected month
        selected_items = all_month_items.get(selected_month, [])
        if selected_items:
            with st.expander(f"{selected_month} {label} ({len(selected_items)})"):
                for item in selected_items:
                    st.write(f"• {item}")
        else:
            st.caption(f"No {label} recorded for {selected_month}.")

        # Other work for selected month
        all_other = velocity_data.get("all_month_other_items", {})
        selected_other = all_other.get(selected_month, {})
        if selected_other:
            total_other = sum(len(v) for v in selected_other.values())
            with st.expander(f"{selected_month} other work ({total_other})"):
                for wtype, names in selected_other.items():
                    st.markdown(f"**{wtype}** — {len(names)}")
                    for name in names:
                        st.write(f"• {name}")

    else:
        items = velocity_data.get("current_month_items", [])
        if items:
            with st.expander(f"This month's {label} ({len(items)})"):
                for item in items:
                    st.write(f"• {item}")
        else:
            st.caption(f"No {label} have hit the target column yet this month.")

    # Full year breakdown — always shown for all clients
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


def render_win_rate(win_data: dict, client_name: str) -> None:
    """
    Display overall win rate metrics and a monthly grouped bar + win-rate line chart.
    Win rate = cards reaching a win column ÷ cards reaching Full Launch.
    """
    total_fl = win_data["total_full_launch"]
    total_w = win_data["total_winners"]
    overall = win_data["overall_win_rate"]
    monthly = win_data["monthly_data"]
    concluded_label = win_data.get("concluded_label", "Full Launch")

    c1, c2, c3 = st.columns(3)
    c1.metric(f"{concluded_label} (YTD)", total_fl)
    c2.metric("Winners (YTD)", total_w)
    c3.metric("Win Rate (YTD)", f"{overall:.0f}%")

    if not monthly:
        st.caption("No win rate data available for this year yet.")
        return

    months = [m["month"] for m in monthly]
    fl_counts = [m["full_launch"] for m in monthly]
    w_counts = [m["winners"] for m in monthly]
    win_rates = [m["win_rate"] for m in monthly]

    monthly_rate_ceil = max(110, max(win_rates, default=0) * 1.15)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name=concluded_label,
        x=months,
        y=fl_counts,
        marker_color="#475569",
        opacity=0.75,
        yaxis="y1",
    ))
    fig.add_trace(go.Bar(
        name="Winners",
        x=months,
        y=w_counts,
        marker_color="#10b981",
        yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        name="Win Rate %",
        x=months,
        y=win_rates,
        mode="lines+markers",
        line=dict(color="#f59e0b", width=2),
        marker=dict(size=7),
        yaxis="y2",
    ))
    fig.update_layout(
        height=270,
        barmode="group",
        margin=dict(l=0, r=50, t=10, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(
            title="Cards",
            gridcolor="#1e293b",
            range=[0, max(max(fl_counts, default=1), max(w_counts, default=1)) * 1.3],
        ),
        yaxis2=dict(
            title="Win Rate %",
            overlaying="y",
            side="right",
            range=[0, monthly_rate_ceil],
            showgrid=False,
            ticksuffix="%",
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=11)),
    )
    st.plotly_chart(fig, use_container_width=True,
                    key=f"winrate_{client_name}", config={"displayModeBar": False})

    # Label breakdown (TSB only — other clients don't return by_label)
    by_label = win_data.get("by_label", {})
    if by_label:
        st.caption("Win rate by label")
        label_names = list(by_label.keys())
        label_concluded = [by_label[l]["concluded"] for l in label_names]
        label_winners = [by_label[l]["winners"] for l in label_names]
        label_rates = [by_label[l]["win_rate"] for l in label_names]

        # y2 range must accommodate actual rates (can exceed 100% when cards skipped Full Launch)
        lbl_rate_ceil = max(110, max(label_rates, default=0) * 1.15)

        fig_lbl = go.Figure()
        fig_lbl.add_trace(go.Bar(
            name=concluded_label,
            x=label_names,
            y=label_concluded,
            marker_color="#475569",
            opacity=0.75,
            yaxis="y1",
        ))
        fig_lbl.add_trace(go.Bar(
            name="Winners",
            x=label_names,
            y=label_winners,
            marker_color="#10b981",
            yaxis="y1",
        ))
        fig_lbl.add_trace(go.Scatter(
            name="Win Rate %",
            x=label_names,
            y=label_rates,
            mode="lines+markers",
            line=dict(color="#f59e0b", width=2),
            marker=dict(size=7),
            yaxis="y2",
        ))
        fig_lbl.update_layout(
            height=240,
            barmode="group",
            margin=dict(l=0, r=50, t=10, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(
                title="Cards",
                gridcolor="#1e293b",
                range=[0, max(max(label_concluded, default=1), max(label_winners, default=1)) * 1.3],
            ),
            yaxis2=dict(
                title="Win Rate %",
                overlaying="y",
                side="right",
                range=[0, lbl_rate_ceil],
                showgrid=False,
                ticksuffix="%",
            ),
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="right", x=1, font=dict(size=11)),
        )
        st.plotly_chart(fig_lbl, use_container_width=True,
                        key=f"winrate_label_{client_name}", config={"displayModeBar": False})

    # Monthly detail expanders
    monthly_fl = win_data.get("monthly_full_launch", {})
    monthly_w = win_data.get("monthly_winners", {})
    months_with_data = [m for m in monthly_fl if monthly_fl[m] or monthly_w.get(m)]

    if months_with_data:
        with st.expander("Monthly breakdown"):
            for month_full in [m for m in [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ] if m in monthly_fl or m in monthly_w]:
                fl_cards = monthly_fl.get(month_full, [])
                w_cards = monthly_w.get(month_full, [])
                rate = (len(w_cards) / len(fl_cards) * 100) if fl_cards else 0
                st.markdown(
                    f"**{month_full}** — {len(fl_cards)} launched · "
                    f"{len(w_cards)} won · {rate:.0f}% win rate"
                )
                if fl_cards:
                    st.markdown("&nbsp;&nbsp;*Full Launch:*", unsafe_allow_html=True)
                    for name in fl_cards:
                        st.write(f"&nbsp;&nbsp;&nbsp;&nbsp;• {name}")
                if w_cards:
                    st.markdown("&nbsp;&nbsp;*Winners:*", unsafe_allow_html=True)
                    for name in w_cards:
                        st.write(f"&nbsp;&nbsp;&nbsp;&nbsp;• {name}")
                st.divider()


def render_cycle_time(transition_data: list[dict], client_name: str) -> None:
    """Horizontal bar chart of avg days per pipeline stage transition, with median overlay."""
    active = [t for t in transition_data if t["count"] > 0]
    if not active:
        st.caption("No cycle time data found for the selected period.")
        return

    labels = [f"{t['from_col']} → {t['to_col']}" for t in active]
    avg_days = [t["avg_days"] for t in active]
    median_days = [t["median_days"] for t in active]
    counts = [t["count"] for t in active]

    x_max = max(avg_days) * 1.25 if avg_days else 10  # headroom for inline text

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=avg_days,
        y=labels,
        orientation="h",
        name="Average",
        marker_color="#6366f1",
        text=[f"{d:.1f}d  (n={c})" for d, c in zip(avg_days, counts)],
        textposition="inside",
        insidetextanchor="start",
    ))
    fig.add_trace(go.Scatter(
        x=median_days,
        y=labels,
        mode="markers",
        name="Median",
        marker=dict(color="#f59e0b", size=10, symbol="diamond"),
    ))
    fig.update_layout(
        height=max(180, len(labels) * 44),
        margin=dict(l=0, r=20, t=10, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Days", gridcolor="#1e293b", range=[0, x_max]),
        yaxis=dict(autorange="reversed", automargin=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=11)),
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        key=f"cycle_{client_name}",
        config={"displayModeBar": False},
    )


def load_client_data(client_name: str, cfg: dict, year: int = None, month: int = None) -> tuple:
    try:
        current_ids = tuple(
            cfg.get("harvest_project_ids") or [cfg["harvest_project_id"]]
        )
        # Dynamically resolve the correct project IDs for the selected month —
        # project IDs change each year so we look them up from Harvest rather than
        # relying on the hardcoded values in config.
        project_ids = get_project_ids_for_month(
            current_ids,
            year or datetime.datetime.now().year,
            month or datetime.datetime.now().month,
            st.secrets["harvest"]["account_id"],
            st.secrets["harvest"]["access_token"],
        )
        harvest_data = get_combined_harvest_data(
            project_ids,
            st.secrets["harvest"]["account_id"],
            st.secrets["harvest"]["access_token"],
            year=year,
            month=month,
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
        elif pm == "trello_ripe":
            velocity_data = get_trello_velocity(
                st.secrets["trello"]["api_key"],
                st.secrets["trello"]["token"],
                config.RIPE_TRELLO_BOARD_ID,
                cfg["velocity_target_per_month"],
                target_columns=tuple(config.RIPE_TRELLO_TARGET_COLUMNS),
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
                target_columns=tuple(config.JIRA_TESCO_TARGET_COLUMNS),
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
                target_columns=(config.JIRA_AVIS_TARGET_COLUMN,),
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
            hours_col, velocity_col = st.columns([3, 2], gap="large")

            with hours_col:
                st.subheader("🕐 Hours")
                current_ids = tuple(
                    cfg.get("harvest_project_ids") or [cfg["harvest_project_id"]]
                )
                try:
                    min_date, max_date = get_harvest_project_date_range(
                        current_ids,
                        st.secrets["harvest"]["account_id"],
                        st.secrets["harvest"]["access_token"],
                    )
                except Exception:
                    min_date = datetime.date.today().replace(
                        year=datetime.date.today().year - 1, day=1
                    )
                    max_date = datetime.date.today()

                # Build month/year options from project date range, most recent first
                today = datetime.date.today()
                cursor = datetime.date(min_date.year, min_date.month, 1)
                month_options = []
                while cursor <= today:
                    month_options.append((cursor.year, cursor.month))
                    m = cursor.month + 1 if cursor.month < 12 else 1
                    y = cursor.year if cursor.month < 12 else cursor.year + 1
                    cursor = datetime.date(y, m, 1)
                month_options.reverse()  # most recent first

                month_labels = [
                    datetime.date(y, m, 1).strftime("%B %Y")
                    for y, m in month_options
                ]
                sel_label = st.selectbox(
                    "View month",
                    options=month_labels,
                    index=0,
                    key=f"hours_month_{client_name}",
                )
                sel_year, sel_month = month_options[month_labels.index(sel_label)]

            with st.spinner(f"Loading {client_name} data…"):
                harvest_data, velocity_data = load_client_data(
                    client_name, cfg, year=sel_year, month=sel_month
                )

            with hours_col:
                if harvest_data:
                    render_hours(harvest_data, key_prefix=client_name)
                else:
                    st.warning("Hours data unavailable.")

            with velocity_col:
                st.subheader("🚀 Experiment Velocity")
                _vel_period = cfg.get("velocity_period", "month")
                if _vel_period == "quarter":
                    st.caption(f"Target: {cfg['velocity_target_per_month']} experiment / quarter")
                else:
                    st.caption(f"Target: {cfg['velocity_target_per_month']} experiments / month")
                if velocity_data:
                    render_velocity(velocity_data, client_name,
                                    velocity_period=cfg.get("velocity_period", "month"))
                else:
                    st.warning("Velocity data unavailable.")

            # Win rate section — all clients
            pm = cfg["pm_tool"]
            st.divider()
            st.subheader("🏆 Win Rate")
            try:
                if pm == "trello":
                    st.caption("Winners / Live to 100% vs Full Launch")
                    win_data = get_trello_win_rate(
                        st.secrets["trello"]["api_key"],
                        st.secrets["trello"]["token"],
                        st.secrets["trello"]["board_id"],
                        full_launch_col=config.TRELLO_FULL_LAUNCH_COLUMN,
                        win_cols=tuple(config.TRELLO_WIN_COLUMNS),
                    )
                elif pm == "trello_ripe":
                    st.caption("Winners vs all concluded tests (Winners / Losers / Inconclusive)")
                    win_data = get_trello_win_rate(
                        st.secrets["trello"]["api_key"],
                        st.secrets["trello"]["token"],
                        config.RIPE_TRELLO_BOARD_ID,
                        win_cols=tuple(config.RIPE_TRELLO_WIN_COLUMNS),
                        concluded_cols=tuple(config.RIPE_TRELLO_CONCLUDED_COLUMNS),
                    )
                elif pm == "monday":
                    st.caption("Always On / Winners vs all concluded tests")
                    win_data = get_monday_win_rate(
                        st.secrets["monday"]["api_key"],
                        st.secrets["monday"]["board_id"],
                        tuple(config.MONDAY_WIN_COLUMNS),
                        tuple(config.MONDAY_CONCLUDED_COLUMNS),
                    )
                elif pm == "jira_tesco":
                    st.caption("100% Live / Winners vs all concluded tests")
                    win_data = get_jira_win_rate(
                        jira_url=st.secrets["jira_tesco"]["url"],
                        email=st.secrets["jira_tesco"]["email"],
                        api_token=st.secrets["jira_tesco"]["api_token"],
                        win_statuses=tuple(config.JIRA_TESCO_WIN_STATUSES),
                        concluded_statuses=tuple(config.JIRA_TESCO_CONCLUDED_STATUSES),
                        epic=config.JIRA_TESCO_TARGET_EPIC,
                    )
                elif pm == "jira_avis":
                    st.caption("Ready for Deployment vs all concluded tests")
                    win_data = get_jira_win_rate(
                        jira_url=st.secrets["jira_avis"]["url"],
                        email=st.secrets["jira_avis"]["email"],
                        api_token=st.secrets["jira_avis"]["api_token"],
                        win_statuses=tuple(config.JIRA_AVIS_WIN_STATUSES),
                        concluded_statuses=tuple(config.JIRA_AVIS_CONCLUDED_STATUSES),
                        label_filter=config.JIRA_AVIS_LABEL_FILTER,
                    )
                else:
                    win_data = None

                if win_data is not None:
                    render_win_rate(win_data, client_name)
            except Exception as exc:
                st.error(f"Could not load win rate data: {exc}")

            # Cycle time section — all clients
            pm = cfg["pm_tool"]
            st.divider()
            st.subheader("⏱️ Cycle Time")
            st.caption("Average days tickets spend moving between pipeline stages")

            current_q_idx = (datetime.date.today().month - 1) // 3
            qc1, qc2, _ = st.columns([1, 1, 3])
            with qc1:
                ct_start_q = st.selectbox(
                    "From quarter",
                    _QUARTER_OPTS,
                    index=0,
                    key=f"ct_start_{client_name}",
                )
            with qc2:
                ct_end_q = st.selectbox(
                    "To quarter",
                    _QUARTER_OPTS,
                    index=current_q_idx,
                    key=f"ct_end_{client_name}",
                )

            start_q_idx = _QUARTER_OPTS.index(ct_start_q)
            end_q_idx = _QUARTER_OPTS.index(ct_end_q)

            if end_q_idx < start_q_idx:
                st.warning("End quarter must be on or after the start quarter.")
            else:
                ct_start_date, ct_end_date = _quarter_date_range(ct_start_q, ct_end_q)
                try:
                    if pm == "trello":
                        transition_data = get_trello_transition_times(
                            st.secrets["trello"]["api_key"],
                            st.secrets["trello"]["token"],
                            st.secrets["trello"]["board_id"],
                            tuple(config.TRELLO_TRANSITIONS),
                            ct_start_date,
                            ct_end_date,
                        )
                    elif pm == "trello_ripe":
                        transition_data = get_trello_transition_times(
                            st.secrets["trello"]["api_key"],
                            st.secrets["trello"]["token"],
                            config.RIPE_TRELLO_BOARD_ID,
                            tuple(config.RIPE_TRELLO_TRANSITIONS),
                            ct_start_date,
                            ct_end_date,
                        )
                    elif pm == "monday":
                        transition_data = get_monday_transition_times(
                            st.secrets["monday"]["api_key"],
                            st.secrets["monday"]["board_id"],
                            tuple(config.MONDAY_TRANSITIONS),
                            ct_start_date,
                            ct_end_date,
                        )
                    elif pm == "jira_tesco":
                        transition_data = get_jira_transition_times(
                            jira_url=st.secrets["jira_tesco"]["url"],
                            email=st.secrets["jira_tesco"]["email"],
                            api_token=st.secrets["jira_tesco"]["api_token"],
                            transitions=tuple(config.JIRA_TESCO_TRANSITIONS),
                            start_date=ct_start_date,
                            end_date=ct_end_date,
                            epic=config.JIRA_TESCO_TARGET_EPIC,
                        )
                    elif pm == "jira_avis":
                        transition_data = get_jira_transition_times(
                            jira_url=st.secrets["jira_avis"]["url"],
                            email=st.secrets["jira_avis"]["email"],
                            api_token=st.secrets["jira_avis"]["api_token"],
                            transitions=tuple(config.JIRA_AVIS_TRANSITIONS),
                            start_date=ct_start_date,
                            end_date=ct_end_date,
                            label_filter=config.JIRA_AVIS_LABEL_FILTER,
                        )
                    else:
                        transition_data = None

                    if transition_data is not None:
                        render_cycle_time(transition_data, client_name)
                except Exception as exc:
                    st.error(f"Could not load cycle time data: {exc}")


if __name__ == "__main__":
    main()
