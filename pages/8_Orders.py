"""Orders — list view of synced Shopify orders with filters."""

import streamlit as st
st.set_page_config(layout="wide")

import pandas as pd
from datetime import date, timedelta

from core.database import init_db, get_orders, get_order_items, get_orders_count
from core.theme import inject_css, page_header
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header("Orders", "Recent Shopify orders — geography, channel, line items")

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
total_orders = get_orders_count()
if total_orders == 0:
    st.info(
        "No order data yet. Go to **Data Management → Order Sync** to pull orders from Shopify."
    )
    st.stop()

st.caption(f"Total orders in database: **{total_orders}**")

today = date.today()
default_start = today - timedelta(days=14)

fcol1, fcol2, fcol3 = st.columns([2, 2, 2])
with fcol1:
    f_start = st.date_input("From", value=default_start, key="orders_filter_start")
with fcol2:
    f_end = st.date_input("To", value=today, key="orders_filter_end")
with fcol3:
    f_limit = st.selectbox("Show", options=[50, 100, 250, 500, 1000], index=1, key="orders_limit")

# Country filter: load distinct countries from filtered range
all_in_range = get_orders(
    start_date=f_start.isoformat(),
    end_date=f_end.isoformat(),
    limit=10000,
)
country_options = sorted({
    (o.get("ship_country_code") or "—", o.get("ship_country") or "Unknown")
    for o in all_in_range
})
country_labels = ["All countries"] + [f"{cc} — {cn}" for cc, cn in country_options]
country_map = {f"{cc} — {cn}": cc for cc, cn in country_options}

fc1, _ = st.columns([2, 4])
with fc1:
    sel_country_label = st.selectbox("Country", options=country_labels, key="orders_country")
sel_country = country_map.get(sel_country_label) if sel_country_label != "All countries" else None

# ---------------------------------------------------------------------------
# Fetch + summarize
# ---------------------------------------------------------------------------
orders = get_orders(
    start_date=f_start.isoformat(),
    end_date=f_end.isoformat(),
    country=sel_country,
    limit=f_limit,
)

if not orders:
    st.info("No orders match the selected filters.")
    st.stop()

# Top-line stats
total_revenue = sum((o.get("total_price") or 0) for o in orders)
avg_order = total_revenue / len(orders) if orders else 0
unique_countries = len({o.get("ship_country_code") for o in orders if o.get("ship_country_code")})

m1, m2, m3, m4 = st.columns(4)
m1.metric("Orders", f"{len(orders):,}")
m2.metric("Revenue", f"${total_revenue:,.0f}")
m3.metric("Avg order", f"${avg_order:.2f}")
m4.metric("Countries", f"{unique_countries}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Orders table
# ---------------------------------------------------------------------------
table_rows = []
for o in orders:
    table_rows.append({
        "Order #": o.get("order_number"),
        "Date": o.get("created_at_local"),
        "Country": o.get("ship_country_code") or "—",
        "State": o.get("ship_state_code") or o.get("ship_state") or "—",
        "City": o.get("ship_city") or "—",
        "Channel": o.get("source_name") or "—",
        "Status": o.get("financial_status") or "—",
        "Total": f"${(o.get('total_price') or 0):.2f}",
        "Discount": f"${(o.get('total_discounts') or 0):.2f}",
        "Shopify ID": o.get("shopify_order_id"),
    })
df = pd.DataFrame(table_rows)
st.dataframe(
    df.drop(columns=["Shopify ID"]),
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------------------
# Order details viewer (expand line items for a specific order)
# ---------------------------------------------------------------------------
st.markdown("### Order detail")
order_numbers = [o.get("order_number") for o in orders if o.get("order_number")]
selected_order_num = st.selectbox(
    "Select an order to see line items",
    options=order_numbers,
    key="orders_detail_picker",
)
if selected_order_num:
    selected = next(o for o in orders if o.get("order_number") == selected_order_num)
    items = get_order_items(selected["shopify_order_id"])

    dc1, dc2, dc3 = st.columns(3)
    dc1.markdown(f"**Order:** {selected.get('order_number')}")
    dc1.markdown(f"**Date:** {selected.get('created_at_local')}")
    dc2.markdown(f"**Country:** {selected.get('ship_country') or '—'}")
    dc2.markdown(f"**State:** {selected.get('ship_state') or '—'}")
    dc2.markdown(f"**City:** {selected.get('ship_city') or '—'}")
    dc3.markdown(f"**Total:** ${selected.get('total_price') or 0:.2f}")
    dc3.markdown(f"**Discount:** ${selected.get('total_discounts') or 0:.2f}")
    dc3.markdown(f"**Channel:** {selected.get('source_name') or '—'}")

    if items:
        item_rows = [
            {
                "SKU": it.get("shopify_sku") or "—",
                "Product": it.get("product_title") or "—",
                "Variant": it.get("variant_title") or "—",
                "Qty": it.get("quantity"),
                "Unit Price": f"${(it.get('unit_price') or 0):.2f}",
                "Tracked": "✓" if it.get("style") else "—",
                "Mapped to": (
                    f"{it['style']} / {it['color']} / {it['size']}"
                    if it.get("style") else "—"
                ),
            }
            for it in items
        ]
        st.dataframe(pd.DataFrame(item_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No line items found for this order.")
