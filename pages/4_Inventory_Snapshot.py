"""Inventory Snapshot — current stock levels across all warehouses."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from core.auth import check_password
from core.theme import inject_css, page_header, metric_row, CRITICAL_BG, WARNING_BG, OK_BG
from core.config import STYLES, COLORS, SIZES, WAREHOUSE_DISPLAY_NAMES, ALL_STYLES, ALL_SIZES, get_colors, get_sizes
from core.database import init_db, get_latest_inventory, get_setting
from core.calculations import stock_life_days, stockout_date

# ── Auth & Theme ──────────────────────────────────────────────────────────────
if not check_password():
    st.stop()
inject_css()
page_header("Inventory Snapshot", "Current stock levels across all warehouses")

# ── Database ──────────────────────────────────────────────────────────────────
init_db()

# ── Filters ───────────────────────────────────────────────────────────────────
filter_cols = st.columns([1, 1, 1, 1])
with filter_cols[0]:
    sel_warehouse = st.selectbox("Warehouse", ["All"] + WAREHOUSE_DISPLAY_NAMES)
with filter_cols[1]:
    sel_style = st.selectbox("Style", ["All"] + ALL_STYLES, key="inv_style")
with filter_cols[2]:
    sel_color = st.selectbox("Color", ["All"] + COLORS, key="inv_color")

wh_filter = sel_warehouse if sel_warehouse != "All" else None
rows = get_latest_inventory(warehouse=wh_filter)

if not rows:
    st.info("No inventory data yet. Go to **Data Management** to upload an ERP export.")
    st.stop()

df = pd.DataFrame(rows)

# Apply filters
if sel_style != "All":
    df = df[df["style"] == sel_style]
if sel_color != "All":
    df = df[df["color"] == sel_color]

if df.empty:
    st.warning("No data matches the selected filters.")
    st.stop()

# ── Calculations ──────────────────────────────────────────────────────────────
stockout_threshold = int(get_setting("stockout_threshold_days", "14"))
warning_threshold = int(get_setting("warning_threshold_days", "30"))

df["avg_daily_demand"] = (df["sales_7d"] / 7).round(1)
df["stock_life_calc"] = df.apply(
    lambda r: stock_life_days(r["available_qty"], r["avg_daily_demand"]), axis=1
)
df["stockout_date"] = df.apply(
    lambda r: stockout_date(r["available_qty"], r["avg_daily_demand"]), axis=1
)

# ── Summary Metrics ───────────────────────────────────────────────────────────
total_stock = df["available_qty"].sum()
at_risk = len(
    df[(df["stock_life_calc"].notna()) & (df["stock_life_calc"] <= stockout_threshold)]
)
warning_count = len(
    df[
        (df["stock_life_calc"].notna())
        & (df["stock_life_calc"] > stockout_threshold)
        & (df["stock_life_calc"] <= warning_threshold)
    ]
)
snapshot_date = df["snapshot_date"].iloc[0] if len(df) > 0 else "-"

st.markdown("")
metric_row([
    ("Total Available Stock", f"{total_stock:,}"),
    ("Critical SKUs", str(at_risk)),
    ("Warning SKUs", str(warning_count)),
    ("Snapshot Date", snapshot_date),
])
st.markdown("")

# ── Detailed Inventory Table ──────────────────────────────────────────────────
st.subheader("Detailed Inventory")
st.caption(
    f"Rows highlighted by stock life: "
    f"**Critical** = {stockout_threshold} days or fewer \u00b7 "
    f"**Warning** = {stockout_threshold}\u2013{warning_threshold} days \u00b7 "
    f"**OK** = {warning_threshold}+ days"
)

display_df = df[[
    "style", "color", "size", "warehouse",
    "available_qty", "stock_qty", "avg_daily_demand",
    "stock_life_calc", "stockout_date",
    "sales_7d", "sales_28d", "in_transit_qty",
]].copy()

display_df.columns = [
    "Style", "Color", "Size", "Warehouse",
    "Available", "Total Stock", "Avg Daily Demand",
    "Stock Life (Days)", "Stockout Date",
    "7-Day Sales", "28-Day Sales", "On-Order",
]

# Sort by size order XS → 3XL
SIZE_ORDER = {s: i for i, s in enumerate(ALL_SIZES)}
display_df["_size_order"] = display_df["Size"].map(SIZE_ORDER)
display_df = display_df.sort_values(["Style", "Color", "_size_order", "Warehouse"]).drop(columns=["_size_order"]).reset_index(drop=True)

# Round for readability
display_df["Avg Daily Demand"] = display_df["Avg Daily Demand"].round(1)
display_df["Stock Life (Days)"] = display_df["Stock Life (Days)"].round(0)


def highlight_stock_life(row):
    """Color-code rows based on stock life thresholds."""
    life = row["Stock Life (Days)"]
    if pd.isna(life):
        return [""] * len(row)
    if life <= stockout_threshold:
        return [f"background-color: {CRITICAL_BG}"] * len(row)
    elif life <= warning_threshold:
        return [f"background-color: {WARNING_BG}"] * len(row)
    return [f"background-color: {OK_BG}"] * len(row)


styled = display_df.style.apply(highlight_stock_life, axis=1)
st.dataframe(
    styled,
    use_container_width=True,
    hide_index=True,
    height=600,
    column_config={
        "Avg Daily Demand": st.column_config.NumberColumn("Avg Daily Demand", format="%.1f"),
        "Stock Life (Days)": st.column_config.NumberColumn("Stock Life (Days)", format="%.0f"),
    },
)

# ── Aggregated View ───────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Stock Summary by Product")
st.caption("Aggregated across all sizes and warehouses for each style / color combination.")

agg = (
    df.groupby(["style", "color"])
    .agg(
        total_available=("available_qty", "sum"),
        total_stock=("stock_qty", "sum"),
        total_in_transit=("in_transit_qty", "sum"),
        avg_demand=("avg_daily_demand", "sum"),
    )
    .reset_index()
)
agg.columns = [
    "Style", "Color", "Available", "Total Stock", "On-Order", "Total Daily Demand",
]
st.dataframe(agg, use_container_width=True, hide_index=True)
