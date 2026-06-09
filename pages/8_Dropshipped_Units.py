"""Dropship Orders — orders shipped from China, imported from ERP Excel.

Structured as 3 tabs with `st.fragment` isolation. Each tab's widgets
only rerun their own tab, not the entire page — which means typing in
the search box or changing the period dropdown feels instant instead of
re-rendering every chart on the page.
"""

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
@st.cache_data(ttl=600, show_spinner=False)
def _c_monthly_breakdown():
    return get_dropship_monthly_breakdown()

@st.cache_data(ttl=600, show_spinner=False)
def _c_vs_local_monthly():
    return get_dropship_vs_local_monthly()

@st.cache_data(ttl=600, show_spinner=False)
def _c_available_months():
    return get_dropship_available_months()

@st.cache_data(ttl=600, show_spinner=False)
def _c_sku_breakdown(month):
    return get_dropship_sku_breakdown_for_month(month)

@st.cache_data(ttl=600, show_spinner=False)
def _c_local_vs_dropship_by_sku(start_ym, end_ym):
    return get_local_vs_dropship_by_sku(start_ym, end_ym, limit=500)

@st.cache_data(ttl=600, show_spinner=False)
def _c_local_vs_dropship_summary(start_ym, end_ym):
    return get_local_vs_dropship_summary(start_ym, end_ym)

@st.cache_data(ttl=600, show_spinner=False)
def _c_dropship_summary():
    return get_dropship_summary()

@st.cache_data(ttl=600, show_spinner=False)
def _c_dropship_orders(start_date, end_date, warehouse, country, limit):
    return get_dropship_orders(
        start_date=start_date, end_date=end_date,
        warehouse=warehouse, country=country, limit=limit,
    )

@st.cache_data(ttl=600, show_spinner=False)
def _c_all_dropship_months():
    """All YYYY-MM that have any dropship data. Used to populate the
    Local vs Dropship Period picker. Cached because this runs even when
    the user only changes the period dropdown (a Streamlit full rerun)."""
    from core.database import get_db as _gdb
    with _gdb() as _conn:
        return [
            r["ym"] for r in _conn.execute(
                "SELECT DISTINCT substr(paid_at_local, 1, 7) AS ym "
                "FROM dropship_orders WHERE paid_at_local IS NOT NULL "
                "ORDER BY ym DESC"
            ).fetchall() if r["ym"]
        ]

@st.cache_data(ttl=600, show_spinner=False)
def _c_filter_options(start_iso, end_iso):
    """Tiny DISTINCT-only query to populate warehouse/country dropdowns
    in the Order Detail tab. Replaces a 20k-row fetch."""
    from core.database import get_db as _gdb
    with _gdb() as _conn:
        whs = [
            r["warehouse"] for r in _conn.execute(
                "SELECT DISTINCT warehouse FROM dropship_orders "
                "WHERE paid_at_local >= ? AND paid_at_local <= ? "
                "  AND warehouse IS NOT NULL AND warehouse != '' "
                "ORDER BY warehouse",
                (start_iso, end_iso),
            ).fetchall()
        ]
        ctrys = [
            r["country"] for r in _conn.execute(
                "SELECT DISTINCT country FROM dropship_orders "
                "WHERE paid_at_local >= ? AND paid_at_local <= ? "
                "  AND country IS NOT NULL AND country != '' "
                "ORDER BY country",
                (start_iso, end_iso),
            ).fetchall()
        ]
    return whs, ctrys

page_header(
    "Dropshipped Units",
    "China → US / CA / AU dropshipped units (excl. HI / AK / PR)",
)


# ===========================================================================
# Helper used in two places — format YYYY-MM as 'Jun ‘26' style label.
# ===========================================================================
def _fmt_ym_short(ym):
    try:
        return pd.to_datetime(ym + "-01").strftime("%b ‘%y")
    except Exception:
        return ym


# ===========================================================================
# Region classifier (used in Order Detail tab)
# ===========================================================================
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


