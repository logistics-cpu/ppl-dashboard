"""Trends — Sales analytics with style and color tabs."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, date

from core.config import STYLES, COLORS, SIZES, ALL_STYLES, ALL_SIZES, get_colors, get_sizes
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


def _current_week_start():
    """Return the start date (Monday) of the current incomplete week."""
    days_since_mon = today.weekday()
    return (today - timedelta(days=days_since_mon)).strftime("%Y-%m-%d")


def _exclude_current_week(df):
    """Remove the current incomplete week from results."""
    cw = _current_week_start()
    return df[df["week_start"] < cw]


def filter_by_period(df):
    df = _exclude_current_week(df)
    if sel_period == "All":
        return df
    if sel_period.startswith("Last"):
        num_weeks = int(sel_period.split()[1])
        # Calculate cutoff from the current week's Monday so we get exactly
        # N completed weeks (not including the current incomplete week).
        current_week_mon = today - timedelta(days=today.weekday())
        cutoff = (current_week_mon - timedelta(weeks=num_weeks)).strftime("%Y-%m-%d")
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
    style_colors = get_colors(style)
    style_sizes = get_sizes(style)
    colors_in_data = [c for c in style_colors if c in df["color"].values]

    for color in colors_in_data:
        color_emoji = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷", "Navy": "🔵", "—": ""}.get(color, "")
        uid = f"{style}_{color}".replace(" ", "_").replace("/", "")
        # For products without colors (e.g., Hydration, Nursing Pillow), don't show "—"
        is_no_color = color == "—"
        # Per-style labels for what "size" actually means
        size_label_map = {
            "Hydration": ("Flavor", "All Flavors"),
            "Nursing Pillow": ("Variant", "All Variants"),
            "Combo": ("Color", "All Colors"),
            "BMP": ("Color", "All Colors"),
        }
        size_axis_label, all_suffix = size_label_map.get(style, ("Size", "All Sizes"))

        # Color map for accessory variants (Combo / BMP) to match actual product colors
        _variant_color_map = {
            "Cream":     "#D4B896",  # warm tan, readable on white
            "Turquoise": "#40E0D0",
            "Peach":     "#FFB997",
            "Ice Grey":  "#B0C4DE",  # light steel blue / cool grey
        }
        use_variant_colors = style in ("Combo", "BMP")
        section_label = style if is_no_color else f"{style} ( {color})"
        title_suffix = f"{style} ({all_suffix})" if is_no_color else f"{style} {color}"
        st.markdown(f"#### {color_emoji} {section_label}".strip())

        cdf = df[df["color"] == color].copy()
        cdf["week_label"] = cdf["week_start"].apply(format_week)

        # Line chart: units by size
        by_size = cdf.groupby(["week_start", "week_label", "size"]).agg(
            units=("units_sold", "sum")
        ).reset_index().sort_values("week_start")

        _line_kwargs = dict(
            data_frame=by_size, x="week_label", y="units", color="size",
            title=f"Units Sold by {size_axis_label} — {title_suffix}",
            labels={"week_label": "Week", "units": "Units Sold", "size": size_axis_label},
            category_orders={"size": style_sizes},
            markers=True,
        )
        if use_variant_colors:
            _line_kwargs["color_discrete_map"] = _variant_color_map
        else:
            _line_kwargs["color_discrete_sequence"] = CHART_COLORS
        fig = px.line(**_line_kwargs)
        fig.update_layout(**PLOTLY_LAYOUT, hovermode="x unified")
        fig.update_traces(line=dict(width=2.5))
        st.plotly_chart(fig, use_container_width=True, key=f"line_{uid}")

        # Total area chart
        total = cdf.groupby(["week_start", "week_label"]).agg(
            total=("units_sold", "sum")
        ).reset_index().sort_values("week_start")

        fig2 = px.bar(
            total, x="week_label", y="total",
            title=f"Total Units Sold per Week — {title_suffix}",
            labels={"week_label": "Week", "total": "Total Units"},
            color_discrete_sequence=[CHART_COLORS[0]],
            text="total",
        )
        fig2.update_traces(marker_line_width=0, opacity=0.9, textposition="outside")
        fig2.update_layout(**PLOTLY_LAYOUT)
        st.plotly_chart(fig2, use_container_width=True, key=f"bar_total_{uid}")

        st.markdown("---")


# ---------------------------------------------------------------------------
# Helper: render charts for a color tab (aggregated by style)
# ---------------------------------------------------------------------------
def render_color_charts(color, df):
    """Show aggregated units (all sizes summed) per style for a color tab."""
    color_emoji = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷", "Navy": "🔵", "—": ""}.get(color, "")

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
            title=f"Total Units Sold by Style — {color} (All Sizes Combined)",
            labels={"week_label": "Week", "units": "Units Sold", "style": "Style"},
            category_orders={"style": ALL_STYLES},
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

        fig2 = px.bar(
            total, x="week_label", y="total",
            title=f"Total {color} Units Sold per Week (All Styles)",
            labels={"week_label": "Week", "total": "Total Units"},
            color_discrete_sequence=[CHART_COLORS[0]],
            text="total",
        )
        fig2.update_traces(marker_line_width=0, opacity=0.9, textposition="outside")
        fig2.update_layout(**PLOTLY_LAYOUT)
        st.plotly_chart(fig2, use_container_width=True, key=f"cbar_total_{cuid}")

        st.markdown("---")

    # Individual style sections
    for style in ALL_STYLES:
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
# Overview: sales by color OR by size (regardless of the other)
# ---------------------------------------------------------------------------
df_overview = filter_by_period(df_all)

# Exclude Nursing Pillow (different color/size dimensions) from this overview
df_overview = df_overview[df_overview["style"].isin(["Long", "7/8", "Short"])]

if not df_overview.empty:
    st.markdown("### Overview — PPL Sales by Color, Size, or Style")
    overview_mode = st.radio(
        "Group by",
        ["By Color", "By Size", "By Style"],
        horizontal=True,
        key="trends_overview_mode",
    )

    df_overview["week_label"] = df_overview["week_start"].apply(format_week)

    if overview_mode == "By Color":
        agg = df_overview.groupby(["week_start", "week_label", "color"]).agg(
            units=("units_sold", "sum")
        ).reset_index().sort_values("week_start")
        fig_ov = px.bar(
            agg, x="week_label", y="units", color="color",
            title="PPL Units Sold by Color (All Styles & Sizes)",
            labels={"week_label": "Week", "units": "Units Sold", "color": "Color"},
            category_orders={"color": ["Black", "Olive Green", "Burgundy", "Navy"]},
            color_discrete_map={"Black": "#1F2937", "Olive Green": "#708238", "Burgundy": "#800020", "Navy": "#1E3A8A"},
            barmode="group",
            text="units",
        )
    elif overview_mode == "By Size":
        size_order = ["XS", "S", "M", "L", "XL", "2XL", "3XL"]
        agg = df_overview.groupby(["week_start", "week_label", "size"]).agg(
            units=("units_sold", "sum")
        ).reset_index().sort_values("week_start")
        fig_ov = px.bar(
            agg, x="week_label", y="units", color="size",
            title="PPL Units Sold by Size (All Styles & Colors)",
            labels={"week_label": "Week", "units": "Units Sold", "size": "Size"},
            category_orders={"size": size_order},
            color_discrete_sequence=CHART_COLORS,
            barmode="group",
            text="units",
        )
    else:  # By Style
        agg = df_overview.groupby(["week_start", "week_label", "style"]).agg(
            units=("units_sold", "sum")
        ).reset_index().sort_values("week_start")
        fig_ov = px.bar(
            agg, x="week_label", y="units", color="style",
            title="PPL Units Sold by Style (All Colors & Sizes)",
            labels={"week_label": "Week", "units": "Units Sold", "style": "Style"},
            category_orders={"style": ["Long", "7/8", "Short"]},
            color_discrete_sequence=CHART_COLORS,
            barmode="group",
            text="units",
        )

    fig_ov.update_traces(marker_line_width=0, opacity=0.9, textposition="outside")
    fig_ov.update_layout(**PLOTLY_LAYOUT, hovermode="x unified")
    st.plotly_chart(fig_ov, use_container_width=True, key=f"overview_{overview_mode}")
    st.markdown("---")


# ---------------------------------------------------------------------------
# Hydration overview — units sold by flavor
# ---------------------------------------------------------------------------
df_hydration = filter_by_period(df_all)
df_hydration = df_hydration[df_hydration["style"] == "Hydration"]

if not df_hydration.empty:
    st.markdown("### Overview — Hydration Sales by Flavor")
    df_hydration["week_label"] = df_hydration["week_start"].apply(format_week)
    flavor_order = ["Variety Pack 15", "Variety Pack 30", "Passionfruit Orange", "Lemonade", "Lemon Lime"]
    agg_hyd = df_hydration.groupby(["week_start", "week_label", "size"]).agg(
        units=("units_sold", "sum")
    ).reset_index().sort_values("week_start")
    fig_hyd = px.bar(
        agg_hyd, x="week_label", y="units", color="size",
        title="Hydration Units Sold by Flavor",
        labels={"week_label": "Week", "units": "Units Sold", "size": "Flavor"},
        category_orders={"size": flavor_order},
        color_discrete_map={
            "Passionfruit Orange": "#F97316",
            "Lemonade": "#EAB308",
            "Lemon Lime": "#84CC16",
            "Variety Pack 15": "#06B6D4",
            "Variety Pack 30": "#8B5CF6",
        },
        barmode="group",
        text="units",
    )
    fig_hyd.update_traces(marker_line_width=0, opacity=0.9, textposition="outside")
    fig_hyd.update_layout(**PLOTLY_LAYOUT, hovermode="x unified")
    st.plotly_chart(fig_hyd, use_container_width=True, key="overview_hydration_flavor")
    st.markdown("---")


# ---------------------------------------------------------------------------
# Tabs: Long | 7/8 | Short | ⚫ Black | 🫒 Olive Green | 🍷 Burgundy | Nursing Pillow
# ---------------------------------------------------------------------------
from core.config import get_other_groups
st.markdown("")
color_emoji_map = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷", "Navy": "🔵", "—": ""}
PPL_STYLES = [s for s in ALL_STYLES if s in ("Long", "7/8", "Short")]
OTHER_GROUPS = get_other_groups()  # [(group_name, [styles])]
all_colors_seen = []
for _s in PPL_STYLES:
    for _c in get_colors(_s):
        if _c not in all_colors_seen and _c != "—":
            all_colors_seen.append(_c)
tab_labels = (
    PPL_STYLES
    + [f"{color_emoji_map.get(c, '')} {c}" for c in all_colors_seen]
    + [g[0] for g in OTHER_GROUPS]
)
all_tabs = st.tabs(tab_labels)

df_filtered = filter_by_period(df_all)

# --- PPL Style tabs ---
for style_idx, style in enumerate(PPL_STYLES):
    with all_tabs[style_idx]:
        sdf = df_filtered[df_filtered["style"] == style]
        if sdf.empty:
            st.info(f"No sales data for {style}.")
        else:
            render_style_charts(style, sdf)

# --- Color tabs ---
for color_idx, color in enumerate(all_colors_seen):
    with all_tabs[len(PPL_STYLES) + color_idx]:
        cdf = df_filtered[df_filtered["color"] == color]
        if cdf.empty:
            st.info(f"No sales data for {color}.")
        else:
            render_color_charts(color, cdf)

# --- Other product group tabs (e.g. Pillow → Nursing Pillow/Combo/BMP) ---
for gi, (gname, gstyles) in enumerate(OTHER_GROUPS):
    parent_idx = len(PPL_STYLES) + len(all_colors_seen) + gi
    with all_tabs[parent_idx]:
        if len(gstyles) > 1:
            sub_tabs = st.tabs(gstyles)
        else:
            sub_tabs = [st.container()]
        for si, style in enumerate(gstyles):
            with sub_tabs[si]:
                sdf = df_filtered[df_filtered["style"] == style]
                if sdf.empty:
                    st.info(f"No sales data for {style}.")
                else:
                    render_style_charts(style, sdf)
