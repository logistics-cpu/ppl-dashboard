"""Forecast — project future weeks with editable inbound quantities."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from datetime import datetime, timedelta, date

from core.config import STYLES, COLORS, SIZES
from core.database import (
    init_db, get_weekly_sales, get_latest_inventory, get_setting,
    get_production_arrivals, get_warehouse_transfers,
)
from core.calculations import stock_life_days, stockout_date
from core.theme import inject_css, page_header
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header("Forecast", "Edit China/US Inbound quantities — stock projections recalculate automatically")

SIZE_ORDER = {s: i for i, s in enumerate(SIZES)}


def format_week_range(ws, we):
    return f"{ws.month}/{ws.day}-{we.month}/{we.day}"


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
all_sales = get_weekly_sales()
all_inv = get_latest_inventory()

if not all_sales and not all_inv:
    st.info("No data yet. Go to **Data Management** to sync sales or upload ERP data first.")
    st.stop()

# Build inventory lookup
inv_lookup = {}
if all_inv:
    for r in all_inv:
        key = (r["style"], r["color"], r["size"])
        if key not in inv_lookup:
            inv_lookup[key] = {"available_qty": 0, "sales_7d": 0, "in_transit": 0}
        inv_lookup[key]["available_qty"] += r["available_qty"] or 0
        inv_lookup[key]["sales_7d"] += r["sales_7d"] or 0
        inv_lookup[key]["in_transit"] += r["in_transit_qty"] or 0

# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
st.markdown(
    '<p style="font-size:0.85rem;font-weight:600;color:#475569;'
    'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">'
    "Forecast Settings</p>",
    unsafe_allow_html=True,
)

# Persist settings across tab switches — initialise session_state defaults once
if "fc_weeks" not in st.session_state:
    st.session_state["fc_weeks"] = 8
if "fc_growth_mode" not in st.session_state:
    st.session_state["fc_growth_mode"] = "Auto (weighted average)"
if "fc_custom_growth" not in st.session_state:
    st.session_state["fc_custom_growth"] = 5.0
if "fc_demand_basis" not in st.session_state:
    st.session_state["fc_demand_basis"] = "Last week"
if "fc_color" not in st.session_state:
    st.session_state["fc_color"] = "All"

c1, c2, c3, c4 = st.columns(4, gap="medium")

with c1:
    forecast_weeks = st.slider(
        "Weeks to forecast", 4, 20,
        help="Number of future weeks to project",
        key="fc_weeks",
    )

with c2:
    growth_mode = st.selectbox(
        "Growth assumption",
        ["Auto (weighted average)", "Custom %", "Flat (0%)"],
        help="How to project future demand",
        key="fc_growth_mode",
    )

with c3:
    if growth_mode == "Custom %":
        custom_growth = st.number_input(
            "Weekly growth %", -50.0, 200.0, step=1.0,
            key="fc_custom_growth",
        ) / 100
    else:
        custom_growth = 0.0
        st.empty()

with c4:
    demand_basis = st.selectbox(
        "Demand basis",
        ["Last 4 weeks avg", "Last 2 weeks avg", "Last week", "ERP 7-day avg"],
        help="Which period to use as base demand",
        key="fc_demand_basis",
    )

st.markdown("")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _current_week_start_str():
    """Return the current week's Tuesday as YYYY-MM-DD string."""
    today_ = date.today()
    days_since_tue = (today_.weekday() - 1) % 7
    return (today_ - timedelta(days=days_since_tue)).strftime("%Y-%m-%d")


def _completed_sales(style, color, size):
    """Return sales rows excluding the current incomplete week, sorted by week_start."""
    cw = _current_week_start_str()
    sales = [
        r for r in all_sales
        if r["style"] == style and r["color"] == color and r["size"] == size
        and str(r["week_start"]) < cw
    ]
    sales.sort(key=lambda r: str(r["week_start"]))
    return sales