# ===========================================================================
# TAB 1: 📈 TRENDS
# Monthly dropshipped units + Dropshipped vs Local Shipping comparison.
# Wrapped in st.fragment so widget interactions inside this tab don't
# re-render the SKU Analysis or Order Detail tabs.
# ===========================================================================
@st.fragment
def _frag_trends():
    st.caption(
        "**Standard dropship rules applied:** China warehouse only · destinations US / CA / AU · "
        "US excludes Hawaii, Alaska, Puerto Rico."
    )

    # --- Monthly trend chart ---
    st.markdown("### 📈 Monthly Dropshipped Units")
    monthly_rows = _c_monthly_breakdown()
    if monthly_rows:
        months = sorted({r["year_month"] for r in monthly_rows})
        pivot = defaultdict(lambda: {c: 0 for c in DROPSHIP_TARGET_COUNTRIES})
        for r in monthly_rows:
            pivot[r["year_month"]][r["country"]] = r["units"]

        trend_records = []
        for m in months:
            row = {"Month": _fmt_ym_short(m)}
            total = 0
            for ctry in DROPSHIP_TARGET_COUNTRIES:
                label = DROPSHIP_TARGET_COUNTRY_LABELS[ctry]
                row[label] = pivot[m][ctry]
                total += pivot[m][ctry]
            row["Total"] = total
            trend_records.append(row)
        trend_df = pd.DataFrame(trend_records)

        plot_df = trend_df.melt(
            id_vars=["Month", "Total"],
            value_vars=["US", "CA", "AU"],
            var_name="Country",
            value_name="Units",
        )
        month_label_order = [_fmt_ym_short(m) for m in months]
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
    if not vs_rows:
        st.info("No data to compare yet.")
        return

    months_seen = sorted({r["year_month"] for r in vs_rows})
    vs_pivot = defaultdict(int)
    for r in vs_rows:
        vs_pivot[(r["year_month"], r["country"], r["origin_type"])] = r["units"] or 0

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
                    "Month": _fmt_ym_short(m),
                    "Dropship (China)": dropship,
                    "Local": local,
                    "Total": total,
                    "% Dropship": f"{pct_dropship:.0f}%",
                })
            vs_df = pd.DataFrame(records)
            vs_df = vs_df[vs_df["Total"] > 0].reset_index(drop=True)

            if vs_df.empty:
                st.info(f"No data for {DROPSHIP_TARGET_COUNTRY_LABELS[country]} in the database.")
                continue

            plot_df = vs_df.melt(
                id_vars=["Month"],
                value_vars=["Dropship (China)", "Local"],
                var_name="Origin",
                value_name="Units",
            )
            label = DROPSHIP_TARGET_COUNTRY_LABELS[country]
            vs_month_order = [_fmt_ym_short(m) for m in months_seen]
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


