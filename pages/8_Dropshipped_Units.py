"""Dropship Orders — orders shipped from China, imported from ERP Excel."""

import streamlit as st
st.set_page_config(layout="wide")

import pandas as pd
from datetime import date, timedelta

from core.database import (
    init_db, get_dropship_orders, get_dropship_summary,
    get_dropship_monthly_breakdown, get_dropship_sku_breakdown_for_month,
    get_dropship_available_months, get_dropship_vs_local_monthly,
    get_local_vs_dropship_by_sku, get_local_vs_dropship_summary,
    DROPSHIP_TARGET_COUNTRIES, DROPSHIP_TARGET_COUNTRY_LABELS,
    DROPSHIP_EXCLUDED_REGIONS,
)
import plotly.express as px
from collections import defaultdict
from core.theme import inject_css, page_header
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

# ---------------------------------------------------------------------------
# Cached wrappers for Turso queries — Turso is in Tokyo so each round-trip
# is ~150ms. Caching means switching periods / typing in search only
# triggers DB calls when params actually change, not on every keystroke.
# ttl=600 — 10 min — long enough to feel instant, short enough that a fresh
# upload becomes visible without manual invalidation.
# ---------------------------------------------------------------------------
import streamlit as _st_cache  # alias to avoid shadowing
@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_monthly_breakdown():
    return get_dropship_monthly_breakdown()

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_vs_local_monthly():
    return get_dropship_vs_local_monthly()

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_available_months():
    return get_dropship_available_months()

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_sku_breakdown(month):
    return get_dropship_sku_breakdown_for_month(month)

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_local_vs_dropship_by_sku(start_ym, end_ym):
    return get_local_vs_dropship_by_sku(start_ym, end_ym, limit=500)

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_local_vs_dropship_summary(start_ym, end_ym):
    return get_local_vs_dropship_summary(start_ym, end_ym)

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_dropship_summary():
    return get_dropship_summary()

@_st_cache.cache_data(ttl=600, show_spinner=False)
def _c_dropship_orders(start_date, end_date, warehouse, country, limit):
    return get_dropship_orders(
        start_date=start_date, end_date=end_date,
        warehouse=warehouse, country=country, limit=limit,
    )

page_header(
    "Dropshipped Units",
    "China → US / CA / AU dropshipped units (excl. HI / AK / PR)",
)

# ---------------------------------------------------------------------------
# Standard dropship view (replicates legacy Excel tracker)
# ---------------------------------------------------------------------------
st.caption(
    "**Standard dropship rules applied:** China warehouse only · destinations US / CA / AU · "
    "US excludes Hawaii, Alaska, Puerto Rico."
)

# --- Monthly trend chart ---
st.markdown("### 📈 Monthly Dropshipped Units")
def _fmt_ym(ym):
    """'2026-05' → 'May ‘26' for discrete category-axis labels."""
    try:
        return pd.to_datetime(ym + "-01").strftime("%b ‘%y")
    except Exception:
        return ym