def get_base_demand(style, color, size):
    size_sales = _completed_sales(style, color, size)

    if not size_sales:
        inv = inv_lookup.get((style, color, size), {})
        return inv.get("sales_7d", 0) / 7 if inv.get("sales_7d", 0) > 0 else 0

    if demand_basis == "ERP 7-day avg":
        inv = inv_lookup.get((style, color, size), {})
        return inv.get("sales_7d", 0) / 7 if inv.get("sales_7d", 0) > 0 else 0

    if demand_basis == "Last week":
        return size_sales[-1]["units_sold"] / 7

    n = 4 if "4" in demand_basis else 2
    recent = size_sales[-n:]
    total = sum(r["units_sold"] for r in recent)
    return total / (len(recent) * 7)


def _weighted_avg_3(g1, g2, g3):
    """Weighted average: 0.2 oldest, 0.3 middle, 0.5 most recent."""
    return g1 * 0.2 + g2 * 0.3 + g3 * 0.5


def get_auto_growth_rates(style, color, size, num_weeks):
    """Return a list of rolling weighted-average growth rates for each forecast week.

    Uses last 3 historical WoW growth rates as seed, then each forecast week's
    rate feeds back into the weighted average for the next week — so the rate
    naturally evolves (and typically decays toward 0) over time.
    """
    size_sales = _completed_sales(style, color, size)

    if len(size_sales) < 2:
        return [0.0] * num_weeks

    # Historical WoW growth rates
    hist_growths = []
    for i in range(1, len(size_sales)):
        prev = size_sales[i - 1]["units_sold"]
        curr = size_sales[i]["units_sold"]
        if prev > 0:
            hist_growths.append((curr - prev) / prev)
        else:
            hist_growths.append(0.0)

    if not hist_growths:
        return [0.0] * num_weeks

    # Seed: take last 3 growth rates (or fewer)
    seed = hist_growths[-3:]

    # Generate rolling weighted-average growth for each forecast week
    rates = []
    window = list(seed)  # mutable rolling window

    for _ in range(num_weeks):
        if len(window) >= 3:
            rate = _weighted_avg_3(window[-3], window[-2], window[-1])
        elif len(window) == 2:
            rate = window[-2] * 0.3 + window[-1] * 0.7
        else:
            rate = window[-1]
        rates.append(rate)
        window.append(rate)  # this week's rate feeds into next calculation

    return rates


def get_last_completed_week_end(style, color, size):
    """Return the end date of the last *completed* week with sales data.
    The current incomplete week is excluded so forecast starts from it."""
    today_ = date.today()
    days_since_tue = (today_.weekday() - 1) % 7
    current_week_start = today_ - timedelta(days=days_since_tue)
    current_week_start_str = current_week_start.strftime("%Y-%m-%d")

    size_sales = [
        r for r in all_sales
        if r["style"] == style and r["color"] == color and r["size"] == size
        and str(r["week_start"]) < current_week_start_str  # exclude current week
    ]
    if not size_sales:
        # No completed weeks — forecast starts from current week
        return current_week_start - timedelta(days=1)
    size_sales.sort(key=lambda r: str(r["week_start"]))
    last = size_sales[-1]
    we = last["week_end"]
    if isinstance(we, str):
        we = datetime.strptime(we, "%Y-%m-%d").date()
    return we


def get_current_stock(style, color, size):
    size_sales = [
        r for r in all_sales
        if r["style"] == style and r["color"] == color and r["size"] == size
    ]
    size_sales.sort(key=lambda r: str(r["week_start"]))
    if size_sales:
        last = size_sales[-1]
        cs = last.get("closing_stock")
        if cs is not None and not (isinstance(cs, float) and pd.isna(cs)):
            return int(cs)

    inv = inv_lookup.get((style, color, size), {})
    return inv.get("available_qty", 0)