# ===========================================================================
# TAB 2: 📊 SKU ANALYSIS
# Monthly SKU breakdown + Local vs Dropship by SKU (the section most
# heavily used — its filters live inside this fragment so search/period
# changes don't trigger reruns elsewhere on the page).
# ===========================================================================
@st.fragment
def _frag_sku_analysis():
    # --- Per-month SKU breakdown ---
    st.markdown("### 📊 SKU Breakdown")
    available_months = _c_available_months()
    if available_months:
        sel_month = st.selectbox(
            "Month",
            options=available_months,
            index=0,
            key="ds_month_picker",
        )

        sku_rows = _c_sku_breakdown(sel_month)
        if sku_rows:
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
        "Old/new ERP variants (J11268 ↔ J23267) auto-merge; merged SKUs are "
        "shown joined by ` + ` so you can verify the merge._"
    )

    _all_months = _c_all_dropship_months()
    if not _all_months:
        st.info("No dropship data yet — upload via Data Management → Dropship Upload.")
        return

    def _months_back_lvd(n):
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

    last_completed_ym = _months_back_lvd(1)

    lvd_period_options = ["Last 3 Months", "Last 6 Months", "Last 12 Months"]
    lvd_period_ranges = {
        "Last 3 Months":  (_months_back_lvd(3),  last_completed_ym),
        "Last 6 Months":  (_months_back_lvd(6),  last_completed_ym),
        "Last 12 Months": (_months_back_lvd(12), last_completed_ym),
    }
    for _ym in _all_months:
        _lbl = _fmt_ym(_ym)
        if _lbl not in lvd_period_ranges:
            lvd_period_options.append(_lbl)
            lvd_period_ranges[_lbl] = (_ym, _ym)

    default_lvd_label = _fmt_ym(_all_months[0])
    lvd_period = st.selectbox(
        "Period",
        options=lvd_period_options,
        index=lvd_period_options.index(default_lvd_label),
        key="lvd_period_picker",
    )
    lvd_start_ym, lvd_end_ym = lvd_period_ranges[lvd_period]

    # Per-SKU table — fetched FIRST so we can compute summary KPIs from
    # it without a second Turso round-trip.
    sku_rows = _c_local_vs_dropship_by_sku(lvd_start_ym, lvd_end_ym)

    # Summary KPIs derived from the rows we already have
    local_u = sum((r.get("local_units") or 0) for r in sku_rows)
    drop_u = sum((r.get("dropship_units") or 0) for r in sku_rows)
    total_u = local_u + drop_u
    sku_n = len(sku_rows)
    overall_pct = (drop_u / total_u * 100) if total_u else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("SKUs tracked", f"{sku_n:,}")
    k2.metric("Local units", f"{local_u:,}")
    k3.metric("Dropship units", f"{drop_u:,}")
    k4.metric("Overall Dropship %", f"{overall_pct:.0f}%")

    if not sku_rows:
        st.info(f"No data for {lvd_period}.")
        return

    # Explicit SKU equivalence groups. Each list represents ONE physical
    # product that the warehouse might code under multiple SKUs (old/new
    # naming, alternate codes, etc.). The first SKU in each list is the
    # canonical/display name.
    SKU_EQUIVALENCE_GROUPS = [
        # ─── Nursing pillow: Cream / Yellow line ────────────────────
        ["J11268-newyellow-Set", "J23267-newyellow-Set"],            # Blissful Bundle
        ["J11268-newyellow-small", "J23267-newyellow-small",
         "J11268-newapricot-small", "J23267-SilentVelcro"],           # BMP
        ["J11268-newyellow-large", "J23267-newyellow-large"],        # FBP

        # ─── Turquoise / Blue line ──────────────────────────────────
        ["J11268-newblue-set",   "J23267-newblue-set"],              # Combo turquoise
        ["J11268-newblue-small", "J23267-newblue-small"],            # BMP turquoise
        ["J11268-newblue-large", "J23267-newblue-large"],            # FBP turquoise

        # ─── Peach / Pink line ──────────────────────────────────────
        ["J11268-newpink-set",   "J23267-newpink-set"],              # Combo peach
        ["J11268-newpink-small", "J23267-newpink-small"],            # BMP peach
        ["J11268-newpink-large", "J23267-newpink-large"],            # FBP peach

        # ─── Pillow Covers ──────────────────────────────────────────
        ["J11268-newyellow-small-1", "J23267-newyellow-smallcover"], # BMP Pillow Cover
        ["J11268-newyellow-large-1", "J23267-newyellow-largecover",
         "J11268-pillowcase-FBA"],                                    # FBP Pillow Cover
        ["J11268-newblue-smallcover",  "J23267-newblue-smallcover"], # BMP cover turquoise
        ["J11268-newblue-largecover",  "J23267-newblue-largecover"], # FBP cover turquoise
        ["J11268-newpink-smallcover",  "J23267-newpink-smallcover"], # BMP cover peach
        ["J11268-newpink-largecover",  "J23267-newpink-largecover"], # FBP cover peach

        # ─── Ice Cool line ──────────────────────────────────────────
        ["J11268-ice-Set",          "J23267-ice-Set"],               # Ice Cool Blissful Bundle
        ["J11268-ice-small",        "J23267-ice-small"],             # Ice Cool BMP
        ["J11268-ice-large",        "J23267-ice-large"],             # Ice Cool FBP
        ["J11268-ice-smallcover",   "J23267-ice-smallcover"],        # Ice Cool BMP Pillow Cover
        ["J11268-ice-largecover",   "J23267-ice-largecover"],        # Ice Cool FBP Pillow Cover

        # ─── PPL Postpartum Recovery Leggings (7/8) ─────────────────
        ["108731-blackBB-high7-XS", "108731-Newblack-high-XS"],
        ["108731-blackBB-high7-S",  "108731-Newblack-high-S"],
        ["108731-blackBB-high7-M",  "108731-Newblack-high-M"],
        ["108731-blackBB-high7-L",  "108731-Newblack-high-L"],
        ["108731-blackBB-high7-XL", "108731-Newblack-high-XL"],

        # ─── PPL Postpartum Recovery Leggings (Short) ───────────────
        ["108731-blackBB-highshort-XS", "108731-Newblack-highshort-XS"],
        ["108731-blackBB-highshort-S",  "108731-Newblack-highshort-S"],
        ["108731-blackBB-highshort-M",  "108731-Newblack-highshort-M"],
        ["108731-blackBB-highshort-L",  "108731-Newblack-highshort-L"],
        ["108731-blackBB-highshort-XL", "108731-Newblack-highshort-XL"],

        # ─── Maternity Legging (long) ───────────────────────────────
        ["108731-black-XS", "108731-blackBB-middle-XS", "108731-Newblack-XS"],
        ["108731-black-S",  "108731-blackBB-middle-S",  "108731-Newblack-S"],
        ["108731-black-M",  "108731-blackBB-middle-M",  "108731-Newblack-M"],
        ["108731-black-L",  "108731-blackBB-middle-L",  "108731-Newblack-L"],
        ["108731-black-XL", "108731-blackBB-middle-XL", "108731-Newblack-XL"],

        # ─── Maternity Legging (Short) ──────────────────────────────
        ["108731-blackBB-middleshort-XS", "108731-Newblack-middleshort-XS"],
        ["108731-blackBB-middleshort-S",  "108731-Newblack-middleshort-S"],
        ["108731-blackBB-middleshort-M",  "108731-Newblack-middleshort-M"],
        ["108731-blackBB-middleshort-L",  "108731-Newblack-middleshort-L"],
        ["108731-blackBB-middleshort-XL", "108731-Newblack-middleshort-XL"],
    ]

    _SKU_TO_GROUP = {}
    for _grp in SKU_EQUIVALENCE_GROUPS:
        _canonical = _grp[0]
        for _s in _grp:
            _SKU_TO_GROUP[_s.lower()] = _canonical

    def _canonical_key(sku):
        if sku and sku.lower() in _SKU_TO_GROUP:
            return _SKU_TO_GROUP[sku.lower()]
        return sku

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

    def _drop_emoji(pct):
        if pct >= 50: return "🔴"
        if pct >= 25: return "🟡"
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

    top_n = st.slider(
        "Top N SKUs by Dropship % (chart)",
        min_value=5, max_value=30, value=15, step=5,
        key="lvd_top_n",
    )
    threshold = 10
    chart_rows = [r for r in sku_rows if r["dropship_units"] >= threshold]
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


