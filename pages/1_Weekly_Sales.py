"""Weekly Sales & Inventory Overview — spreadsheet-style unified view."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from datetime import datetime, timedelta, date

from core.config import STYLES, COLORS, SIZES, ALL_STYLES, ALL_SIZES, get_colors, get_sizes
from core.database import (
    init_db, get_weekly_sales, get_latest_inventory, get_setting,
    get_production_arrivals, get_warehouse_transfers,
)
from core.calculations import (
    weekly_growth_rate, daily_demand, stock_life_days, stockout_date,
)
from core.theme import inject_css, page_header, PRIMARY, TEXT_MUTED
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header("Weekly Sales", "Sales data by style — units sold, demand, stock levels")

SIZE_ORDER = {s: i for i, s in enumerate(ALL_SIZES)}


def format_week_range(week_start_str, week_end_str):
    """Format week as '3/17-3/23' like the spreadsheet."""
    try:
        if isinstance(week_start_str, str):
            ws = datetime.strptime(week_start_str, "%Y-%m-%d")
        else:
            ws = week_start_str
        if isinstance(week_end_str, str):
            we = datetime.strptime(week_end_str, "%Y-%m-%d")
        else:
            we = week_end_str
        return f"{ws.month}/{ws.day}-{we.month}/{we.day}"
    except Exception:
        return str(week_start_str)


# ---------------------------------------------------------------------------
# Load all data
# ---------------------------------------------------------------------------
all_sales = get_weekly_sales()
all_inv = get_latest_inventory()

threshold_days = int(get_setting("stockout_threshold_days") or 14)
warning_days = int(get_setting("warning_threshold_days") or 30)

if not all_sales and not all_inv:
    st.info("No data yet. Go to **Data Management** to sync from Shopify or upload ERP data.")
    st.stop()

# Build inventory lookup: aggregate across warehouses per (style, color, size)
inv_lookup = {}
if all_inv:
    for r in all_inv:
        key = (r["style"], r["color"], r["size"])
        if key not in inv_lookup:
            inv_lookup[key] = {"available_qty": 0, "total_stock": 0, "sales_7d": 0, "in_transit": 0}
        inv_lookup[key]["available_qty"] += r["available_qty"] or 0
        inv_lookup[key]["total_stock"] += r["stock_qty"] or 0
        inv_lookup[key]["sales_7d"] += r["sales_7d"] or 0
        inv_lookup[key]["in_transit"] += r["in_transit_qty"] or 0

# Build inbound lookup from production arrivals
arrivals = get_production_arrivals() if hasattr(get_production_arrivals, '__call__') else []
inbound_lookup = {}
if arrivals:
    for a in arrivals:
        key = (a["style"], a["color"], a["size"])
        inbound_lookup[key] = inbound_lookup.get(key, 0) + a["qty"]

# Build InTransit US and US Inbound lookups from warehouse transfers (Data Management)
transfers = get_warehouse_transfers() if hasattr(get_warehouse_transfers, '__call__') else []
in_transit_us_lookup = {}
us_inbound_lookup = {}
if transfers:
    for t in transfers:
        key = (t["style"], t["color"], t["size"])
        to_wh = t.get("to_warehouse", "")
        # InTransit US = transfers going TO US warehouses (in transit, not yet arrived)
        if "US" in to_wh:
            in_transit_us_lookup[key] = in_transit_us_lookup.get(key, 0) + t["qty"]
        # US Inbound = transfers that have arrived at US warehouses
        us_inbound_lookup[key] = us_inbound_lookup.get(key, 0) + t["qty"]

# ---------------------------------------------------------------------------
# Filters: Color + Week period
# ---------------------------------------------------------------------------
st.markdown(
    '<p style="font-size:0.85rem;font-weight:600;color:#475569;'
    'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">'
    "Filters</p>",
    unsafe_allow_html=True,
)

f1, f2 = st.columns(2, gap="medium")

with f1:
    sel_color = st.selectbox("Color", ["All"] + COLORS, help="Filter by color across all tabs")
    color_filter = sel_color if sel_color != "All" else None

with f2:
    # Build week period options
    today = date.today()
    period_options = [
        "All",
        "This Week",
        "Previous Week",
        "Last 4 Weeks",
        "Last 6 Weeks",
        "Last 8 Weeks",
    ]

    # Add month options dynamically from the data
    if all_sales:
        months_seen = set()
        for r in all_sales:
            try:
                d = datetime.strptime(r["week_start"], "%Y-%m-%d") if isinstance(r["week_start"], str) else r["week_start"]
                months_seen.add((d.year, d.month))
            except Exception:
                pass
        for year, month in sorted(months_seen, reverse=True):
            month_name = datetime(year, month, 1).strftime("%B %Y")
            period_options.append(month_name)

    sel_period = st.selectbox("Period", period_options, index=3, help="Filter weeks to display")


def _current_week_start():
    """Return the start date (Monday) of the current incomplete week."""
    days_since_mon = today.weekday()
    return today - timedelta(days=days_since_mon)


def _exclude_current_week(sales_list):
    """Remove the current incomplete week from results."""
    cw = _current_week_start().strftime("%Y-%m-%d")
    return [r for r in sales_list if str(r["week_start"]) < cw]


def filter_sales_by_period(sales_list):
    """Filter a list of sales rows based on the selected period."""
    if sel_period == "All":
        return _exclude_current_week(sales_list)

    if sel_period == "This Week":
        # Current week (Tue-Mon) — only period that shows incomplete week
        cw = _current_week_start().strftime("%Y-%m-%d")
        return [r for r in sales_list if str(r["week_start"]) == cw]

    if sel_period == "Previous Week":
        # Most recent completed week (Tue-Mon)
        prev_week_start = (_current_week_start() - timedelta(days=7)).strftime("%Y-%m-%d")
        return [r for r in sales_list if str(r["week_start"]) == prev_week_start]

    if sel_period.startswith("Last"):
        num_weeks = int(sel_period.split()[1])
        # Count back from the current week's Monday so we get exactly
        # N completed weeks (not including the current incomplete week).
        cutoff = _current_week_start() - timedelta(weeks=num_weeks)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        return [r for r in _exclude_current_week(sales_list) if str(r["week_start"]) >= cutoff_str]

    # Month filter (e.g. "March 2026")
    try:
        month_date = datetime.strptime(sel_period, "%B %Y")
        year, month = month_date.year, month_date.month
        return [
            r for r in _exclude_current_week(sales_list)
            if _week_in_month(r["week_start"], year, month)
        ]
    except ValueError:
        return _exclude_current_week(sales_list)


def _week_in_month(week_start, year, month):
    """Check if a week overlaps with a given month."""
    try:
        d = datetime.strptime(str(week_start), "%Y-%m-%d")
    except Exception:
        return False
    end = d + timedelta(days=6)
    return (d.year == year and d.month == month) or (end.year == year and end.month == month)


# ---------------------------------------------------------------------------
# Helper: build and render a sales table for a given style + color combo
# ---------------------------------------------------------------------------
_COLOR_EMOJI = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷", "—": ""}


def render_sales_table(style, color, color_sales, heading):
    """Build and display the sales table for one style+color combo."""
    color_emoji = _COLOR_EMOJI.get(color, "")
    st.markdown(f"### {color_emoji} {heading}")

    if not color_sales:
        st.caption("No sales data for this period.")
        st.markdown("---")
        return

    sales_df = pd.DataFrame(color_sales)
    sales_df["size_order"] = sales_df["size"].map(SIZE_ORDER)
    sales_df = sales_df.sort_values(["size_order", "week_start"]).reset_index(drop=True)

    table_rows = []
    for size in get_sizes(style):
        size_data = sales_df[sales_df["size"] == size].sort_values("week_start")
        if size_data.empty:
            continue

        inv = inv_lookup.get((style, color, size), {})
        avail_qty = inv.get("available_qty", 0)
        in_transit_us = in_transit_us_lookup.get((style, color, size), 0)

        raw_rows = []
        prev_units = None
        weeks = list(size_data.iterrows())

        for week_idx, (_, row) in enumerate(weeks):
            units = row["units_sold"]
            growth = weekly_growth_rate(units, prev_units)
            demand_val = daily_demand(units)
            week_label = format_week_range(row["week_start"], row["week_end"])
            is_last_week = (week_idx == len(weeks) - 1)

            db_opening = row.get("opening_stock")
            db_closing = row.get("closing_stock")
            has_db_stock = db_opening is not None and not (isinstance(db_opening, float) and pd.isna(db_opening))

            opening = None
            closing = None

            if has_db_stock:
                opening = int(db_opening) if db_opening else 0
                closing = int(db_closing) if db_closing else 0
            elif is_last_week and inv:
                opening = avail_qty + units
                closing = avail_qty

            raw_rows.append({
                "units": units, "growth": growth, "demand": demand_val,
                "week_label": week_label, "opening": opening, "closing": closing,
                "is_last_week": is_last_week,
            })
            prev_units = units

        # Backfill stock for weeks missing data
        for i in range(len(raw_rows) - 2, -1, -1):
            if raw_rows[i]["opening"] is None and raw_rows[i + 1]["opening"] is not None:
                next_opening = raw_rows[i + 1]["opening"]
                raw_rows[i]["closing"] = next_opening
                raw_rows[i]["opening"] = next_opening + raw_rows[i]["units"]

        # Build display rows
        for r in raw_rows:
            closing = r["closing"]
            demand_val = r["demand"]
            life = None
            alert = ""
            if closing is not None and closing == 0:
                life = 0
                alert = "🔴 Stockout"
            elif closing is not None and closing > 0 and demand_val > 0:
                life = stock_life_days(closing, demand_val)
                if life <= threshold_days:
                    alert = "🔴 Critical"
                elif life <= warning_days:
                    alert = "🟡 Warning"
                else:
                    alert = "🟢 OK"

            table_rows.append({
                "Size": size,
                "Week Date": r["week_label"],
                "Units Sold": str(r["units"]),
                "Daily Demand": str(round(demand_val, 1)),
                "Growth": f"{r['growth']:.1%}" if r["growth"] is not None else "—",
                "Current Stock": str(closing) if closing is not None else "",
                "Stock Life (Days)": str(round(life)) if life is not None else "",
                "Stockout?": alert,
            })

    if table_rows:
        tdf = pd.DataFrame(table_rows)

        size_group_map = {}
        current_group = 0
        prev_size = None
        for idx, row_data in tdf.iterrows():
            if row_data["Size"] != prev_size:
                if prev_size is not None:
                    current_group += 1
                prev_size = row_data["Size"]
            size_group_map[idx] = current_group

        def highlight_display_row(row):
            idx = row.name
            group = size_group_map.get(idx, 0)
            is_odd_group = group % 2 == 1
            bg = "#EFF6FF" if is_odd_group else "#FFFFFF"
            # Consistent alternating backgrounds — alerts shown via text only
            return [f"background-color: {bg}"] * len(row)

        styled = tdf.style.apply(highlight_display_row, axis=1)

        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            height=min(800, 40 + len(tdf) * 35),
            column_config={
                "Size": st.column_config.TextColumn("Size", width="small"),
                "Week Date": st.column_config.TextColumn("Week Date", width="small"),
                "Units Sold": st.column_config.TextColumn("Units Sold", width="small"),
                "Daily Demand": st.column_config.TextColumn("Daily Demand", width="small"),
                "Growth": st.column_config.TextColumn("Growth", width="small"),
                "Current Stock": st.column_config.TextColumn("Current Stock", width="small"),
                "Stock Life (Days)": st.column_config.TextColumn("Stock Life (Days)", width="small"),
                "Stockout?": st.column_config.TextColumn("Stockout?", width="small"),
            },
        )

        criticals = [r for r in table_rows if "Critical" in str(r.get("Stockout?", "")) or "Stockout" in str(r.get("Stockout?", ""))]
        warnings_list = [r for r in table_rows if "Warning" in str(r.get("Stockout?", ""))]
        if criticals or warnings_list:
            alert_parts = []
            if criticals:
                sizes = sorted(set(r["Size"] for r in criticals), key=lambda s: SIZE_ORDER.get(s, 99))
                alert_parts.append(f'🔴 Critical: {", ".join(sizes)}')
            if warnings_list:
                sizes = sorted(set(r["Size"] for r in warnings_list), key=lambda s: SIZE_ORDER.get(s, 99))
                alert_parts.append(f'🟡 Warning: {", ".join(sizes)}')
            st.caption(" · ".join(alert_parts))

    st.markdown("---")


# ---------------------------------------------------------------------------
# Tabs: Long | 7/8 | Short | ⚫ Black | 🫒 Olive Green | 🍷 Burgundy | Nursing Pillow
# ---------------------------------------------------------------------------
st.markdown("")
color_emoji_map = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷"}
PPL_STYLES = [s for s in ALL_STYLES if s in ("Long", "7/8", "Short")]
OTHER_STYLES = [s for s in ALL_STYLES if s not in PPL_STYLES]
tab_labels = PPL_STYLES + [f"{color_emoji_map.get(c, '')} {c}" for c in COLORS] + OTHER_STYLES
all_tabs = st.tabs(tab_labels)

# Tab index layout: [PPL styles] [colors] [other styles]
_style_tab_order = PPL_STYLES + OTHER_STYLES

# --- Style tabs: grouped by color (PPL styles at front, others at back) ---
for style in _style_tab_order:
    if style in PPL_STYLES:
        tab_idx = PPL_STYLES.index(style)
    else:
        tab_idx = len(PPL_STYLES) + len(COLORS) + OTHER_STYLES.index(style)
    with all_tabs[tab_idx]:
        style_colors = get_colors(style)
        style_sales = [r for r in all_sales if r["style"] == style]

        # Color filter only applies when the style actually has that color
        apply_color_filter = color_filter and color_filter in style_colors
        if apply_color_filter:
            style_sales = [r for r in style_sales if r["color"] == color_filter]

        style_sales = filter_sales_by_period(style_sales)

        colors_to_show = [color_filter] if apply_color_filter else style_colors

        # Use container instead of tabs when there is only one color
        if len(colors_to_show) == 1:
            color_containers = [st.container()]
        else:
            color_containers = st.tabs(colors_to_show)

        for ci, color in enumerate(colors_to_show):
            with color_containers[ci]:
                color_sales = [r for r in style_sales if r["color"] == color]
                if color == "—":
                    heading = style
                else:
                    heading = f"{style} ( {color})"
                render_sales_table(style, color, color_sales, heading)

# --- Color tabs (Black, Olive Green, Burgundy): aggregated by style ---
for color_idx, color in enumerate(COLORS):
    with all_tabs[len(PPL_STYLES) + color_idx]:
        color_sales_all = [r for r in all_sales if r["color"] == color]
        color_sales_all = filter_sales_by_period(color_sales_all)

        for style in ALL_STYLES:
            # Skip styles that don't have this color
            if color not in get_colors(style):
                continue

            style_color_sales = [r for r in color_sales_all if r["style"] == style]
            c_emoji = color_emoji_map.get(color, "")
            st.markdown(f"### {c_emoji} {style} ( {color})")

            if not style_color_sales:
                st.caption("No sales data for this period.")
                st.markdown("---")
                continue

            # Aggregate units sold across all sizes per week
            week_totals = {}
            for r in style_color_sales:
                ws = r["week_start"]
                we = r["week_end"]
                if ws not in week_totals:
                    week_totals[ws] = {"week_end": we, "units": 0}
                week_totals[ws]["units"] += r["units_sold"]

            # Build rows sorted by week
            agg_rows = []
            prev_units = None
            for ws in sorted(week_totals.keys()):
                wdata = week_totals[ws]
                units = wdata["units"]
                growth = weekly_growth_rate(units, prev_units)
                demand_val = daily_demand(units)
                week_label = format_week_range(ws, wdata["week_end"])

                agg_rows.append({
                    "Week Date": week_label,
                    "Units Sold": units,
                    "Daily Demand": round(demand_val, 1),
                    "Growth": f"{growth:.1%}" if growth is not None else "—",
                })
                prev_units = units

            if agg_rows:
                agg_df = pd.DataFrame(agg_rows)
                st.dataframe(
                    agg_df,
                    use_container_width=True,
                    hide_index=True,
                    height=min(400, 40 + len(agg_df) * 35),
                    column_config={
                        "Week Date": st.column_config.TextColumn("Week Date", width="small"),
                        "Units Sold": st.column_config.NumberColumn("Units Sold", format="%d"),
                        "Daily Demand": st.column_config.NumberColumn("Daily Demand", format="%.1f"),
                        "Growth": st.column_config.TextColumn("Growth", width="small"),
                    },
                )

            st.markdown("---")
