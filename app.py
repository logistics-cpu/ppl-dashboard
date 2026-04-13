"""PPL Weekly Sales Tracking Dashboard — Main entry point."""

import streamlit as st
from core.database import init_db, get_last_sync, get_latest_inventory, get_weekly_sales
from core.auth import check_password
from core.theme import inject_css, page_header, PRIMARY, TEXT_MUTED, DANGER, WARNING, SUCCESS

st.set_page_config(
    page_title="PPL Sales Dashboard",
    page_icon="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%231E40AF'/><text x='50' y='68' text-anchor='middle' font-size='50' fill='white'>P</text></svg>",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not check_password():
    st.stop()

init_db()
inject_css()

# Sidebar
st.sidebar.markdown(f"""
<div style="padding: 0.5rem 0;">
    <h1 style="color:{PRIMARY};font-size:1.3rem;margin:0;">Sales Dashboard</h1>
    <p style="color:{TEXT_MUTED};font-size:0.8rem;margin:2px 0 0 0;">PPL &amp; Nursing Pillow</p>
</div>
""", unsafe_allow_html=True)
st.sidebar.divider()

last_shopify = get_last_sync("shopify_sales")
last_erp = get_last_sync("erp_upload")

st.sidebar.markdown(f"**Sync Status**")
if last_shopify:
    st.sidebar.markdown(f'<span style="color:{SUCCESS};font-size:0.85rem;">Shopify: {last_shopify["completed_at"][:10]}</span>', unsafe_allow_html=True)
else:
    st.sidebar.markdown(f'<span style="color:{TEXT_MUTED};font-size:0.85rem;">Shopify: Not synced</span>', unsafe_allow_html=True)

if last_erp:
    st.sidebar.markdown(f'<span style="color:{SUCCESS};font-size:0.85rem;">ERP: {last_erp["completed_at"][:10]}</span>', unsafe_allow_html=True)
else:
    st.sidebar.markdown(f'<span style="color:{TEXT_MUTED};font-size:0.85rem;">ERP: No upload</span>', unsafe_allow_html=True)

# Main content
page_header("Sales Dashboard", "Weekly sales tracking, inventory management, and stockout alerts")

# Quick stats from real data
inv_rows = get_latest_inventory()
sales_rows = get_weekly_sales()

total_stock = sum(r["available_qty"] for r in inv_rows) if inv_rows else 0
total_skus = len(set((r["style"], r["color"], r["size"]) for r in inv_rows)) if inv_rows else 0
total_units_sold = sum(r["units_sold"] for r in sales_rows) if sales_rows else 0
weeks_tracked = len(set(r["week_start"] for r in sales_rows)) if sales_rows else 0

# Count critical items
critical_count = 0
if inv_rows:
    for r in inv_rows:
        demand = r["sales_7d"] / 7 if r["sales_7d"] and r["sales_7d"] > 0 else 0
        if demand > 0:
            life = r["available_qty"] / demand
            if life <= 14:
                critical_count += 1

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Stock", f"{total_stock:,}" if total_stock else "--")
m2.metric("Units Sold", f"{total_units_sold:,}" if total_units_sold else "--")
m3.metric("Weeks Tracked", weeks_tracked if weeks_tracked else "--")

if critical_count > 0:
    m4.metric("Stockout Alerts", critical_count, delta=f"{critical_count} critical", delta_color="inverse")
else:
    m4.metric("Stockout Alerts", "0", delta="All clear", delta_color="normal")

st.markdown("")

# Navigation cards
col1, col2 = st.columns(2)

with col1:
    st.markdown(f"""
    <div style="background:white;border:1px solid #E2E8F0;border-radius:12px;padding:24px;margin-bottom:16px;">
        <h3 style="margin:0 0 8px 0;font-size:1.1rem;">Weekly Sales</h3>
        <p style="color:{TEXT_MUTED};margin:0;font-size:0.9rem;">Sales data by style, color, and size. Auto-synced from Shopify with growth trends and daily demand calculations.</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="background:white;border:1px solid #E2E8F0;border-radius:12px;padding:24px;margin-bottom:16px;">
        <h3 style="margin:0 0 8px 0;font-size:1.1rem;">Stockout Alerts</h3>
        <p style="color:{TEXT_MUTED};margin:0;font-size:0.9rem;">SKUs at risk of running out. Sorted by urgency with suggested reorder quantities.</p>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div style="background:white;border:1px solid #E2E8F0;border-radius:12px;padding:24px;margin-bottom:16px;">
        <h3 style="margin:0 0 8px 0;font-size:1.1rem;">Inventory Snapshot</h3>
        <p style="color:{TEXT_MUTED};margin:0;font-size:0.9rem;">Current stock levels across all warehouses from ERP upload. Stock life and stockout projections.</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="background:white;border:1px solid #E2E8F0;border-radius:12px;padding:24px;margin-bottom:16px;">
        <h3 style="margin:0 0 8px 0;font-size:1.1rem;">Trends</h3>
        <p style="color:{TEXT_MUTED};margin:0;font-size:0.9rem;">Sales and inventory charts over time. Demand heatmaps and stock distribution analysis.</p>
    </div>
    """, unsafe_allow_html=True)

if not inv_rows and not sales_rows:
    st.info("Get started by going to **Data Management** in the sidebar to sync Shopify data or upload an ERP inventory export.")