# ===========================================================================
# TAB 3: 🔍 ORDER DETAIL
# Generic filterable view of every dropship row (all warehouses, all
# destinations). Wrapped in a fragment so changing date filters /
# warehouse / country here doesn't re-render the other two tabs.
# ===========================================================================
@st.fragment
def _frag_order_detail():
    st.markdown("## 🔍 All Dropship Orders")
    st.caption("Generic filterable view of every dropship row (all warehouses, all destinations).")

    all_summary = _c_dropship_summary()
    if all_summary["total_orders"] == 0:
        st.info(
            "No dropship data yet. Go to **Data Management → Dropship Upload** "
            "and upload your ERP Excel."
        )
        return

    today = date.today()
    first_of_this_month = today.replace(day=1)
    default_end = first_of_this_month - timedelta(days=1)
    default_start = default_end.replace(day=1)

    fcol1, fcol2, fcol3 = st.columns([2, 2, 2])
    with fcol1:
        f_start = st.date_input("From", value=default_start, key="ds_filter_start")
    with fcol2:
        f_end = st.date_input("To", value=default_end, key="ds_filter_end")
    with fcol3:
        f_limit = st.selectbox(
            "Show", options=[100, 250, 500, 1000, 2000], index=2, key="ds_limit",
        )

    _whs, _ctrys = _c_filter_options(f_start.isoformat(), f_end.isoformat())
    warehouse_options = ["All warehouses"] + _whs
    country_options = ["All countries"] + _ctrys

    fc1, fc2 = st.columns(2)
    with fc1:
        sel_warehouse = st.selectbox("Warehouse", warehouse_options, key="ds_wh")
    with fc2:
        sel_country = st.selectbox("Destination", country_options, key="ds_ctry")

    wh_filter = None if sel_warehouse == "All warehouses" else sel_warehouse
    ctry_filter = None if sel_country == "All countries" else sel_country

    all_filtered = _c_dropship_orders(
        f_start.isoformat(), f_end.isoformat(),
        wh_filter, ctry_filter, 100000,
    )

    # KPIs
    unique_orders = len({r["order_number"] for r in all_filtered if r.get("order_number")})
    total_units = sum((r.get("quantity") or 0) for r in all_filtered)
    unique_countries = len({r.get("country") for r in all_filtered if r.get("country")})
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

    # Warehouse + Country summaries
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


# ===========================================================================
# Render the 3 top-level tabs. Each tab calls its fragment, so widget
# interactions within a tab don't trigger reruns in the others.
# ===========================================================================
tab_trends, tab_sku, tab_orders = st.tabs([
    "📈 Trends",
    "📊 SKU Analysis",
    "🔍 Order Detail",
])

with tab_trends:
    _frag_trends()

with tab_sku:
    _frag_sku_analysis()

with tab_orders:
    _frag_order_detail()
