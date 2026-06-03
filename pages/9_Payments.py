"""Payments / Invoice Tracking — where the money goes."""

import streamlit as st
st.set_page_config(layout="wide")

import pandas as pd
from collections import defaultdict
from datetime import date

import plotly.express as px

from core.database import (
    init_db, get_payments, get_payment_summary_by_category,
    get_payment_summary_by_month_category, get_payment_summary_by_month_country,
    get_payment_available_months, PAYMENT_CATEGORIES,
)
from core.theme import inject_css, page_header
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header(
    "Payments",
    "Invoice tracking — where money goes by category, country and month",
)

# ---------------------------------------------------------------------------
# Bail early if no data
# ---------------------------------------------------------------------------
available_months = get_payment_available_months()
if not available_months:
    st.info(
        "No payment data yet. Go to **Data Management → Payments Upload** "
        "and upload the finance Excel."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Filters — single Period dropdown (current month, rolling windows, individual months)
# ---------------------------------------------------------------------------
def _months_back(n):
    """Return the YYYY-MM that is n calendar months before today."""
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _fmt_ym_long(ym):
    """'2026-05' → 'May 2026' for the period dropdown."""
    try:
        return pd.to_datetime(ym + "-01").strftime("%B %Y")
    except Exception:
        return ym


current_ym = date.today().strftime("%Y-%m")
# Default = most recent month that actually has data
default_ym = available_months[0]  # available_months is sorted newest-first
default_label = _fmt_ym_long(default_ym)

period_options = ["Last 3 Months", "Last 6 Months",
                  "Last 9 Months", "Last 12 Months"]
period_ranges = {
    "Last 3 Months":  (_months_back(2),  current_ym),
    "Last 6 Months":  (_months_back(5),  current_ym),
    "Last 9 Months":  (_months_back(8),  current_ym),
    "Last 12 Months": (_months_back(11), current_ym),
}

# Append every individual month that has data, formatted as 'May 2026'
for _ym in available_months:
    _lbl = _fmt_ym_long(_ym)
    if _lbl not in period_ranges:
        period_options.append(_lbl)
        period_ranges[_lbl] = (_ym, _ym)

filt_c1, filt_c2 = st.columns([2, 1])
with filt_c1:
    sel_period = st.selectbox(
        "Period",
        options=period_options,
        index=period_options.index(default_label),  # default: newest data month
        key="pay_period",
    )
with filt_c2:
    include_stock = st.checkbox(
        "Include Stock Payments",
        value=False,
        help=(
            "Stock Payments = monthly deposits to the China agency. "
            "Excluded by default so the breakdown shows real operating spend."
        ),
        key="pay_include_stock",
    )

start_ym, end_ym = period_ranges[sel_period]

# Period label used in chart titles (so screenshots / downloads show the period)
if start_ym == end_ym:
    period_label = _fmt_ym_long(start_ym)
else:
    period_label = f"{_fmt_ym_long(start_ym)} – {_fmt_ym_long(end_ym)}"

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
cat_summary = get_payment_summary_by_category(
    start_ym=start_ym, end_ym=end_ym, include_stock=include_stock,
)

# Operational figures (excludes Stock Payments regardless of the toggle —
# Stock Payments represent agency deposits, not operating spend)
op_summary = get_payment_summary_by_category(
    start_ym=start_ym, end_ym=end_ym, include_stock=False,
)
total_outflow = sum(r["total"] for r in op_summary if r["total"] > 0)
total_inflow = sum(r["total"] for r in op_summary if r["total"] < 0)
# Net = what the agency actually used = outflow - refunds
net_spend = total_outflow + total_inflow  # total_inflow is already negative

invoiced_categories = {
    cat for cat, meta in PAYMENT_CATEGORIES.items() if meta.get("has_invoice")
}
invoiced_amount = sum(
    r["total"] for r in op_summary
    if r["category"] in invoiced_categories and r["total"] > 0
)
invoiced_pct = (invoiced_amount / total_outflow * 100) if total_outflow else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Outflow", f"${total_outflow:,.0f}")
m2.metric("Invoiced %", f"{invoiced_pct:.0f}%", help="Share of outflow with formal invoices")
m3.metric("Refunds / Negatives", f"${abs(total_inflow):,.0f}")
m4.metric(
    "Net Spend",
    f"${net_spend:,.0f}",
    help="Outflow − Refunds. The amount actually paid out for operations this period.",
)

if not include_stock:
    st.caption(
        "Charts and tables below **exclude Stock Payments**. "
        "Toggle the checkbox above to include them."
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# 🥧 Pie chart — Spend by Category
# ---------------------------------------------------------------------------
sc1, sc2 = st.columns([3, 2])

with sc1:
    st.markdown("### 🥧 Spend by Category")
    pos_cat = [r for r in cat_summary if r["total"] > 0]
    if pos_cat:
        pie_df = pd.DataFrame([
            {"Category": r["category"] or "(Uncategorized)", "Amount": r["total"]}
            for r in pos_cat
        ])
        fig_pie = px.pie(
            pie_df, names="Category", values="Amount", hole=0.45,
            title=f"Spend by Category — {period_label}",
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No positive spend in the selected range.")

with sc2:
    st.markdown("### 📊 Category Totals")
    cat_table = pd.DataFrame([
        {
            "Category": r["category"] or "(Uncategorized)",
            "Amount": f"${r['total']:,.2f}",
            "Share": (
                f"{(r['total'] / total_outflow * 100):.1f}%"
                if r["total"] > 0 and total_outflow else "—"
            ),
            "# txns": r["n"],
        }
        for r in cat_summary
    ])
    st.dataframe(cat_table, use_container_width=True, hide_index=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# 📈 Monthly trend (total outflow + by category)
# ---------------------------------------------------------------------------
st.markdown("### 📈 Monthly Trend")
month_cat_rows = get_payment_summary_by_month_category(
    start_ym=start_ym, end_ym=end_ym, include_stock=include_stock,
)
def _fmt_month(ym):
    """Format 'YYYY-MM' as e.g. 'Jan ‘26' for chart axes."""
    try:
        return pd.to_datetime(ym + "-01").strftime("%b ‘%y")
    except Exception:
        return ym

if month_cat_rows:
    df_mc = pd.DataFrame(month_cat_rows)
    df_mc["category"] = df_mc["category"].fillna("(Uncategorized)")
    # Spend trend only — drop negative aggregates (refunds, deposits)
    df_mc_spend = df_mc[df_mc["total"] > 0].copy()
    df_mc_spend["Month"] = df_mc_spend["year_month"].apply(_fmt_month)
    month_order = sorted(df_mc_spend["year_month"].unique())
    month_label_order = [_fmt_month(m) for m in month_order]

    fig_trend = px.bar(
        df_mc_spend, x="Month", y="total", color="category",
        title=f"Monthly Spend by Category (positive spend only) — {period_label}",
        labels={"Month": "Month", "total": "Amount ($)", "category": "Category"},
        barmode="stack",
        category_orders={"Month": month_label_order},
    )
    fig_trend.update_layout(legend_title_text="Category", xaxis_type="category")
    st.plotly_chart(fig_trend, use_container_width=True)
else:
    st.info("No monthly data.")

st.markdown("---")

# ---------------------------------------------------------------------------
# 🌍 Monthly trend by country (US / CA / AU / Other)
# ---------------------------------------------------------------------------
st.markdown("### 🌍 Spend by Country")
month_ctry_rows = get_payment_summary_by_month_country(
    start_ym=start_ym, end_ym=end_ym, include_stock=include_stock,
)
if month_ctry_rows:
    df_mt = pd.DataFrame(month_ctry_rows)
    # Spend trend only — drop negative country aggregates
    df_mt_spend = df_mt[df_mt["total"] > 0].copy()
    df_mt_spend["Month"] = df_mt_spend["year_month"].apply(_fmt_month)
    ctry_month_order = sorted(df_mt_spend["year_month"].unique())
    ctry_month_label_order = [_fmt_month(m) for m in ctry_month_order]

    fig_ctry = px.bar(
        df_mt_spend, x="Month", y="total", color="country",
        barmode="group",
        title=f"Monthly Spend by Country (positive spend only) — {period_label}",
        labels={"Month": "Month", "total": "Amount ($)", "country": "Country"},
        category_orders={
            "Month": ctry_month_label_order,
            "country": ["US", "UK", "CA", "AU", "Other", "Unknown"],
        },
    )
    fig_ctry.update_layout(xaxis_type="category")
    st.plotly_chart(fig_ctry, use_container_width=True)
else:
    st.info("No country breakdown.")

st.markdown("---")

# ---------------------------------------------------------------------------
# 📊 Monthly comparison table (Category × Month, with % change)
# ---------------------------------------------------------------------------
st.markdown("### 📊 Monthly Comparison (Category × Month)")
if month_cat_rows:
    # Pivot
    pivot = defaultdict(lambda: defaultdict(float))
    cats_in_data = set()
    months_in_data = set()
    for r in month_cat_rows:
        cat = r["category"] or "(Uncategorized)"
        pivot[cat][r["year_month"]] = r["total"] or 0
        cats_in_data.add(cat)
        months_in_data.add(r["year_month"])

    months_sorted = sorted(months_in_data)
    # Order categories by total spend desc
    cat_totals = {c: sum(pivot[c].values()) for c in cats_in_data}
    cats_sorted = sorted(cats_in_data, key=lambda x: -cat_totals[x])

    def _fmt_cell(v, prev_val):
        """Format a cell as '$amount (▲/▼ ±%)' — hides delta when prev is 0/None."""
        if prev_val is None or prev_val == 0 or v == 0:
            return f"${v:,.0f}"
        d = (v - prev_val) / abs(prev_val) * 100
        # Cap extreme deltas to keep the table readable
        if abs(d) >= 1000:
            return f"${v:,.0f}"
        arrow = "▲" if d > 0 else "▼" if d < 0 else "·"
        return f"${v:,.0f} ({arrow} {d:+.0f}%)"

    # Build the comparison table
    comp_records = []
    for cat in cats_sorted:
        row = {"Category": cat}
        prev_val = None
        for m in months_sorted:
            v = pivot[cat][m]
            row[m] = _fmt_cell(v, prev_val)
            prev_val = v
        comp_records.append(row)

    # Totals row
    totals_row = {"Category": "Σ Total"}
    prev_val = None
    for m in months_sorted:
        v = sum(pivot[c][m] for c in cats_sorted)
        totals_row[m] = _fmt_cell(v, prev_val)
        prev_val = v
    comp_records.append(totals_row)

    comp_df = pd.DataFrame(comp_records)

    # Color the delta arrows: ▲ (increase) → red, ▼ (decrease) → green.
    # Convention: lower spend = good (green), higher spend = bad (red).
    def _color_delta(val):
        s = str(val)
        if "▲" in s:
            return "color: #DC2626; font-weight: 600;"  # red
        if "▼" in s:
            return "color: #16A34A; font-weight: 600;"  # green
        return ""

    styled = comp_df.style.map(_color_delta)
    st.dataframe(styled, use_container_width=True, hide_index=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# 📋 Transaction list
# ---------------------------------------------------------------------------
st.markdown("### 📋 Transactions")

# Sub-filters
ft1, ft2, ft3 = st.columns([2, 2, 2])
with ft1:
    cat_options = ["All categories"] + [r["category"] or "(Uncategorized)" for r in cat_summary]
    sel_cat = st.selectbox("Category", cat_options, key="pay_tx_cat")
with ft2:
    ctry_options_in_data = sorted({r["country"] for r in month_ctry_rows if r.get("country")})
    ctry_options = ["All countries"] + ctry_options_in_data
    sel_ctry = st.selectbox("Country", ctry_options, key="pay_tx_ctry")
with ft3:
    tx_limit = st.selectbox("Show", options=[100, 250, 500, 1000, 5000], index=1, key="pay_tx_limit")

cat_filter = None if sel_cat == "All categories" else (
    None if sel_cat == "(Uncategorized)" else sel_cat
)
ctry_filter = None if sel_ctry == "All countries" else sel_ctry

txs = get_payments(
    start_ym=start_ym, end_ym=end_ym,
    category=cat_filter, country=ctry_filter,
    include_stock=include_stock, limit=tx_limit,
)
if txs:
    tx_df = pd.DataFrame([
        {
            "Date": t.get("payment_date"),
            "Month": t.get("year_month"),
            "Category": t.get("category") or "(Uncategorized)",
            "Country": t.get("country") or "—",
            "Amount": f"${(t.get('amount') or 0):,.2f}",
            "Description": (t.get("description") or "")[:80],
            "Invoice": "✓" if t.get("has_invoice") else "—",
        }
        for t in txs
    ])
    st.dataframe(tx_df, use_container_width=True, hide_index=True)
    st.caption(f"Showing {len(txs)} transactions.")
else:
    st.info("No transactions match the selected filters.")
