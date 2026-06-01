"""Dropship Orders — orders shipped from China, imported from ERP Excel."""

import streamlit as st
st.set_page_config(layout="wide")

import pandas as pd
from datetime import date, timedelta

from core.database import (
    init_db, get_dropship_orders, get_dropship_summary,
)
from core.theme import inject_css, page_header
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header(
    "Dropship Orders",
    "Orders shipped from China (ERP import) — destinations, products, warehouses",
)

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
all_summary = get_dropship_summary()
if all_summary["total_orders"] == 0:
    st.info(
        "No dropship data yet. Go to **Data Management → Dropship Upload** "
        "and upload your ERP Excel."
    )
    st.stop()

today = date.today()
default_start = today - timedelta(days=30)

fcol1, fcol2, fcol3 = st.columns([2, 2, 2])
with fcol1:
    f_start = st.date_input("From", value=default_start, key="ds_filter_start")
with fcol2:
    f_end = st.date_input("To", value=today, key="ds_filter_end")
with fcol3:
    f_limit = st.selectbox(
        "Show", options=[100, 250, 500, 1000, 2000], index=2, key="ds_limit",
    )

# Load summary in range for filter options + KPIs
in_range = get_dropship_orders(
    start_date=f_start.isoformat(),
    end_date=f_end.isoformat(),
    limit=20000,
)
warehouse_options = ["All warehouses"] + sorted({
    r["warehouse"] for r in in_range if r.get("warehouse")
})
country_options = ["All countries"] + sorted({
    r["country"] for r in in_range if r.get("country")
})

fc1, fc2 = st.columns(2)
with fc1:
    sel_warehouse = st.selectbox("Warehouse", warehouse_options, key="ds_wh")
with fc2:
    sel_country = st.selectbox("Destination", country_options, key="ds_ctry")

wh_filter = None if sel_warehouse == "All warehouses" else sel_warehouse
ctry_filter = None if sel_country == "All countries" else sel_country

# Fetch ALL rows matching filters for KPI / summary calculation (no row limit).
# A separate, limited query is used only for the detail table below.
all_filtered = get_dropship_orders(
    start_date=f_start.isoformat(),
    end_date=f_end.isoformat(),
    warehouse=wh_filter,
    country=ctry_filter,
    limit=100000,
)

# ---------------------------------------------------------------------------
# KPIs (always based on the FULL date range + filters, not the display limit)
# ---------------------------------------------------------------------------
unique_orders = len({r["order_number"] for r in all_filtered if r.get("order_number")})
total_units = sum((r.get("quantity") or 0) for r in all_filtered)
unique_countries = len({r.get("country") for r in all_filtered if r.get("country")})

# Share of line items shipped from China
china_shipped = sum(1 for r in all_filtered if r.get("warehouse") == "China")
china_pct = (china_shipped / len(all_filtered) * 100) if all_filtered else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Orders", f"{unique_orders:,}")
m2.metric("Units", f"{total_units:,}")
m3.metric("Destinations", f"{unique_countries}")
m4.metric("From China", f"{china_pct:.0f}%")

st.caption(
    f"Stats above cover all rows from {f_start.isoformat()} to {f_end.isoformat()}. "
    f"Detail table below shows up to {f_limit} most recent rows."
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Warehouse + Country summaries (use the FULL filtered set, not the limited table)
# ---------------------------------------------------------------------------
sc1, sc2 = st.columns(2)
with sc1:
    st.markdown("### By Warehouse")
    wh_data = {}
    for r in all_filtered:
        w = r.get("warehouse") or "Unknown"
        if w not in wh_data:
            wh_data[w] = {"orders": set(), "units": 0}
        wh_data[w]["orders"].add(r.get("order_number"))
        wh_data[w]["units"] += r.get("quantity") or 0
    wh_df = pd.DataFrame([
        {"Warehouse": w, "Orders": len(d["orders"]), "Units": d["units"]}
        for w, d in sorted(wh_data.items(), key=lambda x: -len(x[1]["orders"]))
    ])
    st.dataframe(wh_df, use_container_width=True, hide_index=True)

with sc2:
    st.markdown("### Top Destinations")
    ctry_data = {}
    for r in all_filtered:
        c = r.get("country") or "Unknown"
        if c not in ctry_data:
            ctry_data[c] = {"orders": set(), "units": 0}
        ctry_data[c]["orders"].add(r.get("order_number"))
        ctry_data[c]["units"] += r.get("quantity") or 0
    ctry_df = pd.DataFrame([
        {"Country": c, "Orders": len(d["orders"]), "Units": d["units"]}
        for c, d in sorted(ctry_data.items(), key=lambda x: -len(x[1]["orders"]))[:15]
    ])
    st.dataframe(ctry_df, use_container_width=True, hide_index=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Detail table (limited to f_limit most recent rows for display performance)
# ---------------------------------------------------------------------------
st.markdown("### Order Detail")
rows = all_filtered[:f_limit]
detail_rows = []
for r in rows:
    detail_rows.append({
        "Order #": r.get("order_number"),
        "Paid": r.get("paid_at_local"),
        "Warehouse": r.get("warehouse") or "—",
        "Country": r.get("country") or "—",
        "Region": r.get("region") or "—",
        "Qty": r.get("quantity"),
        "Shopify SKU": r.get("shopify_sku") or "—",
        "Mapped": (
            f"{r['style']} / {r['color']} / {r['size']}"
            if r.get("style") else "—"
        ),
        "Carrier": r.get("shipping_carrier") or "—",
    })

if detail_rows:
    df = pd.DataFrame(detail_rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(f"Showing {len(detail_rows)} line items.")
else:
    st.info("No dropship orders match the selected filters.")
