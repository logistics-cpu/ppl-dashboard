"""Weekly Report — auto-generated summary for Slack updates."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from datetime import datetime, timedelta, date

from core.config import STYLES, COLORS, SIZES, ALL_STYLES, PRODUCT_GROUPS, get_colors, get_sizes
from core.database import init_db, get_weekly_sales, get_latest_inventory, get_setting
from core.calculations import weekly_growth_rate, daily_demand, stock_life_days
from core.theme import inject_css, page_header, PRIMARY, TEXT_MUTED
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header("Weekly Report", "Auto-generated summary for team updates")

# ---------------------------------------------------------------------------
# Helper: current week start (Tuesday)
# ---------------------------------------------------------------------------
today = date.today()
days_since_tue = (today.weekday() - 1) % 7
current_week_start = today - timedelta(days=days_since_tue)

# ---------------------------------------------------------------------------
# Load data & exclude current incomplete week
# ---------------------------------------------------------------------------
all_sales = get_weekly_sales()
all_inv = get_latest_inventory()

# Filter out current incomplete week
completed_sales = [r for r in all_sales if str(r["week_start"]) < str(current_week_start)]

# Get sorted unique weeks
weeks_set = sorted(set(str(r["week_start"]) for r in completed_sales))

if len(weeks_set) < 1:
    st.warning("Not enough completed weeks to generate a report.")
    st.stop()

# ---------------------------------------------------------------------------
# Week selector
# ---------------------------------------------------------------------------
st.markdown(
    '<p style="font-size:0.85rem;font-weight:600;color:#475569;'
    'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">'
    "Report Settings</p>",
    unsafe_allow_html=True,
)


def _format_week_label(ws_str):
    """Format '2026-03-31' as 'Week 3/31-4/6'."""
    try:
        ws = datetime.strptime(ws_str, "%Y-%m-%d")
        we = ws + timedelta(days=6)
        return f"{ws.month}/{ws.day}-{we.month}/{we.day}"
    except Exception:
        return ws_str


# Default to latest completed week
default_idx = len(weeks_set) - 1

week_options = [_format_week_label(w) for w in weeks_set]
sel_col1, sel_col2 = st.columns([1, 3])
with sel_col1:
    sel_week_label = st.selectbox(
        "Report week",
        week_options,
        index=default_idx,
        help="Select the week to generate the report for",
    )

sel_week_idx = week_options.index(sel_week_label)
report_week = weeks_set[sel_week_idx]
prev_week = weeks_set[sel_week_idx - 1] if sel_week_idx > 0 else None

# Week number (weeks since first week in data)
week_number = sel_week_idx + 1
report_week_end = (datetime.strptime(report_week, "%Y-%m-%d") + timedelta(days=6))
report_label = _format_week_label(report_week)

# ---------------------------------------------------------------------------
# Build aggregated data
# ---------------------------------------------------------------------------

# Sales for report week & previous week, grouped by (style, color)
def _aggregate_by_style_color(sales_list, week_start_str):
    """Aggregate units by (style, color) for a given week."""
    agg = {}
    for r in sales_list:
        if str(r["week_start"]) != week_start_str:
            continue
        key = (r["style"], r["color"])
        agg[key] = agg.get(key, 0) + r["units_sold"]
    return agg


def _aggregate_by_style_color_size(sales_list, week_start_str):
    """Aggregate units by (style, color, size) for a given week."""
    agg = {}
    for r in sales_list:
        if str(r["week_start"]) != week_start_str:
            continue
        key = (r["style"], r["color"], r["size"])
        agg[key] = agg.get(key, 0) + r["units_sold"]
    return agg


current_agg = _aggregate_by_style_color(completed_sales, report_week)
prev_agg = _aggregate_by_style_color(completed_sales, prev_week) if prev_week else {}

current_by_sku = _aggregate_by_style_color_size(completed_sales, report_week)
prev_by_sku = _aggregate_by_style_color_size(completed_sales, prev_week) if prev_week else {}

# Inventory lookup
inv_lookup = {}
for r in all_inv:
    key = (r["style"], r["color"], r["size"])
    if key not in inv_lookup:
        inv_lookup[key] = {"available_qty": 0, "total_stock": 0}
    inv_lookup[key]["available_qty"] += r["available_qty"] or 0
    inv_lookup[key]["total_stock"] += r["stock_qty"] or 0

# ---------------------------------------------------------------------------
# Compute metrics for report
# ---------------------------------------------------------------------------
_COLOR_EMOJI = {"Black": "\u26ab", "Olive Green": "\U0001f9c0", "Burgundy": "\U0001f377", "\u2014": ""}

# Total units this week vs last
total_this = sum(current_agg.values())
total_prev = sum(prev_agg.values()) if prev_agg else 0
total_growth = weekly_growth_rate(total_this, total_prev)

# Style+color breakdown with growth
style_color_rows = []
for style in ALL_STYLES:
    for color in get_colors(style):
        key = (style, color)
        this_units = current_agg.get(key, 0)
        prev_units = prev_agg.get(key, 0)
        if this_units == 0 and prev_units == 0:
            continue
        growth = weekly_growth_rate(this_units, prev_units)
        style_color_rows.append({
            "style": style,
            "color": color,
            "units": this_units,
            "prev_units": prev_units,
            "growth": growth,
        })

# Notable changes (>10% change with meaningful volume)
notable_gains = [r for r in style_color_rows if r["growth"] is not None and r["growth"] > 0.10 and r["units"] >= 5]
notable_drops = [r for r in style_color_rows if r["growth"] is not None and r["growth"] < -0.10 and r["prev_units"] >= 5]

# Stock health: for each style+color, compute max weekly growth the stock can tolerate
# "Tolerate X% weekly growth" = stock lasts >= threshold_days at demand * (1+X%)
threshold_days = int(get_setting("stockout_threshold_days") or 14)
warning_days = int(get_setting("warning_threshold_days") or 30)


def _stock_tolerance(style, color):
    """Calculate max weekly growth % stock can handle for threshold_days."""
    total_stock = 0
    total_daily = 0
    for size in get_sizes(style):
        inv_key = (style, color, size)
        sku_key = (style, color, size)
        stock = inv_lookup.get(inv_key, {}).get("available_qty", 0)
        units = current_by_sku.get(sku_key, 0)
        total_stock += stock
        total_daily += daily_demand(units) if units > 0 else 0

    if total_daily <= 0:
        return None, total_stock

    # How many weeks of stock at current demand
    stock_life_wks = total_stock / (total_daily * 7)

    # Find max growth % where stock lasts >= warning_days
    # At growth g: weekly demand = current * (1+g)^week
    # Sum of demand over N weeks = current * sum((1+g)^i for i in 1..N)
    # We want sum <= total_stock
    target_weeks = warning_days / 7
    max_growth = None
    for g_pct in range(200, -1, -1):  # 200% down to 0%
        g = g_pct / 100
        total_demand = 0
        weekly = total_daily * 7
        for w in range(int(target_weeks)):
            weekly_d = weekly * ((1 + g) ** (w + 1))
            total_demand += weekly_d
        if total_demand <= total_stock:
            max_growth = g
            break

    return max_growth, total_stock


# Size-level stock alerts
critical_sizes = []
warning_sizes = []
ok_sizes = []

for style in ALL_STYLES:
    for color in get_colors(style):
        for size in get_sizes(style):
            inv_key = (style, color, size)
            sku_key = (style, color, size)
            stock = inv_lookup.get(inv_key, {}).get("available_qty", 0)
            units = current_by_sku.get(sku_key, 0)
            dd = daily_demand(units) if units > 0 else 0
            life = stock_life_days(stock, dd) if dd > 0 else None

            if life is not None and life <= threshold_days:
                critical_sizes.append((style, color, size, int(life)))
            elif life is not None and life <= warning_days:
                warning_sizes.append((style, color, size, int(life)))
            elif life is not None:
                ok_sizes.append((style, color, size, int(life)))

# Trend observation: last 3-4 weeks for major style+colors
def _trend_description(style, color):
    """Get a short trend description from recent weeks."""
    recent_weeks = weeks_set[-4:] if len(weeks_set) >= 4 else weeks_set
    weekly_units = []
    for w in recent_weeks:
        agg = _aggregate_by_style_color(completed_sales, w)
        weekly_units.append(agg.get((style, color), 0))

    if len(weekly_units) < 2:
        return None

    # Check trend direction
    increases = sum(1 for i in range(1, len(weekly_units)) if weekly_units[i] > weekly_units[i-1])
    decreases = sum(1 for i in range(1, len(weekly_units)) if weekly_units[i] < weekly_units[i-1])
    changes = len(weekly_units) - 1

    if increases == changes:
        return "trending up"
    elif decreases == changes:
        return "trending down"
    elif increases > decreases:
        return "mostly up"
    elif decreases > increases:
        return "mostly down"
    else:
        return "stable"


# ---------------------------------------------------------------------------
# Render Report
# ---------------------------------------------------------------------------
st.markdown("---")

# ---------------------------------------------------------------------------
# Per-product-group summaries
# ---------------------------------------------------------------------------
for group_name, group_styles in PRODUCT_GROUPS.items():
    # Filter data for this product group
    group_this = sum(v for (s, c), v in current_agg.items() if s in group_styles)
    group_prev = sum(v for (s, c), v in prev_agg.items() if s in group_styles) if prev_agg else 0
    group_growth = weekly_growth_rate(group_this, group_prev)
    group_skus = sum(1 for (s, c, sz) in current_by_sku if s in group_styles)

    if group_this == 0 and group_prev == 0:
        continue

    st.markdown(
        f'<h2 style="margin-bottom:0;">'
        f'{group_name} Update | Week {week_number} ({report_label})</h2>',
        unsafe_allow_html=True,
    )

    m1, m2, m3 = st.columns(3)
    with m1:
        delta_str = f"{group_growth:+.1%}" if group_growth is not None else "\u2014"
        st.metric("Units Sold", f"{group_this:,}", delta_str)
    with m2:
        st.metric("Previous Week", f"{group_prev:,}" if prev_agg else "\u2014")
    with m3:
        st.metric("SKUs Tracked", f"{group_skus}")

    st.markdown("---")

st.markdown("---")

# ---------------------------------------------------------------------------
# Summary bullets (Slack-ready text)
# ---------------------------------------------------------------------------
st.subheader("Summary")

bullets = []

# 1. Overall change per product group
for group_name, group_styles in PRODUCT_GROUPS.items():
    g_this = sum(v for (s, c), v in current_agg.items() if s in group_styles)
    g_prev = sum(v for (s, c), v in prev_agg.items() if s in group_styles) if prev_agg else 0
    g_growth = weekly_growth_rate(g_this, g_prev)
    if g_this == 0 and g_prev == 0:
        continue
    if g_growth is not None:
        direction = "up" if g_growth > 0 else "down"
        bullets.append(
            f"Overall {group_name} sales **{direction} {abs(g_growth):.0%}** WoW "
            f"({g_prev:,} \u2192 {g_this:,} units)"
        )
    else:
        bullets.append(f"Overall {group_name} sales: **{g_this:,}** units (no prior week data)")

# 2. Notable gains
if notable_gains:
    gains_sorted = sorted(notable_gains, key=lambda x: x["growth"], reverse=True)
    gain_parts = []
    for r in gains_sorted[:5]:
        emoji = _COLOR_EMOJI.get(r["color"], "")
        gain_parts.append(
            f"{emoji} {r['style']} {r['color']}: {r['prev_units']}→{r['units']} "
            f"(**+{r['growth']:.0%}**)"
        )
    bullets.append("**Notable gains:** " + ", ".join(gain_parts))

# 3. Notable drops
if notable_drops:
    drops_sorted = sorted(notable_drops, key=lambda x: x["growth"])
    drop_parts = []
    for r in drops_sorted[:5]:
        emoji = _COLOR_EMOJI.get(r["color"], "")
        drop_parts.append(
            f"{emoji} {r['style']} {r['color']}: {r['prev_units']}→{r['units']} "
            f"(**{r['growth']:.0%}**)"
        )
    bullets.append("**Notable drops:** " + ", ".join(drop_parts))

# 4. New/small colors summary
for color in ["Olive Green", "Burgundy"]:
    color_total = sum(v for (s, c), v in current_agg.items() if c == color)
    prev_color_total = sum(v for (s, c), v in prev_agg.items() if c == color) if prev_agg else 0
    if color_total > 0:
        emoji = _COLOR_EMOJI.get(color, "")
        if prev_color_total > 0:
            cg = weekly_growth_rate(color_total, prev_color_total)
            cg_str = f" ({cg:+.0%})" if cg is not None else ""
            bullets.append(
                f"**{emoji} {color}:** total {prev_color_total}→{color_total} units{cg_str}"
            )
        else:
            bullets.append(f"**{emoji} {color}:** total {color_total} units")

# 5. Stock health
if critical_sizes:
    critical_labels = [f"{s} {c} {sz} ({d}d)" for s, c, sz, d in critical_sizes[:8]]
    bullets.append(f"\U0001f534 **Critical stock:** {', '.join(critical_labels)}")

if warning_sizes:
    warning_labels = [f"{s} {c} {sz} ({d}d)" for s, c, sz, d in warning_sizes[:8]]
    bullets.append(f"\U0001f7e1 **Low stock warning:** {', '.join(warning_labels)}")

# 6. Stock tolerance summary (PPL Black)
tolerance_notes = []
for style in STYLES:
    max_g, stock = _stock_tolerance(style, "Black")
    if max_g is not None:
        tolerance_notes.append(f"{style}: can tolerate **{max_g:.0%}** weekly growth")

if tolerance_notes:
    bullets.append(
        "**Stock tolerance (Black):** " + " \u00b7 ".join(tolerance_notes)
    )

# 6b. Stock tolerance for Nursing Pillow
np_max_g, np_stock = _stock_tolerance("Nursing Pillow", "\u2014")
if np_max_g is not None:
    bullets.append(f"**Nursing Pillow stock tolerance:** can tolerate **{np_max_g:.0%}** weekly growth")

# 7. Trend observation (PPL)
trend_notes = []
for style in STYLES:
    trend = _trend_description(style, "Black")
    if trend:
        trend_notes.append(f"{style} Black {trend}")

# NP trend
np_trend = _trend_description("Nursing Pillow", "\u2014")
if np_trend:
    trend_notes.append(f"Nursing Pillow {np_trend}")

if trend_notes:
    bullets.append("**Trend (last 4 weeks):** " + ", ".join(trend_notes))

# Render bullets
for b in bullets:
    st.markdown(f"- {b}")

# ---------------------------------------------------------------------------
# Style+Color breakdown table
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Sales by Style + Color")

if style_color_rows:
    table_data = []
    for r in style_color_rows:
        emoji = _COLOR_EMOJI.get(r["color"], "")
        growth_str = f"{r['growth']:+.1%}" if r["growth"] is not None else "—"
        table_data.append({
            "Style": f"{emoji} {r['style']}",
            "Color": r["color"],
            "This Week": r["units"],
            "Last Week": r["prev_units"],
            "Growth": growth_str,
        })

    df = pd.DataFrame(table_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Copy-to-Slack text block (plain text, no emoji)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Copy to Slack")

import re

def _to_slack(md_text):
    """Convert markdown bullets to Slack format: no emoji, clean bold markers."""
    # Remove emoji characters (Unicode emoji ranges)
    text = re.sub(
        r'[\U0001f300-\U0001f9ff\u2600-\u27bf\u26aa\u26ab\U0001f534\U0001f7e1]',
        '', md_text
    )
    # Convert markdown **bold** to Slack *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # Clean up double spaces from emoji removal
    text = re.sub(r'  +', ' ', text).strip()
    # Remove leading comma+space if emoji removal left one
    text = re.sub(r'^,\s*', '', text)
    return text

slack_lines = []

for group_name, group_styles in PRODUCT_GROUPS.items():
    group_this = sum(v for (s, c), v in current_agg.items() if s in group_styles)
    group_prev = sum(v for (s, c), v in prev_agg.items() if s in group_styles) if prev_agg else 0
    if group_this == 0 and group_prev == 0:
        continue
    slack_lines.append(f"*{group_name} Update | Week {week_number} ({report_label})*")
    slack_lines.append("")

for b in bullets:
    slack_lines.append(f"\u2022 {_to_slack(b)}")

slack_text = "\n".join(slack_lines)

st.code(slack_text, language=None)
st.caption("Copy the text above and paste directly into Slack.")