monthly_rows = _c_monthly_breakdown()
if monthly_rows:
    # Pivot to month × country
    months = sorted({r["year_month"] for r in monthly_rows})
    pivot = defaultdict(lambda: {c: 0 for c in DROPSHIP_TARGET_COUNTRIES})
    for r in monthly_rows:
        pivot[r["year_month"]][r["country"]] = r["units"]

    trend_records = []
    for m in months:
        row = {"Month": _fmt_ym(m)}
        total = 0
        for ctry in DROPSHIP_TARGET_COUNTRIES:
            label = DROPSHIP_TARGET_COUNTRY_LABELS[ctry]
            row[label] = pivot[m][ctry]
            total += pivot[m][ctry]
        row["Total"] = total
        trend_records.append(row)
    trend_df = pd.DataFrame(trend_records)

    # Bar chart (grouped)
    plot_df = trend_df.melt(
        id_vars=["Month", "Total"],
        value_vars=["US", "CA", "AU"],
        var_name="Country",
        value_name="Units",
    )
    month_label_order = [_fmt_ym(m) for m in months]
    fig = px.bar(
        plot_df, x="Month", y="Units", color="Country",
        title="Monthly Dropshipped Units (China → US / CA / AU)",
        barmode="group",
        color_discrete_map={"US": "#1E40AF", "CA": "#DC2626", "AU": "#16A34A"},
        category_orders={
            "Country": ["US", "CA", "AU"],
            "Month": month_label_order,
        },
        text="Units",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(xaxis_type="category")
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(trend_df, use_container_width=True, hide_index=True)
else:
    st.info("No China-shipped orders to US / CA / AU in the database yet.")

st.markdown("---")

# --- Dropshipped vs Local Shipping comparison ---
st.markdown("### 🌐 Dropshipped vs Local Shipping")
st.caption(
    "China (默认仓库 + 东莞爆品仓) vs local warehouses, per destination. "
    "Hawaii / Alaska / Puerto Rico excluded from US."
)

vs_rows = _c_vs_local_monthly()
if vs_rows:
    months_seen = sorted({r["year_month"] for r in vs_rows})

    # Pivot: (year_month, country, origin_type) → units
    vs_pivot = defaultdict(int)
    for r in vs_rows:
        vs_pivot[(r["year_month"], r["country"], r["origin_type"])] = r["units"] or 0

    # One stacked / grouped chart per destination country, in tabs
    dest_tab_labels = [
        f"🇺🇸 US" if c == "United States" else
        f"🇨🇦 CA" if c == "Canada" else
        f"🇦🇺 AU"
        for c in DROPSHIP_TARGET_COUNTRIES
    ]
    dest_tabs = st.tabs(dest_tab_labels)

    for di, country in enumerate(DROPSHIP_TARGET_COUNTRIES):
        with dest_tabs[di]:
            records = []
            for m in months_seen:
                dropship = vs_pivot[(m, country, "Dropship")]
                local = vs_pivot[(m, country, "Local")]
                total = dropship + local
                pct_dropship = (dropship / total * 100) if total else 0
                records.append({
                    "Month": _fmt_ym(m),
                    "Dropship (China)": dropship,
                    "Local": local,
                    "Total": total,
                    "% Dropship": f"{pct_dropship:.0f}%",
                })
            vs_df = pd.DataFrame(records)
            # Skip months with no data for this destination
            vs_df = vs_df[vs_df["Total"] > 0].reset_index(drop=True)

            if vs_df.empty:
                st.info(f"No data for {DROPSHIP_TARGET_COUNTRY_LABELS[country]} in the database.")
                continue

            # Grouped bar chart
            plot_df = vs_df.melt(
                id_vars=["Month"],
                value_vars=["Dropship (China)", "Local"],
                var_name="Origin",
                value_name="Units",
            )
            label = DROPSHIP_TARGET_COUNTRY_LABELS[country]
            vs_month_order = [_fmt_ym(m) for m in months_seen]
            fig_vs = px.bar(
                plot_df, x="Month", y="Units", color="Origin",
                title=f"{label}: Dropshipped vs Local Shipping",
                barmode="group",
                color_discrete_map={"Dropship (China)": "#DC2626", "Local": "#1E40AF"},
                category_orders={
                    "Origin": ["Dropship (China)", "Local"],
                    "Month": vs_month_order,
                },
                text="Units",
            )
            fig_vs.update_traces(textposition="outside")
            fig_vs.update_layout(xaxis_type="category")
            st.plotly_chart(fig_vs, use_container_width=True, key=f"vs_{label}")

            # Append a Total row at the bottom
            totals_row = {
                "Month": "📊 Total",
                "Dropship (China)": int(vs_df["Dropship (China)"].sum()),
                "Local": int(vs_df["Local"].sum()),
                "Total": int(vs_df["Total"].sum()),
            }
            grand_total = totals_row["Total"]
            totals_row["% Dropship"] = (
                f"{(totals_row['Dropship (China)'] / grand_total * 100):.0f}%"
                if grand_total else "0%"
            )
            vs_df_with_total = pd.concat(
                [vs_df, pd.DataFrame([totals_row])], ignore_index=True,
            )
            st.dataframe(vs_df_with_total, use_container_width=True, hide_index=True)
else:
    st.info("No data to compare yet.")

st.markdown("---")

# --- Per-month SKU breakdown ---
st.markdown("### 📊 SKU Breakdown")
available_months = _c_available_months()
if available_months:
    sel_month = st.selectbox(
        "Month",
        options=available_months,
        index=0,  # most recent month first
        key="ds_month_picker",
    )

    sku_rows = _c_sku_breakdown(sel_month)
    if sku_rows:
        # Pivot to one row per SKU with US/CA/AU + Total columns
        sku_pivot = defaultdict(lambda: {"shopify_sku": "", "US": 0, "CA": 0, "AU": 0})
        for r in sku_rows:
            key = r["erp_sku"] or "(no ERP SKU)"
            sku_pivot[key]["shopify_sku"] = r["shopify_sku"] or ""
            label = DROPSHIP_TARGET_COUNTRY_LABELS[r["country"]]
            sku_pivot[key][label] += r["units"] or 0

        sku_records = []
        for erp_sku, data in sku_pivot.items():
            total = data["US"] + data["CA"] + data["AU"]
            sku_records.append({
                "SKU": erp_sku,
                "Shopify SKU": data["shopify_sku"],
                "US": data["US"],
                "CA": data["CA"],
                "AU": data["AU"],
                "Total": total,
            })
        sku_df = pd.DataFrame(sku_records).sort_values("Total", ascending=False).reset_index(drop=True)

        # Append a 📊 Monthly Total row at the bottom (mirrors the Excel format)
        totals_row = {
            "SKU": "📊 Monthly Total",
            "Shopify SKU": "",
            "US": int(sku_df["US"].sum()),
            "CA": int(sku_df["CA"].sum()),
            "AU": int(sku_df["AU"].sum()),
            "Total": int(sku_df["Total"].sum()),
        }
        sku_df_with_total = pd.concat(
            [sku_df, pd.DataFrame([totals_row])], ignore_index=True,
        )

        st.caption(f"**{sel_month}** · {len(sku_df)} SKUs · {totals_row['Total']:,} total units")
        st.dataframe(sku_df_with_total, use_container_width=True, hide_index=True)
    else:
        st.info(f"No China-shipped orders to US/CA/AU for {sel_month}.")
else:
    st.info("Upload dropship data first via Data Management → Dropship Upload.")

st.markdown("---")

# ---------------------------------------------------------------------------
# 📦 Per-SKU Local vs Dropship breakdown
# ---------------------------------------------------------------------------
st.markdown("### 📦 Local vs Dropship by SKU")
st.caption(
    "**Local** = warehouse-home shipping to its home country (US warehouse → US "
    "mainland, CA → CA, AU → AU). "
    "**Dropship** = China origin to anywhere, OR any shipment to Hawaii / Alaska / "
    "Puerto Rico, OR cross-region (e.g. US warehouse → UK). "
    "Use this to spot SKUs that are over-relying on expensive dropship lanes. "
    "_Rows are grouped by **ERP SKU** (the physical item shipped). "
    "The Shopify SKU column is shown for reference only — it sometimes "
    "points to an upsell variant in the ERP data. "
    "Old/new ERP variants (J11268 ↔ J23267) auto-merge; a `(+N)` badge "
    "shows the variant count._"
)

# Month picker — use the broader 'available months from all dropship data'
# (not the standard-rules version)
from core.database import get_db as _gdb
with _gdb() as _conn:
    _all_months = [
        r["ym"] for r in _conn.execute(
            "SELECT DISTINCT substr(paid_at_local, 1, 7) AS ym "
            "FROM dropship_orders WHERE paid_at_local IS NOT NULL "
            "ORDER BY ym DESC"
        ).fetchall() if r["ym"]
    ]

if not _all_months:
    st.info("No dropship data yet — upload via Data Management → Dropship Upload.")
else:
    # Build a dropdown with rolling-window options + each individual month.
    def _months_back_lvd(n):
        """Return YYYY-MM that is n calendar months before today."""
        today = date.today()
        y, m = today.year, today.month - n
        while m <= 0:
            m += 12
            y -= 1
        return f"{y:04d}-{m:02d}"

    def _fmt_ym(ym):
        try:
            return pd.to_datetime(ym + "-01").strftime("%B %Y")
        except Exception:
            return ym

    # Last completed month = the month BEFORE today's month. This way
    # "Last 3 Months" = 3 completed months ending last month, which
    # matches how Shopify and most ecommerce tools report periods.
    last_completed_ym = _months_back_lvd(1)

    lvd_period_options = ["Last 3 Months", "Last 6 Months", "Last 12 Months"]
    lvd_period_ranges = {
        "Last 3 Months":  (_months_back_lvd(3),  last_completed_ym),
        "Last 6 Months":  (_months_back_lvd(6),  last_completed_ym),
        "Last 12 Months": (_months_back_lvd(12), last_completed_ym),
    }
    # Append every individual month with data
    for _ym in _all_months:
        _lbl = _fmt_ym(_ym)
        if _lbl not in lvd_period_ranges:
            lvd_period_options.append(_lbl)
            lvd_period_ranges[_lbl] = (_ym, _ym)

    # Default to the newest month with data
    default_lvd_label = _fmt_ym(_all_months[0])
    lvd_period = st.selectbox(
        "Period",
        options=lvd_period_options,
        index=lvd_period_options.index(default_lvd_label),
        key="lvd_period_picker",
    )
    lvd_start_ym, lvd_end_ym = lvd_period_ranges[lvd_period]

    # Summary KPIs
    summary = _c_local_vs_dropship_summary(lvd_start_ym, lvd_end_ym)
    local_u = summary.get("local_units") or 0
    drop_u = summary.get("dropship_units") or 0
    total_u = summary.get("total_units") or 0
    sku_n = summary.get("sku_count") or 0
    overall_pct = (drop_u / total_u * 100) if total_u else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("SKUs tracked", f"{sku_n:,}")
    k2.metric("Local units", f"{local_u:,}")
    k3.metric("Dropship units", f"{drop_u:,}")
    k4.metric("Overall Dropship %", f"{overall_pct:.0f}%")

    # Per-SKU table
    sku_rows = _c_local_vs_dropship_by_sku(lvd_start_ym, lvd_end_ym)

    if sku_rows:
        # Merge old/new ERP variants that refer to the same physical product.
        # Only the known equivalent prefix pairs are merged — other J-prefix
        # SKUs that happen to share a suffix (e.g. J11268-black, J11939-black,
        # J12570-black are three different products) stay separate.
        import re as _re

        # Known old → new ERP prefix pairs for the SAME product line.
        # Add new equivalencies here as you discover them.
        EQUIVALENT_ERP_PREFIXES = {"J11268", "J23267"}

        def _canonical_key(sku):
            """For SKUs whose prefix is in the equivalence set, return a
            canonical key based on suffix (lowercased). For everything else,
            return the original SKU so it stands alone."""
            m = _re.match(r"^(J\d+)-(.+)$", sku or "")
            if m:
                prefix, suffix = m.group(1), m.group(2)
                if prefix in EQUIVALENT_ERP_PREFIXES:
                    return f"__variant__{suffix.lower()}"
            return sku

        # Group by ERP SKU (canonical) only. The Shopify SKU column shows
        # the dominant Shopify SKU just as a reference label — it's not
        # used for counting because the ERP's 平台SKU often points to an
        # upsell variant that doesn't reflect what was physically shipped.
        buckets = {}
        for r in sku_rows:
            k = _canonical_key(r["erp_sku"])
            if k not in buckets:
                buckets[k] = {
                    "erp_skus": set(),
                    "shopify_sku": r["shopify_sku"],
                    "best_shopify_units": r["total_units"],
                    "local_units": 0,
                    "dropship_units": 0,
                    "total_units": 0,
                }
            buckets[k]["erp_skus"].add(r["erp_sku"])
            # Pick the Shopify SKU coming from the highest-volume ERP variant
            if r["total_units"] > buckets[k]["best_shopify_units"]:
                buckets[k]["shopify_sku"] = r["shopify_sku"]
                buckets[k]["best_shopify_units"] = r["total_units"]
            buckets[k]["local_units"] += r["local_units"]
            buckets[k]["dropship_units"] += r["dropship_units"]
            buckets[k]["total_units"] += r["total_units"]

        sku_rows_view = []
        for k, b in buckets.items():
            total = b["total_units"]
            pct = (b["dropship_units"] / total * 100) if total else 0
            erp_skus_sorted = sorted(b["erp_skus"])
            # Show all merged SKUs joined by ' + ' so the user can verify
            # the merge. For unmerged rows just show the single SKU.
            if len(erp_skus_sorted) > 1:
                erp_display = " + ".join(erp_skus_sorted)
            else:
                erp_display = erp_skus_sorted[0]
            sku_rows_view.append({
                "erp_sku": erp_display,
                "shopify_sku": b["shopify_sku"],
                "local_units": b["local_units"],
                "dropship_units": b["dropship_units"],
                "total_units": total,
                "dropship_pct": pct,
            })
        sku_rows_view.sort(key=lambda r: -r["total_units"])

        # Build display rows with color flagging
        def _drop_emoji(pct):
            if pct >= 50:
                return "🔴"
            if pct >= 25:
                return "🟡"
            return "🟢"

        display = []
        for r in sku_rows_view:
            display.append({
                "": _drop_emoji(r["dropship_pct"]),
                "SKU": r["erp_sku"],
                "Shopify SKU": r["shopify_sku"] or "",
                "Local": r["local_units"],
                "Dropship": r["dropship_units"],
                "Total": r["total_units"],
                "Dropship %": round(r["dropship_pct"], 1),
            })

        display_df = pd.DataFrame(display)

        # SKU search + flag filter
        ff1, ff2 = st.columns([3, 1])
        with ff1:
            sku_query = st.text_input(
                "🔍 Search SKU (matches both ERP SKU and Shopify SKU)",
                value="",
                placeholder="e.g. blackBB-high7-S, J11268, pplegging-full",
                key="lvd_sku_search",
            )
        with ff2:
            flag_filter = st.selectbox(
                "Show",
                options=["All", "🟢 Healthy (<25%)", "🟡 Watch (25-50%)", "🔴 Review (≥50%)"],
                index=0,
                key="lvd_flag_filter",
            )

        # Apply filters
        filtered_df = display_df.copy()
        if sku_query.strip():
            q = sku_query.strip().lower()
            filtered_df = filtered_df[
                filtered_df["SKU"].str.lower().str.contains(q, na=False)
                | filtered_df["Shopify SKU"].str.lower().str.contains(q, na=False)
            ]
        if flag_filter.startswith("🟢"):
            filtered_df = filtered_df[filtered_df["Dropship %"] < 25]
        elif flag_filter.startswith("🟡"):
            filtered_df = filtered_df[
                (filtered_df["Dropship %"] >= 25) & (filtered_df["Dropship %"] < 50)
            ]
        elif flag_filter.startswith("🔴"):
            filtered_df = filtered_df[filtered_df["Dropship %"] >= 50]

        st.markdown(
            "🟢 < 25% (healthy) &nbsp; · &nbsp; 🟡 25–50% (watch) &nbsp; · &nbsp; "
            "🔴 ≥ 50% (review — local stock issue?)"
        )
        st.caption(
            f"Showing **{len(filtered_df)}** of **{len(display_df)}** SKUs"
            + (f" matching '{sku_query}'" if sku_query.strip() else "")
            + (f" · filter: {flag_filter}" if flag_filter != "All" else "")
        )

        st.dataframe(
            filtered_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Dropship %": st.column_config.ProgressColumn(
                    "Dropship %",
                    min_value=0, max_value=100,
                    format="%.0f%%",
                ),
                "Local": st.column_config.NumberColumn(format="%d"),
                "Dropship": st.column_config.NumberColumn(format="%d"),
                "Total": st.column_config.NumberColumn(format="%d"),
            },
            height=600,
        )

        # Top-N chart: SKUs ranked by Dropship %
        top_n = st.slider(
            "Top N SKUs by Dropship % (chart)",
            min_value=5, max_value=30, value=15, step=5,
            key="lvd_top_n",
        )
        # Filter: only SKUs with at least a few units of dropship to avoid
        # cluttering the chart with low-volume oddities.
        threshold = 10
        chart_rows = [
            r for r in sku_rows if r["dropship_units"] >= threshold
        ]
        chart_rows.sort(key=lambda x: -x["dropship_pct"])
        chart_rows = chart_rows[:top_n]

        if chart_rows:
            chart_df = pd.DataFrame([
                {
                    "SKU": r["erp_sku"],
                    "Local": r["local_units"],
                    "Dropship": r["dropship_units"],
                    "Dropship %": r["dropship_pct"],
                }
                for r in chart_rows
            ])
            stacked = chart_df.melt(
                id_vars=["SKU", "Dropship %"],
                value_vars=["Local", "Dropship"],
                var_name="Origin",
                value_name="Units",
            )
            fig_lvd = px.bar(
                stacked,
                x="Units", y="SKU", color="Origin",
                orientation="h",
                title=f"Top {len(chart_rows)} SKUs by Dropship % — {lvd_period}",
                color_discrete_map={"Local": "#1E40AF", "Dropship": "#DC2626"},
                category_orders={
                    "Origin": ["Local", "Dropship"],
                    "SKU": [r["erp_sku"] for r in chart_rows],
                },
            )
            fig_lvd.update_layout(
                height=max(380, 30 * len(chart_rows) + 100),
                yaxis_autorange="reversed",
            )
            st.plotly_chart(fig_lvd, use_container_width=True)
        else:
            st.info(
                f"No SKUs with ≥{threshold} dropship units in {lvd_period} — "
                "nothing notable to chart."
            )
    else:
        st.info(f"No data for {lvd_period}.")