def build_initial_forecast(style, color, size):
    """Generate initial forecast rows with editable inbound columns."""
    base_demand = get_base_demand(style, color, size)
    if base_demand <= 0:
        return None

    if growth_mode == "Auto (weighted average)":
        growth_rates = get_auto_growth_rates(style, color, size, forecast_weeks)
    elif growth_mode == "Custom %":
        growth_rates = [custom_growth] * forecast_weeks
    else:
        growth_rates = [0.0] * forecast_weeks

    current_stock = get_current_stock(style, color, size)
    last_week_end = get_last_completed_week_end(style, color, size)

    rows = []
    demand = base_demand

    for w in range(1, forecast_weeks + 1):
        week_start = last_week_end + timedelta(days=1) + timedelta(weeks=w - 1)
        week_end = week_start + timedelta(days=6)

        wg = growth_rates[w - 1]
        if w > 1:
            demand = demand * (1 + wg)

        rows.append({
            "Size": size,
            "Week Date": format_week_range(week_start, week_end),
            "Proj. Daily Demand": round(demand, 1),
            "Growth": f"{wg:.1%}" if wg != 0 else "—",
            "Opening Stock": 0,  # calculated after
            "Closing Stock": 0,  # calculated after
            "China Inbound": 0,
            "US Inbound": 0,
            "Stock Life": 0,
            "Alert": "",
            "_demand": demand,  # hidden, for recalc
        })

    return rows, current_stock


def recalculate_forecast(df, current_stocks):
    """Recalculate Opening/Closing Stock, Stock Life, Alert based on inbound edits."""
    result_rows = []
    stock_by_size = dict(current_stocks)  # {size: current_stock}

    threshold = int(get_setting("stockout_threshold_days") or 14)
    warning = int(get_setting("warning_threshold_days") or 30)

    for _, row in df.iterrows():
        size = row["Size"]
        demand_daily = row["Proj. Daily Demand"]
        weekly_demand = round(demand_daily * 7)
        china_in = int(row["China Inbound"]) if row["China Inbound"] else 0
        us_in = int(row["US Inbound"]) if row["US Inbound"] else 0
        total_inbound = china_in + us_in

        opening = stock_by_size.get(size, 0)
        closing = max(0, opening - weekly_demand + total_inbound)

        life = stock_life_days(closing, demand_daily) if demand_daily > 0 else None

        alert = ""
        if closing <= 0:
            alert = "🔴 STOCKOUT"
        elif life is not None and life <= threshold:
            alert = "🔴 Critical"
        elif life is not None and life <= warning:
            alert = "🟡 Warning"
        elif life is not None:
            alert = "🟢 OK"

        result_rows.append({
            "Size": size,
            "Week Date": row["Week Date"],
            "Proj. Daily Demand": demand_daily,
            "Growth": row["Growth"],
            "Opening Stock": opening,
            "Closing Stock": closing,
            "China Inbound": china_in,
            "US Inbound": us_in,
            "Stock Life": round(life) if life is not None else 0,
            "Alert": alert,
        })

        stock_by_size[size] = closing

    return pd.DataFrame(result_rows)


# ---------------------------------------------------------------------------
# Style tabs
# ---------------------------------------------------------------------------
style_tabs = st.tabs(STYLES)

_COLOR_EMOJI = {"Black": "⚫", "Olive Green": "🫒", "Burgundy": "🍷"}

