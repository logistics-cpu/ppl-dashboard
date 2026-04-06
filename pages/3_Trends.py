"""Trends — Sales analytics with style and color tabs."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, date

from core.config import STYLES, COLORS, SIZES
from core.database import init_db, get_weekly_sales, get_latest_inventory
from core.auth import check_password
from core.theme import inject_css, page_header, PLOTLY_LAYOUT, CHART_COLORS

if not check_password():
    st.stop()

inject_css()
page_header("Trends", "Sales analytics over time — by style and color")

init_db()

all_sales = get_weekly_sales()

if not all_sales:
    st.info("No sales data yet. Go to **Data Management** to sync from Shopify.")
    st.stop()

df_all = pd.DataFrame(all_sales)

# ---------------------------------------------------------------------------
# Period filter
# ---------------------------------------------------------------------------
today = date.today()
period_options = ["All", "Last 4 Weeks", "Last 8 Weeks", "Last 12 Weeks"]

# Add month options
months_seen = set()
for _, r in df_all.iterrows():
    try:
        d = datetime.strptime(str(r["week_start"]), "%Y-%m-%d")
        months_seen.add((d.year, d.month))
    except Exception:
        pass
for year, month in sorted(months_seen, reverse=True):
    period_options.append(datetime(year, month, 1).strftime("%B %Y"))

sel_period = st.selectbox("Period", period_options, index=0, key="trend_period")


def filter_by_period(df):
    if sel_period == "All":
        return df
    if sel_period.startswith("Last"):
        num_weeks = int(sel_period.split()[1])
        cutoff = (today - timedelta(weeks=num_weeks)).strftime("%Y-%m-%d")
        return df[df["week_start"] >= cutoff]
    try:
        md = datetime.strptime(sel_period, "%B %Y")
        return df[df["week_start"].apply(
            lambda ws: _in_month(ws, md.year, md.month)
        )]
    except ValueError:
        return df


def _in_month(ws, year, month):
    try:
        d = datetime.strptime(str(ws), "%Y-%m-%d")
    except Exception:
        return False
    end = d + timedelta(days=6)
    return (d.year == year and d.month == month) or (end.year == year and end.month == month)


def format_week(ws):
    try:
        d = datetime.strptime(str(ws), "%Y-%m-%d")
        return f"{d.month}/{d.day}"
    except Exception:
        return str(ws)


# ---------------------------------------------------------------------------
# Helper: render charts for a style tab (by size, per color)
# ---------------------------------------------------------------------------
def render_style_charts(style, df):
    """Show units sold by size (line) + total area chart for a style tab."""
    colors_in_data = [c for c in COLORS if c in df["color"].values]

    for color in colors_in_data:
        color_emoji = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷"}.get(color, "")
        uid = f"{style}_{color}".replace(" ", "_").replace("/", "")
        st.markdown(f"#### {color_emoji} {style} ( {color})")

        cdf = df[df["color"] == color].copy()
        cdf["week_label"] = cdf["week_start"].apply(format_week)

        # Line chart: units by size
        by_size = cdf.groupby(["week_start", "week_label", "size"]).agg(
            units=("units_sold", "sum")
        ).reset_index().sort_values("week_start")

        fig = px.line(
            by_size, x="week_label", y="units", color="size",
            title=f"Units Sold by Size",
            labels={"week_label": "Week", "units": "Units Sold", "size": "Size"},
            category_orders={"size": SIZES},
            color_discrete_sequence=CHART_COLORS,
            markers=True,
        )
        fig.update_layout(**PLOTLY_LAYOUT, hovermode="x unified")
        fig.update_traces(line=dict(width=2.5))
        st.plotly_chart(fig, use_container_width=True, key=f"line_{uid}")

        # Total area chart
        total = cdf.groupby(["week_start", "week_label"]).agg(
            total=("units_sold", "sum")
        ).reset_index().sort_values("week_start")

        fig2 = px.area(
            total, x="week_label", y="total",
            title=f"Total Units Sold per Week",
            labels={"week_label": "Week", "total": "Total Units"},
            color_discrete_sequence=[CHART_COLORS[0]],
        )
        fig2.update_traces(
            fill="tozeroy",
            fillcolor="rgba(30,64,175,0.12)",
            line=dict(width=2.5, color=CHART_COLORS[0]),
        )
        fig2.update_layout(**PLOTLY_LAYOUT)
        st.plotly_chart(fig2, use_container_width=True, key=f"area_{uid}")

        st.markdown("---")


# ---------------------------------------------------------------------------
# Helper: render charts for a color tab (aggregated by style)
# ---------------------------------------------------------------------------
def render_color_charts(color, df):
    """Show aggregated units (all sizes summed) per style for a color tab."""
    color_emoji = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷"}.get(color, "")

    # Stacked bar: all styles on one chart
    by_style_week = df.groupby(["week_start", "style"]).agg(
        units=("units_sold", "sum")
    ).reset_index().sort_values("week_start")
    by_style_week["week_label"] = by_style_week["week_start"].apply(format_week)

    cuid = color.replace(" ", "_")

    if not by_style_week.empty:
        st.markdown(f"#### {color_emoji} {color} — All Styles")

        fig = px.bar(
            by_style_week, x="week_label", y="units", color="style",
            title=f"Total Units Sold by Style (All Sizes Combined)",
            labels={"week_label": "Week", "units": "Units Sold", "style": "Style"},
            category_orders={"style": STYLES},
            color_discrete_sequence=CHART_COLORS,
            barmode="group",
        )
        fig.update_layout(**PLOTLY_LAYOUT, hovermode="x unified")
        fig.update_traces(marker_line_width=0, opacity=0.9)
        st.plotly_chart(fig, use_container_width=True, key=f"cbar_all_{cuid}")

        # Total across all styles
        total = df.groupby(["week_start"]).agg(
            total=("units_sold", "sum")
        ).reset_index().sort_values("week_start")
        total["week_label"] = total["week_start"].apply(format_week)

        fig2 = px.area(
            total, x="week_label", y="total",
            title=f"Total {color} Units Sold per Week (All Styles)",
            labels={"week_label": "Week", "total": "Total Units"},
            color_discrete_sequence=[CHART_COLORS[0]],
        )
        fig2.update_traces(
            fill="tozeroy",
            fillcolor="rgba(30,64,175,0.12)",
            line=dict(width=2.5, color=CHART_COLORS[0]),
        )
        fig2.update_layout(**PLOTLY_LAYOUT)
        st.plotly_chart(fig2, use_container_width=True, key=f"carea_all_{cuid}")

        st.markdown("---")

    # Individual style sections
    for style in STYLES:
        sdf = df[df["style"] == style].copy()
        if sdf.empty:
            continue

        suid = f"{cuid}_{style}".replace("/", "")
        st.markdown(f"#### {color_emoji} {style} ( {color})")

        agg = sdf.groupby(["week_start"]).agg(
            units=("units_sold", "sum")
        ).reset_index().sort_values("week_start")
        agg["week_label"] = agg["week_start"].apply(format_week)

        fig = px.bar(
            agg, x="week_label", y="units",
            title=f"Total Units Sold — {style} {color} (All Sizes)",
            labels={"week_label": "Week", "units": "Units Sold"},
            color_discrete_sequence=[CHART_COLORS[0]],
        )
        fig.update_layout(**PLOTLY_LAYOUT)
        fig.update_traces(marker_line_width=0, opacity=0.9)
        st.plotly_chart(fig, use_container_width=True, key=f"cbar_{suid}")

        st.markdown("---")


# ---------------------------------------------------------------------------
# Tabs: Long | 7/8 | Short | ⚫ Black | 🫒 Olive Green | 🍷 Burgundy
# ---------------------------------------------------------------------------
st.markdown("")
color_emoji_map = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷"}
tab_labels = STYLES + [f"{color_emoji_map.get(c, '')} {c}" for c in COLORS]
all_tabs = st.tabs(tab_labels)

df_filtered = filter_by_period(df_all)

# --- Style tabs ---
for style_idx, style in enumerate(STYLES):
    with all_tabs[style_idx]:
        sdf = df_filtered[df_filtered["style"] == style]
        if sdf.empty:
            st.info(f"No sales data for {style}.")
        else:
            render_style_charts(style, sdf)

# --- Color tabs ---
for color_idx, color in enumerate(COLORS):
    with all_tabs[len(STYLES) + color_idx]:
        cdf = df_filtered[df_filtered["color"] == color]
        if cdf.empty:
            st.info(f"No sales data for {color}.")
        else:
            render_color_charts(color, cdf)
