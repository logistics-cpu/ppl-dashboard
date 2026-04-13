"""Stockout Alerts — SKUs at risk of running out of stock."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from core.config import STYLES, COLORS, SIZES, ALL_STYLES, ALL_SIZES, get_colors, get_sizes
from core.database import init_db, get_latest_inventory, get_setting
from core.calculations import stock_life_days, stockout_date, suggested_reorder_qty
from core.auth import check_password
from core.theme import inject_css, page_header

# ── Auth & Theme ──────────────────────────────────────────────────────────────
if not check_password():
    st.stop()
inject_css()
page_header("Stockout Alerts", "SKUs at risk of running out of stock")

init_db()

# ── Thresholds ────────────────────────────────────────────────────────────────
stockout_threshold = int(get_setting("stockout_threshold_days", "14"))
warning_threshold = int(get_setting("warning_threshold_days", "30"))

st.markdown(
    """
    <style>
        /* Colored metric cards */
        .metric-card {
            border-radius: 12px;
            padding: 20px 24px;
            border: 1px solid #E2E8F0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .metric-card .label {
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 6px;
        }
        .metric-card .value {
            font-size: 2rem;
            font-weight: 700;
            line-height: 1.1;
        }
        .metric-card .help {
            font-size: 0.72rem;
            margin-top: 6px;
            opacity: 0.75;
        }
        .metric-critical {
            background: #FEE2E2;
            border-color: #FECACA;
        }
        .metric-critical .label { color: #991B1B; }
        .metric-critical .value { color: #991B1B; }
        .metric-critical .help  { color: #991B1B; }
        .metric-warning {
            background: #FEF3C7;
            border-color: #FDE68A;
        }
        .metric-warning .label { color: #92400E; }
        .metric-warning .value { color: #92400E; }
        .metric-warning .help  { color: #92400E; }
        .metric-total {
            background: #EFF6FF;
            border-color: #BFDBFE;
        }
        .metric-total .label { color: #1E40AF; }
        .metric-total .value { color: #1E40AF; }
        .metric-total .help  { color: #1E40AF; }

        /* Slider section */
        .slider-section {
            background: white;
            border: 1px solid #E2E8F0;
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .slider-section p {
            margin: 0 0 4px 0;
            font-size: 0.82rem;
            font-weight: 600;
            color: #475569;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        /* Success banner */
        .success-banner {
            background: #D1FAE5;
            border: 1px solid #A7F3D0;
            border-radius: 12px;
            padding: 32px 24px;
            text-align: center;
            margin-top: 12px;
        }
        .success-banner .icon {
            font-size: 2.4rem;
            margin-bottom: 8px;
        }
        .success-banner .title {
            font-size: 1.1rem;
            font-weight: 700;
            color: #065F46;
            margin-bottom: 4px;
        }
        .success-banner .subtitle {
            font-size: 0.88rem;
            color: #065F46;
            opacity: 0.8;
        }

        /* Section divider */
        .section-header {
            font-size: 1.05rem;
            font-weight: 700;
            color: #0F172A;
            margin: 32px 0 12px 0;
            padding-bottom: 8px;
            border-bottom: 2px solid #E2E8F0;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Threshold Slider ──────────────────────────────────────────────────────────
threshold = st.slider(
    "Alert Threshold — days of stock remaining",
    min_value=1,
    max_value=90,
    value=warning_threshold,
)
st.caption(f"Showing SKUs with fewer than **{threshold} days** of stock remaining.")

# ── Load Data ─────────────────────────────────────────────────────────────────
rows = get_latest_inventory()

if not rows:
    st.info("No inventory data yet. Go to **Data Management** to upload an ERP export.")
    st.stop()

df = pd.DataFrame(rows)
df["avg_daily_demand"] = (df["sales_7d"] / 7).round(2)
df["stock_life"] = df.apply(
    lambda r: stock_life_days(r["available_qty"], r["avg_daily_demand"]), axis=1
)
df["stockout_date"] = df.apply(
    lambda r: stockout_date(r["available_qty"], r["avg_daily_demand"]), axis=1
)
df["reorder_qty"] = df["avg_daily_demand"].apply(
    lambda d: suggested_reorder_qty(d)
)

# ── Filter to At-Risk Items ──────────────────────────────────────────────────
at_risk = df[df["stock_life"].notna() & (df["stock_life"] <= threshold)].copy()
at_risk = at_risk.sort_values("stock_life").reset_index(drop=True)

# ── Summary Metrics ───────────────────────────────────────────────────────────
critical = len(at_risk[at_risk["stock_life"] <= stockout_threshold])
warning = len(
    at_risk[
        (at_risk["stock_life"] > stockout_threshold)
        & (at_risk["stock_life"] <= warning_threshold)
    ]
)
total_at_risk = len(at_risk)

m1, m2, m3 = st.columns(3)

with m1:
    st.markdown(
        f"""
        <div class="metric-card metric-critical">
            <div class="label">Critical</div>
            <div class="value">{critical}</div>
            <div class="help">&lt; {stockout_threshold} days of stock</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with m2:
    st.markdown(
        f"""
        <div class="metric-card metric-warning">
            <div class="label">Warning</div>
            <div class="value">{warning}</div>
            <div class="help">{stockout_threshold}–{warning_threshold} days of stock</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with m3:
    st.markdown(
        f"""
        <div class="metric-card metric-total">
            <div class="label">Total At Risk</div>
            <div class="value">{total_at_risk}</div>
            <div class="help">SKUs below {threshold}-day threshold</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("")  # spacing

# ── No Alerts — Success State ─────────────────────────────────────────────────
if at_risk.empty:
    st.markdown(
        f"""
        <div class="success-banner">
            <div class="icon">&#10003;</div>
            <div class="title">All Clear — No Stockout Alerts</div>
            <div class="subtitle">No SKUs are below {threshold} days of stock. Looking good!</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ── At-Risk SKU Table ─────────────────────────────────────────────────────────
st.markdown('<div class="section-header">At-Risk SKUs</div>', unsafe_allow_html=True)

display_df = at_risk[
    [
        "style",
        "color",
        "size",
        "warehouse",
        "available_qty",
        "avg_daily_demand",
        "stock_life",
        "stockout_date",
        "reorder_qty",
        "in_transit_qty",
    ]
].copy()

display_df.columns = [
    "Style",
    "Color",
    "Size",
    "Warehouse",
    "Available",
    "Avg Daily Demand",
    "Stock Life (Days)",
    "Stockout Date",
    "Suggested Reorder",
    "On-Order",
]

# Sort by size order XS → 3XL
SIZE_ORDER = {s: i for i, s in enumerate(ALL_SIZES)}
display_df["_size_order"] = display_df["Size"].map(SIZE_ORDER)
display_df = display_df.sort_values(["Stock Life (Days)", "_size_order"]).drop(columns=["_size_order"]).reset_index(drop=True)

display_df["Stock Life (Days)"] = display_df["Stock Life (Days)"].round(0)


def highlight_urgency(row):
    """Color-code rows by urgency level."""
    life = row["Stock Life (Days)"]
    if life <= stockout_threshold:
        return [f"background-color: #FEE2E2; color: #991B1B"] * len(row)
    elif life <= warning_threshold:
        return [f"background-color: #FEF3C7; color: #92400E"] * len(row)
    return [""] * len(row)


styled = display_df.style.apply(highlight_urgency, axis=1)
st.dataframe(
    styled,
    use_container_width=True,
    hide_index=True,
    height=500,
    column_config={
        "Avg Daily Demand": st.column_config.NumberColumn("Avg Daily Demand", format="%.1f"),
        "Stock Life (Days)": st.column_config.NumberColumn("Stock Life (Days)", format="%.0f"),
    },
)

# ── Alert Summary by Product ──────────────────────────────────────────────────
st.markdown(
    '<div class="section-header">Alert Summary by Product</div>',
    unsafe_allow_html=True,
)

alert_summary = (
    at_risk.groupby(["style", "color"])
    .agg(
        sizes_at_risk=("size", "count"),
        min_stock_life=("stock_life", "min"),
        total_available=("available_qty", "sum"),
        total_reorder=("reorder_qty", "sum"),
    )
    .reset_index()
)
alert_summary.columns = [
    "Style",
    "Color",
    "Sizes At Risk",
    "Min Stock Life",
    "Total Available",
    "Total Reorder Qty",
]
alert_summary["Min Stock Life"] = alert_summary["Min Stock Life"].round(0)
st.dataframe(alert_summary, use_container_width=True, hide_index=True)