for style_idx, style in enumerate(STYLES):
    with style_tabs[style_idx]:
        colors_to_show = COLORS

        # Nested color tabs so you always see which color is active
        color_tab_labels = [f"{_COLOR_EMOJI.get(c, '')} {c}" for c in colors_to_show]
        color_tabs = st.tabs(color_tab_labels) if len(colors_to_show) > 1 else [st.container()]

        for color_idx, color in enumerate(colors_to_show):
          with color_tabs[color_idx]:

            # Build initial forecast per size
            current_stocks = {}
            size_forecasts = {}  # {size: rows_list}

            for size in SIZES:
                result = build_initial_forecast(style, color, size)
                if result is None:
                    continue
                rows, current_stock = result
                current_stocks[size] = current_stock
                size_forecasts[size] = rows

            if not size_forecasts:
                st.caption("No data to forecast for this color.")
                st.markdown("---")
                continue

            _col_config = {
                "Week Date": st.column_config.TextColumn("Week Date", width="small"),
                "Proj. Daily Demand": st.column_config.NumberColumn("Proj. Daily Demand", format="%.1f"),
                "Growth": st.column_config.TextColumn("Growth", width="small"),
                "Opening Stock": st.column_config.NumberColumn("Opening Stock", format="%d"),
                "China Inbound": st.column_config.NumberColumn(
                    "China Inbound", format="%d", min_value=0, max_value=100000,
                    help="Enter expected inbound qty to China warehouse",
                ),
                "US Inbound": st.column_config.NumberColumn(
                    "US Inbound", format="%d", min_value=0, max_value=100000,
                    help="Enter expected inbound qty to US warehouse",
                ),
                "Closing Stock": st.column_config.NumberColumn("Closing Stock", format="%d"),
                "Stock Life": st.column_config.NumberColumn("Stock Life", format="%d"),
                "Alert": st.column_config.TextColumn("Alert", width="small"),
            }
            _disabled_cols = [
                "Week Date", "Proj. Daily Demand",
                "Growth", "Opening Stock", "Closing Stock", "Stock Life", "Alert",
            ]

            # Persistent inbound store — survives tab switches
            if "fc_inbound" not in st.session_state:
                st.session_state["fc_inbound"] = {}  # {editor_key: {row_idx: {col: val}}}

            def _save_inbound(editor_key):
                """Callback: capture inbound edits into persistent store."""
                edits = st.session_state.get(editor_key, {})
                edited_rows = edits.get("edited_rows", {}) if isinstance(edits, dict) else {}
                if not edited_rows:
                    return
                store = st.session_state["fc_inbound"]
                if editor_key not in store:
                    store[editor_key] = {}
                for row_idx_str, changes in edited_rows.items():
                    row_idx = int(row_idx_str)
                    if row_idx not in store[editor_key]:
                        store[editor_key][row_idx] = {}
                    for col, val in changes.items():
                        if col in ("China Inbound", "US Inbound"):
                            store[editor_key][row_idx][col] = val

            stockout_sizes_all = []

            for size in SIZES:
                if size not in size_forecasts:
                    continue

                rows = size_forecasts[size]
                editor_key = f"fc_{style}_{color}_{size}"

                # Build base df for this size
                base_df = pd.DataFrame(rows).drop(columns=["_demand"], errors="ignore")

                # Apply persisted inbound values
                saved = st.session_state.get("fc_inbound", {}).get(editor_key, {})
                for row_idx, cols in saved.items():
                    if row_idx < len(base_df):
                        for col, val in cols.items():
                            base_df.at[row_idx, col] = val

                # Recalculate with inbound values applied
                calc_df = recalculate_forecast(base_df, {size: current_stocks.get(size, 0)})
                calc_df["China Inbound"] = base_df["China Inbound"].values
                calc_df["US Inbound"] = base_df["US Inbound"].values
                calc_df = calc_df.drop(columns=["Size"], errors="ignore")

                # Size group header — includes style+color so it's always visible
                color_emoji = _COLOR_EMOJI.get(color, "")
                stock_now = current_stocks.get(size, 0)
                st.markdown(
                    f'<div style="background:#F1F5F9;padding:6px 12px;'
                    f'border-left:3px solid #1E40AF;margin-top:12px;margin-bottom:4px;'
                    f'font-size:0.9rem;">'
                    f'<span style="font-weight:700;">{color_emoji} {style} ( {color})</span>'
                    f' &nbsp;·&nbsp; '
                    f'<span style="font-weight:600;">{size}</span>'
                    f' &nbsp;<span style="font-weight:400;color:#64748B;">'
                    f'Current Stock: {stock_now}</span></div>',
                    unsafe_allow_html=True,
                )

                st.data_editor(
                    calc_df,
                    use_container_width=True,
                    hide_index=True,
                    height=min(400, 45 + len(calc_df) * 35),
                    key=editor_key,
                    disabled=_disabled_cols,
                    column_config=_col_config,
                    on_change=_save_inbound,
                    args=(editor_key,),
                )

                # Track stockout sizes
                if "Alert" in calc_df.columns:
                    so = calc_df[calc_df["Alert"].str.contains("STOCKOUT", na=False)]
                    if len(so) > 0:
                        stockout_sizes_all.append(size)

            # Stockout summary across all sizes
            if stockout_sizes_all:
                sizes_str = ", ".join(sorted(set(stockout_sizes_all), key=lambda s: SIZE_ORDER.get(s, 99)))
                st.error(f"⚠️ Projected stockout for: **{sizes_str}** — consider increasing inbound quantities")

            st.markdown("---")