st.markdown("---")
st.markdown("## 🔍 All Dropship Orders")
st.caption("Generic filterable view of every dropship row (all warehouses, all destinations).")

# ---------------------------------------------------------------------------
# Filters (existing generic view)
# ---------------------------------------------------------------------------
all_summary = _c_dropship_summary()
if all_summary["total_orders"] == 0:
    st.info(
        "No dropship data yet. Go to **Data Management → Dropship Upload** "
        "and upload your ERP Excel."
    )
    st.stop()

today = date.today()
# Default to the previous complete month (e.g. in June, show May 1 - May 31)
first_of_this_month = today.replace(day=1)
default_end = first_of_this_month - timedelta(days=1)        # last day of previous month
default_start = default_end.replace(day=1)                   # first day of previous month

fcol1, fcol2, fcol3 = st.columns([2, 2, 2])
with fcol1:
    f_start = st.date_input("From", value=default_start, key="ds_filter_start")
with fcol2:
    f_end = st.date_input("To", value=default_end, key="ds_filter_end")
with fcol3:
    f_limit = st.selectbox(
        "Show", options=[100, 250, 500, 1000, 2000], index=2, key="ds_limit",
    )

# Load summary in range for filter options + KPIs
in_range = _c_dropship_orders(
    f_start.isoformat(), f_end.isoformat(), None, None, 20000,
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
all_filtered = _c_dropship_orders(
    f_start.isoformat(), f_end.isoformat(),
    wh_filter, ctry_filter, 100000,
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
# ---------------------------------------------------------------------------
# Region classification — UK kept separate from EU per ecommerce reporting
# convention. Core markets (US/UK/CA/AU) each get their own slice; the long
# tail is grouped into EU / Asia / Middle East / Latin America / Africa / Other.
# ---------------------------------------------------------------------------
EU_COUNTRIES = {
    "Netherlands", "Ireland", "Germany", "Portugal", "Albania", "Belgium",
    "Czechia", "France", "Italy", "Spain", "Austria", "Finland", "Greece",
    "Switzerland", "Poland", "Malta", "Slovakia", "Luxembourg", "Sweden",
    "Norway", "Denmark", "Hungary", "Romania", "Croatia", "Bulgaria",
    "Serbia", "Ukraine",
}
ASIA_COUNTRIES = {
    "Hong Kong", "Singapore", "Taiwan", "Japan", "South Korea", "Malaysia",
    "Indonesia", "Thailand", "Vietnam", "Philippines", "India", "China",
}
MIDDLE_EAST_COUNTRIES = {
    "Saudi Arabia", "United Arab Emirates", "Israel", "Turkey",
}
LATAM_COUNTRIES = {"Mexico", "Brazil", "Chile", "Argentina", "Colombia", "Peru"}
AFRICA_COUNTRIES = {"South Africa", "Egypt", "Kenya", "Nigeria"}
OCEANIA_OTHER = {"New Zealand"}


def _region_for(country):
    if country == "United States": return "🇺🇸 US"
    if country == "United Kingdom": return "🇬🇧 UK"
    if country == "Canada": return "🇨🇦 CA"
    if country == "Australia": return "🇦🇺 AU"
    if country in EU_COUNTRIES: return "🇪🇺 EU"
    if country in ASIA_COUNTRIES: return "🌏 Asia"
    if country in MIDDLE_EAST_COUNTRIES: return "🕌 Middle East"
    if country in LATAM_COUNTRIES: return "🌎 Latin America"
    if country in AFRICA_COUNTRIES: return "🌍 Africa"
    if country in OCEANIA_OTHER: return "🇳🇿 Oceania (other)"
    return "❓ Other"


# Build all aggregates once
wh_data = defaultdict(lambda: {"orders": set(), "units": 0})
ctry_data = defaultdict(lambda: {"orders": set(), "units": 0, "region": None})
region_data = defaultdict(lambda: {"orders": set(), "units": 0})
for r in all_filtered:
    w = r.get("warehouse") or "Unknown"
    c = r.get("country") or "Unknown"
    reg = _region_for(c)
    qty = r.get("quantity") or 0
    on = r.get("order_number")
    wh_data[w]["orders"].add(on);     wh_data[w]["units"] += qty
    ctry_data[c]["orders"].add(on);   ctry_data[c]["units"] += qty
    ctry_data[c]["region"] = reg
    region_data[reg]["orders"].add(on); region_data[reg]["units"] += qty

# --- Mirrored layout: warehouse + region, each with donut on top, table below ---
wh_total_orders = sum(len(d["orders"]) for d in wh_data.values()) or 1
wh_total_units = sum(d["units"] for d in wh_data.values()) or 1
tot_orders_region = sum(len(d["orders"]) for d in region_data.values()) or 1
tot_units_region = sum(d["units"] for d in region_data.values()) or 1

region_order = [
    "🇺🇸 US", "🇬🇧 UK", "🇨🇦 CA", "🇦🇺 AU",
    "🇪🇺 EU", "🌏 Asia", "🕌 Middle East",
    "🌎 Latin America", "🌍 Africa", "🇳🇿 Oceania (other)",
    "❓ Other",
]

sc1, sc2 = st.columns([1, 1])

with sc1:
    st.markdown("### 🏭 By Warehouse")
    # Donut (Orders share)
    wh_sorted = sorted(wh_data.items(), key=lambda x: -len(x[1]["orders"]))
    wh_donut_df = pd.DataFrame([
        {"Warehouse": w, "Orders": len(d["orders"])}
        for w, d in wh_sorted if len(d["orders"]) > 0
    ])
    fig_wh = px.pie(
        wh_donut_df, names="Warehouse", values="Orders", hole=0.5,
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )
    fig_wh.update_traces(textposition="inside", textinfo="percent+label")
    fig_wh.update_layout(
        height=380, margin=dict(t=20, b=20, l=20, r=20), showlegend=True,
    )
    st.plotly_chart(fig_wh, use_container_width=True)

    # Table below
    wh_df = pd.DataFrame([
        {
            "Warehouse": w,
            "Orders": len(d["orders"]),
            "Orders %": f"{len(d['orders']) / wh_total_orders * 100:.1f}%",
            "Units": d["units"],
            "Units %": f"{d['units'] / wh_total_units * 100:.1f}%",
        }
        for w, d in wh_sorted
    ])
    st.dataframe(wh_df, use_container_width=True, hide_index=True)

with sc2:
    st.markdown("### 🌍 Regional Share")
    # Donut (Orders share)
    region_donut_df = pd.DataFrame([
        {"Region": r, "Orders": len(region_data[r]["orders"])}
        for r in region_order
        if r in region_data and len(region_data[r]["orders"]) > 0
    ])
    fig_region = px.pie(
        region_donut_df, names="Region", values="Orders", hole=0.5,
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig_region.update_traces(textposition="inside", textinfo="percent+label")
    fig_region.update_layout(
        height=380, margin=dict(t=20, b=20, l=20, r=20), showlegend=True,
    )
    st.plotly_chart(fig_region, use_container_width=True)

    # Table below
    region_table = pd.DataFrame([
        {
            "Region": r,
            "Orders": len(region_data[r]["orders"]),
            "Orders %": f"{len(region_data[r]['orders']) / tot_orders_region * 100:.1f}%",
            "Units": region_data[r]["units"],
            "Units %": f"{region_data[r]['units'] / tot_units_region * 100:.1f}%",
        }
        for r in region_order if r in region_data
    ])
    st.dataframe(region_table, use_container_width=True, hide_index=True)

# --- Country drill-down (collapsed by default) ---
with st.expander("📍 Browse by individual country", expanded=False):
    ctry_rows = [
        {
            "Country": c,
            "Region": d["region"],
            "Orders": len(d["orders"]),
            "Orders %": f"{len(d['orders']) / tot_orders_region * 100:.1f}%",
            "Units": d["units"],
            "Units %": f"{d['units'] / tot_units_region * 100:.1f}%",
        }
        for c, d in sorted(ctry_data.items(), key=lambda x: -len(x[1]["orders"]))
    ]
    st.dataframe(pd.DataFrame(ctry_rows), use_container_width=True, hide_index=True)
    st.caption(
        f"{len(ctry_rows)} countries total. "
        "Click the **Region** column header to sort/group by region."
    )

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
