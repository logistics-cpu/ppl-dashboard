"""Product Costs — per-SKU landed/total cost model, history, and margin.

Migrated from the "📦 Product Cost 2026.xlsx" workbook. The dashboard is
the source of truth: manual costs are edited here, raw exports are
uploaded via Data Management, and every component recomputes live.
"""

import streamlit as st
st.set_page_config(layout="wide")

import pandas as pd
import plotly.express as px
from datetime import date

from core.database import init_db, get_setting, set_setting
from core.costs import (
    assemble_cost_table, take_snapshot, get_snapshots, get_snapshot_dates,
    get_freight_averages, get_freight_per_shipment_series, get_shipments,
    get_shipment_lines, add_shipment, delete_shipment,
    get_lastmile_averages, get_lastmile_monthly, get_lastmile_summary,
    get_coverpair_averages,
    get_sku_specs, update_sku_spec, get_rent_brackets, get_rate_card,
    replace_rent_brackets, replace_rate_card,
    get_cost_products, update_cost_product,
    get_margin_revenue, get_actual_rent_per_unit,
    compute_rent_per_unit, compute_inbound_per_unit,
    REGION_US,
)
from core.theme import inject_css, page_header
from core.auth import check_password

if not check_password():
    st.stop()

inject_css()
init_db()

page_header(
    "Product Costs",
    "Per-SKU landed & total cost — US fulfillment · live-computed from "
    "freight, rent, inbound and 3PL billing data",
)

# ---------------------------------------------------------------------------
# Cached wrappers (Turso in Tokyo ≈150ms/round-trip)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _c_cost_table():
    return assemble_cost_table(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_snapshots():
    return get_snapshots(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_snapshot_dates():
    return get_snapshot_dates(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_freight_avgs():
    return get_freight_averages(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_freight_series():
    return get_freight_per_shipment_series(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_shipments():
    return get_shipments(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_lastmile_avgs(start, end):
    return get_lastmile_averages(REGION_US, start, end)

@st.cache_data(ttl=600, show_spinner=False)
def _c_lastmile_monthly(main_sku, order_type):
    return get_lastmile_monthly(REGION_US, main_sku, order_type)

@st.cache_data(ttl=600, show_spinner=False)
def _c_lastmile_summary():
    return get_lastmile_summary(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_specs():
    return get_sku_specs(REGION_US)

@st.cache_data(ttl=600, show_spinner=False)
def _c_products():
    return get_cost_products(REGION_US, include_inactive=True)

@st.cache_data(ttl=600, show_spinner=False)
def _c_margin_revenue(start, end):
    return get_margin_revenue(start, end, "US")

@st.cache_data(ttl=600, show_spinner=False)
def _c_order_months():
    from core.database import get_db as _gdb
    with _gdb() as conn:
        return [
            r["ym"] for r in conn.execute(
                "SELECT DISTINCT substr(created_at_local, 1, 7) AS ym "
                "FROM orders ORDER BY ym DESC"
            ).fetchall() if r["ym"]
        ]


def _clear_caches():
    st.cache_data.clear()


COMPONENTS = [
    ("product_cost", "Product Cost"),
    ("agent_fee", "Agent Fee"),
    ("domestic_freight", "Domestic Freight"),
    ("sea_freight", "Sea Freight"),
    ("warehouse_rent", "Warehouse Rent"),
    ("inbound", "Inbound"),
    ("local_shipping", "Local Shipping"),
    ("pick_pack", "Pick & Pack"),
    ("pink_box", "Pink Box"),
    ("other_box", "Big Box"),
]


def _fmt_money(v):
    return f"${v:,.2f}" if v is not None else "—"


# ===========================================================================
# TAB 1: 💰 Cost Summary
# ===========================================================================
@st.fragment
def _frag_summary():
    table = _c_cost_table()
    if not table:
        st.info(
            "No cost data yet. Seed it from the workbook via "
            "**Data Management → Product Costs**."
        )
        return

    n_products = len(table)
    avg_landed = sum(r["landed_cost"] for r in table) / n_products
    avg_total = sum(r["total_cost"] for r in table) / n_products
    n_missing = sum(1 for r in table if r["missing"])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Products", f"{n_products}")
    k2.metric("Avg Landed Cost", _fmt_money(avg_landed))
    k3.metric("Avg Total Cost", _fmt_money(avg_total))
    k4.metric("Rows w/ missing data", f"{n_missing}", help=(
        "Products where one or more components couldn't be computed "
        "(no freight / spec / order data) and contribute $0. "
        "Check the ⚠️ column below."
    ))

    fc1, fc2, fc3 = st.columns([3, 1, 1])
    with fc1:
        q = st.text_input(
            "🔍 Search product / SKU",
            value="", placeholder="e.g. combo-cream, J11268, legging",
            key="cs_search",
        )
    with fc2:
        cats = ["All"] + sorted({r["category"] for r in table if r["category"]})
        cat = st.selectbox("Category", cats, key="cs_category")
    with fc3:
        st.write("")
        show_skus = st.toggle(
            "Show SKU columns", value=False, key="cs_show_skus",
            help="Show/hide the Shopify SKU and China (ERP) SKU columns. "
                 "Search always matches them either way.",
        )

    rows = table
    if q.strip():
        ql = q.strip().lower()
        rows = [
            r for r in rows
            if ql in (r["shopify_sku"] or "").lower()
            or ql in (r["product_name"] or "").lower()
            or ql in (r["china_sku1"] or "").lower()
            or ql in (r["china_sku2"] or "").lower()
        ]
    if cat != "All":
        rows = [r for r in rows if r["category"] == cat]

    display = []
    for r in rows:
        china_skus = " + ".join(
            s for s in (r["china_sku1"], r["china_sku2"]) if s
        )
        display.append({
            "⚠️": "⚠️ " + ",".join(r["missing"]) if r["missing"] else "",
            "Product": r["product_name"],
            "Shopify SKU": r["display_sku"],
            "China SKUs": china_skus or "—",
            "Product Cost": r["product_cost"],
            "Agent Fee": r["agent_fee"],
            "Domestic": r["domestic_freight"],
            "Sea": r["sea_freight"],
            "Rent": r["warehouse_rent"],
            "Inbound": r["inbound"],
            "Last-mile": r["local_shipping"],
            "Pick&Pack": r["pick_pack"],
            "Pink Box": r["pink_box"],
            "Big Box": r["other_box"],
            "TOTAL": r["total_cost"],
            "LANDED": r["landed_cost"],
        })
    df = pd.DataFrame(display)
    if not show_skus:
        df = df.drop(columns=["Shopify SKU", "China SKUs"])
    money_cols = [
        c for c in df.columns
        if c not in ("⚠️", "Product", "Shopify SKU", "China SKUs")
    ]

    # Color-coding (mirrors the Google Sheet): yellow = manual inputs,
    # blue = auto-computed from data, gray = total, green = landed cost.
    MANUAL_COLS = ["Product Cost", "Agent Fee", "Pick&Pack", "Pink Box", "Big Box"]
    COMPUTED_COLS = ["Domestic", "Sea", "Rent", "Inbound", "Last-mile"]
    styled = (
        df.style
        .format({c: "${:,.2f}" for c in money_cols}, na_rep="—")
        .set_properties(subset=MANUAL_COLS, **{"background-color": "#FEF9C3"})
        .set_properties(subset=COMPUTED_COLS, **{"background-color": "#DBEAFE"})
        .set_properties(subset=["TOTAL"], **{
            "background-color": "#E2E8F0", "font-weight": "bold",
        })
        .set_properties(subset=["LANDED"], **{
            "background-color": "#DCFCE7", "font-weight": "bold",
        })
    )
    st.caption(f"Showing **{len(df)}** of **{len(table)}** products")
    st.dataframe(styled, use_container_width=True, hide_index=True, height=520)
    st.caption(
        "🟨 Manual inputs (edited in ⚙️ Inputs & Settings) · "
        "🟦 Auto-computed from freight / rent / billing data · "
        "🟩 **LANDED** = product + domestic + sea + inbound + pink box "
        "(cost to land in the US warehouse) · "
        "⬜ **TOTAL** = all components — the all-in cost to reach the customer."
    )

    # Stacked component chart
    st.markdown("#### Cost composition")
    cc1, cc2 = st.columns([2, 2])
    with cc1:
        top_n = st.slider("Products to chart (by total cost)", 5, 40, 15, 5, key="cs_topn")
    with cc2:
        chart_mode = st.radio(
            "Group by", ["10 components", "Fixed vs Variable"],
            horizontal=True, key="cs_chart_mode",
        )
    chart_rows = sorted(rows, key=lambda r: -r["total_cost"])[:top_n]
    if chart_rows:
        recs = []
        for r in chart_rows:
            label = (r["product_name"] or r["display_sku"])[:38]
            if chart_mode == "Fixed vs Variable":
                recs.append({"Product": label, "Component": "🔒 Fixed", "$/unit": r["fixed_cost"]})
                recs.append({"Product": label, "Component": "📈 Variable", "$/unit": r["variable_cost"]})
            else:
                for key, name in COMPONENTS:
                    recs.append({"Product": label, "Component": name, "$/unit": r[key]})
        cdf = pd.DataFrame(recs)
        comp_order = (
            ["🔒 Fixed", "📈 Variable"] if chart_mode == "Fixed vs Variable"
            else [n for _, n in COMPONENTS]
        )
        fig = px.bar(
            cdf, x="$/unit", y="Product", color="Component", orientation="h",
            title=f"Cost composition — top {len(chart_rows)} products by total cost",
            category_orders={
                "Product": [(r["product_name"] or r["display_sku"])[:38] for r in chart_rows],
                "Component": comp_order,
            },
            color_discrete_map=(
                {"🔒 Fixed": "#1E40AF", "📈 Variable": "#DC2626"}
                if chart_mode == "Fixed vs Variable" else None
            ),
        )
        fig.update_layout(height=max(420, 32 * len(chart_rows) + 120),
                          yaxis_autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# TAB 2: 📈 Cost Trends
# ===========================================================================
@st.fragment
def _frag_trends():
    st.caption(
        "Variable costs (freight, rent, inbound, last-mile) move as new data "
        "arrives. Snapshots capture the full cost breakdown on every data "
        "change; the raw-data charts below go deeper on freight & last-mile."
    )

    snap_dates = _c_snapshot_dates()
    c1, c2 = st.columns([3, 1])
    with c1:
        if snap_dates:
            st.caption(
                f"**{len(snap_dates)} snapshot days** · latest: "
                f"{snap_dates[0]['snapshot_date']} ({snap_dates[0]['reason']})"
            )
        else:
            st.caption("No snapshots yet.")
    with c2:
        if st.button("📸 Take snapshot now", key="trend_snap_btn"):
            n = take_snapshot(REGION_US, reason="manual")
            _clear_caches()
            st.success(f"Snapshot saved — {n} products")
            st.rerun(scope="fragment")

    snaps = _c_snapshots()
    table = _c_cost_table()
    sku_names = {
        r["shopify_sku"]: f"{(r['product_name'] or '')[:30]} ({r['display_sku']})"
        for r in table
    }

    if snaps:
        st.markdown("#### Snapshot history")
        top_default = [
            r["shopify_sku"] for r in sorted(table, key=lambda x: -x["total_cost"])[:5]
        ]
        sel_skus = st.multiselect(
            "Products",
            options=list(sku_names.keys()),
            default=top_default,
            format_func=lambda s: sku_names.get(s, s),
            key="trend_skus",
        )
        metric_opts = {"Landed cost": "landed_cost", "Total cost": "total_cost"}
        for key, name in COMPONENTS:
            metric_opts[name] = key
        sel_metric = st.selectbox(
            "Metric", options=list(metric_opts.keys()), index=0, key="trend_metric",
            help="Pick a single component (e.g. Sea Freight) to see how just "
                 "that cost moved over time.",
        )
        mkey = metric_opts[sel_metric]

        srows = [s for s in snaps if s["shopify_sku"] in set(sel_skus)]
        if srows:
            sdf = pd.DataFrame([
                {
                    "Date": s["snapshot_date"],
                    "Product": sku_names.get(s["shopify_sku"], s["shopify_sku"]),
                    "$/unit": s[mkey],
                }
                for s in srows
            ])
            fig = px.line(
                sdf, x="Date", y="$/unit", color="Product", markers=True,
                title=f"{sel_metric} over time",
            )
            fig.update_layout(height=420)
            st.plotly_chart(fig, use_container_width=True)

            # Stacked area for one product
            one = st.selectbox(
                "Component breakdown for one product",
                options=sel_skus,
                format_func=lambda s: sku_names.get(s, s),
                key="trend_one_sku",
            )
            one_rows = [s for s in snaps if s["shopify_sku"] == one]
            if len(one_rows) > 0:
                recs = []
                for s in one_rows:
                    for key, name in COMPONENTS:
                        recs.append({
                            "Date": s["snapshot_date"], "Component": name,
                            "$/unit": s[key] or 0,
                        })
                adf = pd.DataFrame(recs)
                fig2 = px.area(
                    adf, x="Date", y="$/unit", color="Component",
                    title=f"Cost composition over time — {sku_names.get(one, one)}",
                    category_orders={"Component": [n for _, n in COMPONENTS]},
                )
                fig2.update_layout(height=420)
                st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Select products above to chart their snapshot history.")

    st.markdown("---")
    st.markdown("#### Raw-data time series (full history)")

    # Last-mile monthly trend
    lm_tab, fr_tab = st.tabs(["🚚 Last-mile monthly", "🚢 Freight per shipment"])
    with lm_tab:
        singles, _pooled = _c_lastmile_avgs(None, None)
        lm_skus = sorted(singles.keys(), key=lambda k: -singles[k]["n_orders"])
        sel_lm = st.multiselect(
            "SKUs (singles orders)",
            options=lm_skus,
            default=lm_skus[:3],
            key="trend_lm_skus",
        )
        recs = []
        for sku in sel_lm:
            for m in _c_lastmile_monthly(sku, None):
                recs.append({
                    "Month": m["ym"], "SKU": sku,
                    "Avg $/order": m["avg_cost"], "Orders": m["n_orders"],
                })
        if recs:
            ldf = pd.DataFrame(recs)
            fig3 = px.line(
                ldf, x="Month", y="Avg $/order", color="SKU", markers=True,
                title="Average last-mile shipping cost per month",
                hover_data=["Orders"],
            )
            fig3.update_layout(height=420, xaxis_type="category")
            st.plotly_chart(fig3, use_container_width=True)

    with fr_tab:
        series = _c_freight_series()
        fr_skus = sorted({r["sku"] for r in series})
        sel_fr = st.multiselect(
            "SKUs", options=fr_skus, default=fr_skus[:3], key="trend_fr_skus",
        )
        recs = []
        for r in series:
            if r["sku"] in set(sel_fr) and (r["dom_per_unit"] or r["sea_per_unit"]):
                label = r["ship_date"] or f"#{r['shipment_id']}"
                recs.append({
                    "Shipment": label, "SKU": r["sku"],
                    "Sea $/unit": r["sea_per_unit"],
                    "Domestic $/unit": r["dom_per_unit"],
                    "Total $/unit": (r["dom_per_unit"] or 0) + (r["sea_per_unit"] or 0),
                })
        if recs:
            fdf = pd.DataFrame(recs)
            fig4 = px.line(
                fdf, x="Shipment", y="Total $/unit", color="SKU", markers=True,
                title="Freight cost per unit, shipment by shipment",
                hover_data=["Sea $/unit", "Domestic $/unit"],
            )
            fig4.update_layout(height=420, xaxis_type="category")
            st.plotly_chart(fig4, use_container_width=True)
            st.caption(
                "Seeded shipments have no dates (the workbook didn't record "
                "them) and show as #shipment-id; new shipments added via the "
                "Freight tab include dates."
            )


# ===========================================================================
# TAB 3: 💵 Margin
# ===========================================================================
@st.fragment
def _frag_margin():
    months = _c_order_months()
    if not months:
        st.info("No synced Shopify orders — run a Shopify sync first.")
        return

    def _months_back(n):
        today = date.today()
        y, m = today.year, today.month - n
        while m <= 0:
            m += 12
            y -= 1
        return f"{y:04d}-{m:02d}"

    last_completed = _months_back(1)
    period_opts = ["Last 3 Months", "Last 6 Months", "Last 12 Months"]
    ranges = {
        "Last 3 Months": (_months_back(3), last_completed),
        "Last 6 Months": (_months_back(6), last_completed),
        "Last 12 Months": (_months_back(12), last_completed),
    }
    for ym in months:
        label = pd.to_datetime(ym + "-01").strftime("%B %Y")
        if label not in ranges:
            period_opts.append(label)
            ranges[label] = (ym, ym)

    sel = st.selectbox("Period (US orders)", period_opts, index=0, key="mg_period")
    start_ym, end_ym = ranges[sel]
    start_date = f"{start_ym}-01"
    end_date = f"{end_ym}-31"

    revenue = _c_margin_revenue(start_date, end_date)
    table = _c_cost_table()
    costs_by_sku = {r["shopify_sku"]: r for r in table}

    recs, matched_rev, total_rev = [], 0.0, 0.0
    for sku, rv in revenue.items():
        total_rev += rv["revenue"] or 0
        c = costs_by_sku.get(sku)
        if not c or not rv["units"]:
            continue
        matched_rev += rv["revenue"] or 0
        asp = rv["revenue"] / rv["units"]
        margin_unit = asp - c["total_cost"]
        recs.append({
            "Product": c["product_name"],
            "Shopify SKU": c["display_sku"],
            "Units": rv["units"],
            "Revenue": rv["revenue"],
            "ASP": asp,
            "Total Cost/u": c["total_cost"],
            "Landed/u": c["landed_cost"],
            "Margin $/u": margin_unit,
            "Margin %": (margin_unit / asp * 100) if asp else 0,
            "Total Margin $": margin_unit * rv["units"],
        })

    if not recs:
        st.warning("No overlap between sold SKUs and cost rows for this period.")
        return

    recs.sort(key=lambda r: -r["Total Margin $"])
    coverage = (matched_rev / total_rev * 100) if total_rev else 0
    tot_margin = sum(r["Total Margin $"] for r in recs)
    tot_units = sum(r["Units"] for r in recs)
    blended = (
        sum(r["Margin %"] * r["Revenue"] for r in recs)
        / sum(r["Revenue"] for r in recs)
    ) if recs else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Revenue covered", f"{coverage:.0f}%", help=(
        "Share of period revenue with a matching cost row. Uncovered SKUs "
        "are listed in the reconciliation expander below."
    ))
    k2.metric("Units (covered)", f"{tot_units:,}")
    k3.metric("Total margin", _fmt_money(tot_margin))
    k4.metric("Blended margin %", f"{blended:.1f}%")

    df = pd.DataFrame(recs)
    st.dataframe(
        df, use_container_width=True, hide_index=True, height=480,
        column_config={
            "Revenue": st.column_config.NumberColumn(format="$%.0f"),
            "ASP": st.column_config.NumberColumn(format="$%.2f"),
            "Total Cost/u": st.column_config.NumberColumn(format="$%.2f"),
            "Landed/u": st.column_config.NumberColumn(format="$%.2f"),
            "Margin $/u": st.column_config.NumberColumn(format="$%.2f"),
            "Margin %": st.column_config.ProgressColumn(
                "Margin %", min_value=0, max_value=100, format="%.0f%%",
            ),
            "Total Margin $": st.column_config.NumberColumn(format="$%.0f"),
        },
    )

    top = df.head(25)
    fig = px.bar(
        top, x="Margin %", y="Product", orientation="h",
        color="Margin %", color_continuous_scale=["#DC2626", "#F59E0B", "#16A34A"],
        title=f"Margin % — top {len(top)} products by total margin ({sel})",
        hover_data=["Units", "ASP", "Total Cost/u"],
    )
    fig.update_layout(height=max(420, 28 * len(top) + 120), yaxis_autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)

    # Reconciliation
    sold_no_cost = [
        {"Shopify SKU": sku, "Units": rv["units"], "Revenue": rv["revenue"]}
        for sku, rv in sorted(revenue.items(), key=lambda kv: -(kv[1]["revenue"] or 0))
        if sku not in costs_by_sku
    ]
    sold_skus = set(revenue.keys())
    cost_never_sold = [
        {"Product": r["product_name"], "Shopify SKU": r["display_sku"]}
        for r in table if r["shopify_sku"] not in sold_skus
    ]
    with st.expander(f"⚠️ Sold but no cost row ({len(sold_no_cost)} SKUs)"):
        if sold_no_cost:
            st.dataframe(
                pd.DataFrame(sold_no_cost), use_container_width=True,
                hide_index=True,
                column_config={"Revenue": st.column_config.NumberColumn(format="$%.0f")},
            )
        else:
            st.caption("None 🎉")
    with st.expander(f"Cost rows with no sales in period ({len(cost_never_sold)})"):
        if cost_never_sold:
            st.dataframe(pd.DataFrame(cost_never_sold), use_container_width=True, hide_index=True)


# ===========================================================================
# TAB 4: 🚢 Freight
# ===========================================================================
@st.fragment
def _frag_freight():
    shipments = _c_shipments()
    st.markdown("#### Shipments")
    if shipments:
        sdf = pd.DataFrame([
            {
                "Shipment": s["shipment_id"],
                "Date": s["ship_date"] or "—",
                "SKUs": s["n_skus"],
                "Units": s["total_qty"],
                "Domestic $": s["dom_total"],
                "Sea $": s["sea_total"],
                "Notes": s["notes"] or "",
            }
            for s in shipments
        ])
        st.dataframe(
            sdf, use_container_width=True, hide_index=True,
            column_config={
                "Domestic $": st.column_config.NumberColumn(format="$%.2f"),
                "Sea $": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
    else:
        st.info("No freight shipments yet — add one below or seed from the workbook.")

    st.markdown("#### Per-SKU freight averages")
    st.caption(
        "Simple mean of per-shipment $/unit. Domestic is allocated by CBM "
        "share within a shipment; sea freight by volumetric-weight share."
    )
    fa = _c_freight_avgs()
    if fa:
        fdf = pd.DataFrame([
            {
                "SKU": sku,
                "Avg Domestic $/u": d["avg_dom"],
                "Avg Sea $/u": d["avg_sea"],
                "Avg Total $/u": d["avg_total"],
                "Min": d["min_total"],
                "Max": d["max_total"],
                "Shipments": d["n_shipments"],
                "Units": d["total_qty"],
            }
            for sku, d in sorted(fa.items())
        ])
        money = ["Avg Domestic $/u", "Avg Sea $/u", "Avg Total $/u", "Min", "Max"]
        st.dataframe(
            fdf, use_container_width=True, hide_index=True, height=400,
            column_config={c: st.column_config.NumberColumn(format="$%.4f") for c in money},
        )

    st.markdown("---")
    st.markdown("#### ➕ Add a freight shipment")
    st.caption(
        "Enter the shipment totals and its SKU/qty lines — per-unit costs "
        "and SKU averages recompute automatically, and a cost snapshot is taken."
    )
    specs = _c_specs()
    spec_skus = sorted(s["sku"] for s in specs)

    hc1, hc2, hc3, hc4 = st.columns([2, 2, 2, 2])
    with hc1:
        new_sid = st.text_input("Shipment ID", key="fr_new_sid")
    with hc2:
        new_date = st.date_input("Ship date", value=date.today(), key="fr_new_date")
    with hc3:
        new_dom = st.number_input("Domestic total $", min_value=0.0, step=10.0, key="fr_new_dom")
    with hc4:
        new_sea = st.number_input("Sea total $", min_value=0.0, step=10.0, key="fr_new_sea")

    lines_df = st.data_editor(
        pd.DataFrame({"SKU": pd.Series(dtype="str"), "Qty": pd.Series(dtype="int")}),
        num_rows="dynamic",
        use_container_width=True,
        key="fr_new_lines",
        column_config={
            "SKU": st.column_config.SelectboxColumn("SKU", options=spec_skus, required=True),
            "Qty": st.column_config.NumberColumn("Qty", min_value=1, step=1, required=True),
        },
    )
    if st.button("Add shipment", type="primary", key="fr_add_btn"):
        lines = [
            {"sku": r["SKU"], "qty": int(r["Qty"])}
            for _, r in lines_df.iterrows()
            if r.get("SKU") and pd.notna(r.get("Qty"))
        ]
        if not new_sid.strip():
            st.error("Shipment ID is required.")
        elif not lines:
            st.error("Add at least one SKU/qty line.")
        else:
            add_shipment(
                new_sid.strip(), new_date.isoformat(),
                float(new_dom), float(new_sea), lines,
            )
            take_snapshot(REGION_US, reason="upload_freight")
            _clear_caches()
            st.success(f"Shipment {new_sid} added ({len(lines)} lines) — costs refreshed.")
            st.rerun(scope="fragment")

    with st.expander("🗑️ Delete a shipment"):
        if shipments:
            del_sid = st.selectbox(
                "Shipment", [s["shipment_id"] for s in shipments], key="fr_del_sid",
            )
            if st.button("Delete shipment", key="fr_del_btn"):
                delete_shipment(del_sid)
                take_snapshot(REGION_US, reason="upload_freight")
                _clear_caches()
                st.success(f"Shipment {del_sid} deleted.")
                st.rerun(scope="fragment")


# ===========================================================================
# TAB 5: 🏭 Rent & Inbound
# ===========================================================================
@st.fragment
def _frag_rent_inbound():
    specs = _c_specs()
    brackets = get_rent_brackets(REGION_US)
    rate_card = get_rate_card(REGION_US)
    unload = float(get_setting("cost_us_unload_rate_per_cbm", "6.2"))
    rent_method = get_setting("cost_us_rent_method", "assumed")
    rent_window = int(float(get_setting("cost_us_rent_window_months", "3")))

    st.caption(
        "**Assumed rent $/unit** = unit CBM × Σ(days in age bracket × rate) "
        "over assumed storage days. "
        "**Actual rent $/unit** = rent the 3PL actually billed for the SKU "
        f"over the last {rent_window} months of rent data ÷ units shipped "
        "(from the dropship uploads). "
        "**Inbound $/unit** = weight-tier op fee + unit CBM × "
        f"${unload:.2f}/CBM unload rate."
    )

    # ── Rent method picker ─────────────────────────────────────────────
    mp1, mp2, mp3 = st.columns([2, 1, 1])
    with mp1:
        sel_method = st.radio(
            "Official rent method (feeds Total / Landed cost)",
            options=["assumed", "actual"],
            format_func=lambda m: (
                "📐 Assumed (CBM × days × rates)" if m == "assumed"
                else "🧾 Actual billed (falls back to assumed if no history)"
            ),
            index=0 if rent_method == "assumed" else 1,
            horizontal=True,
            key="ri_method",
        )
    with mp2:
        sel_window = st.number_input(
            "Window (months)", min_value=1, max_value=12,
            value=rent_window, key="ri_window",
        )
    with mp3:
        st.write("")
        if st.button("💾 Apply", key="ri_method_save"):
            set_setting("cost_us_rent_method", sel_method)
            set_setting("cost_us_rent_window_months", int(sel_window))
            take_snapshot(REGION_US, reason="rates")
            _clear_caches()
            st.success("Rent method updated — costs recomputed, snapshot taken.")
            st.rerun(scope="fragment")

    actual = get_actual_rent_per_unit(REGION_US, rent_window)
    if not actual:
        st.info(
            "No actual rent data yet — upload a storage rent export "
            "(仓租费) via **Data Management → Product Costs** to see "
            "actual vs assumed side by side."
        )

    rows = []
    for s in specs:
        rent_assumed = None
        if s["in_rent_table"]:
            cbm = s["rent_unit_cbm"] or s["unit_cbm"]
            rent_assumed = compute_rent_per_unit(
                cbm, s["assumed_storage_days"], brackets,
            ) if cbm else None
        a = actual.get(s["sku"])
        rent_actual = a["per_unit"] if a else None
        inbound = None
        if s["in_sku_master"] and s["unit_cbm"] is not None and s["unit_weight_kg"] is not None:
            inbound = compute_inbound_per_unit(
                s["unit_weight_kg"], s["unit_cbm"], rate_card, unload,
            )
        official = None
        if rent_method == "actual" and rent_actual is not None:
            official = "🧾 actual"
        elif rent_assumed is not None:
            official = "📐 assumed"
        rows.append({
            "SKU": s["sku"],
            "Unit CBM": s["unit_cbm"],
            "Unit Wt (kg)": s["unit_weight_kg"],
            "Storage days": s["assumed_storage_days"],
            "Assumed $/u": rent_assumed,
            "Actual $/u": rent_actual,
            "Δ Actual−Assumed": (
                rent_actual - rent_assumed
                if rent_actual is not None and rent_assumed is not None else None
            ),
            "Official": official or "—",
            "Rent billed": a["rent_billed"] if a else None,
            "Units shipped": a["units_shipped"] if a else None,
            "Inbound $/u": inbound,
        })
    df = pd.DataFrame(rows)
    q = st.text_input("🔍 Search SKU", "", key="ri_search")
    if q.strip():
        df = df[df["SKU"].str.contains(q.strip().upper(), na=False)]
    st.dataframe(
        df, use_container_width=True, hide_index=True, height=440,
        column_config={
            "Assumed $/u": st.column_config.NumberColumn(format="$%.4f"),
            "Actual $/u": st.column_config.NumberColumn(format="$%.4f"),
            "Δ Actual−Assumed": st.column_config.NumberColumn(format="$%.4f"),
            "Rent billed": st.column_config.NumberColumn(format="$%.2f"),
            "Inbound $/u": st.column_config.NumberColumn(format="$%.4f"),
        },
    )

    # Comparison chart: SKUs with both values
    both = [
        r for r in rows
        if r["Assumed $/u"] is not None and r["Actual $/u"] is not None
    ]
    if both:
        both.sort(key=lambda r: -(r["Actual $/u"] or 0))
        cdf = pd.DataFrame([
            {"SKU": r["SKU"], "Method": "📐 Assumed", "$/unit": r["Assumed $/u"]}
            for r in both[:20]
        ] + [
            {"SKU": r["SKU"], "Method": "🧾 Actual", "$/unit": r["Actual $/u"]}
            for r in both[:20]
        ])
        fig = px.bar(
            cdf, x="$/unit", y="SKU", color="Method", barmode="group",
            orientation="h",
            title=f"Assumed vs actual rent $/unit — top {min(len(both), 20)} SKUs by actual",
            color_discrete_map={"📐 Assumed": "#1E40AF", "🧾 Actual": "#DC2626"},
        )
        fig.update_layout(
            height=max(420, 34 * min(len(both), 20) + 120),
            yaxis_autorange="reversed",
        )
        st.plotly_chart(fig, use_container_width=True)

    rc1, rc2 = st.columns(2)
    with rc1:
        st.markdown("##### Rent age brackets ($/CBM/day)")
        st.dataframe(
            pd.DataFrame([
                {
                    "From day": b["start_day"],
                    "To day": b["end_day"] if b["end_day"] is not None else "∞",
                    "Rate": b["rate_per_cbm_day"],
                }
                for b in brackets
            ]),
            use_container_width=True, hide_index=True,
        )
    with rc2:
        st.markdown("##### Inbound op-fee weight tiers")
        st.dataframe(
            pd.DataFrame([
                {
                    "From kg": t["tier_start_kg"],
                    "To kg": t["tier_end_kg"] if t["tier_end_kg"] is not None else "∞",
                    "Op fee $": t["op_fee"],
                }
                for t in rate_card
            ]),
            use_container_width=True, hide_index=True,
        )
    st.caption("Edit rates and storage days in ⚙️ Inputs & Settings.")


# ===========================================================================
# TAB 6: 🚚 Last-mile
# ===========================================================================
@st.fragment
def _frag_lastmile():
    summary = _c_lastmile_summary()
    if not summary.get("n"):
        st.info(
            "No last-mile billing data yet — upload it via "
            "**Data Management → Product Costs**."
        )
        return

    window = st.selectbox(
        "Averaging window",
        ["All time (matches workbook)", "Last 3 months", "Last 6 months", "Last 12 months"],
        index=0, key="lm_window",
    )
    start = None
    if window != "All time (matches workbook)":
        n = int(window.split()[1])
        today = date.today()
        y, m = today.year, today.month - n
        while m <= 0:
            m += 12
            y -= 1
        start = f"{y:04d}-{m:02d}-01"

    singles, pooled = _c_lastmile_avgs(start, None)
    coverpairs = get_coverpair_averages(REGION_US, start, None)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Orders in history", f"{summary['n']:,}")
    k2.metric("Bundle+bag avg (B)", _fmt_money(pooled.get("TYPE_B")))
    k3.metric("Cozy bundle avg (C)", _fmt_money(pooled.get("TYPE_C")))
    k4.metric("Unclassified orders", f"{summary.get('n_other') or 0:,}", help=(
        "Orders whose SKU combination doesn't match a known single/bundle "
        "pattern. They're excluded from the averages."
    ))
    if coverpairs:
        st.caption(
            "Cover-pair averages: " + " · ".join(
                f"{tok}: {_fmt_money(v)}" for tok, v in sorted(coverpairs.items())
            )
        )

    st.markdown("#### Per-SKU averages (single-SKU orders)")
    q = st.text_input("🔍 Search SKU", "", key="lm_search")
    rows = [
        {
            "SKU": sku,
            "Avg $/order": d["avg_cost"],
            "Orders": d["n_orders"],
            "Min": d["min_cost"],
            "Max": d["max_cost"],
        }
        for sku, d in sorted(singles.items(), key=lambda kv: -kv[1]["n_orders"])
    ]
    if q.strip():
        rows = [r for r in rows if q.strip().upper() in r["SKU"]]
    st.dataframe(
        pd.DataFrame(rows), use_container_width=True, hide_index=True, height=420,
        column_config={
            "Avg $/order": st.column_config.NumberColumn(format="$%.2f"),
            "Min": st.column_config.NumberColumn(format="$%.2f"),
            "Max": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    st.markdown("#### Monthly trend (all orders)")
    monthly_all = _c_lastmile_monthly(None, None)
    if monthly_all:
        mdf = pd.DataFrame(monthly_all)
        fig = px.bar(
            mdf, x="ym", y="avg_cost", text="n_orders",
            title="Average last-mile cost per order, by ship month",
            labels={"ym": "Month", "avg_cost": "Avg $/order", "n_orders": "Orders"},
        )
        fig.update_traces(texttemplate="%{text:,} orders", textposition="outside")
        fig.update_layout(height=400, xaxis_type="category")
        st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# TAB 7: ⚙️ Inputs & Settings
# ===========================================================================
@st.fragment
def _frag_inputs():
    st.markdown("#### Manual cost inputs")
    st.caption(
        "Edit product cost, fees and packaging directly — changes save to "
        "the database, refresh the cost table, and take a snapshot. "
        "Override columns force a computed component to a fixed value "
        "(used by composite bundles); clear an override to resume "
        "auto-calculation."
    )
    products = _c_products()
    if not products:
        st.info("No cost products yet — seed from the workbook first.")
        return

    pdf = pd.DataFrame([
        {
            "id": p["id"],
            "Product": p["product_name"],
            "Shopify SKU": p["display_sku"],
            "China SKU 1": p["china_sku1"],
            "China SKU 2": p["china_sku2"],
            "Product $": p["product_cost"],
            "Agent $": p["agent_fee"],
            "Pick&Pack $": p["pick_pack"],
            "Pink Box $": p["pink_box"],
            "Big Box $": p["other_box"],
            "Ovr Domestic": p["domestic_override"],
            "Ovr Sea": p["sea_override"],
            "Ovr Rent": p["rent_override"],
            "Ovr Inbound": p["inbound_override"],
            "Ovr Last-mile": p["lastmile_override"],
            "LM group": p["lastmile_group"],
            "Active": bool(p["active"]),
        }
        for p in products
    ])

    edited = st.data_editor(
        pdf,
        use_container_width=True,
        hide_index=True,
        height=480,
        key="inp_products_editor",
        disabled=["id", "Product", "Shopify SKU"],
        column_config={
            "id": None,  # hidden
            "LM group": st.column_config.SelectboxColumn(
                "LM group",
                options=["SINGLE", "TYPE_B", "TYPE_C", "WITHBAG",
                         "COVERPAIR:ICE", "COVERPAIR:NEWYELLOW",
                         "COVERPAIR:NEWBLUE", "COVERPAIR:NEWPINK"],
            ),
        },
    )

    _FIELD_MAP = {
        "China SKU 1": "china_sku1", "China SKU 2": "china_sku2",
        "Product $": "product_cost", "Agent $": "agent_fee",
        "Pick&Pack $": "pick_pack", "Pink Box $": "pink_box",
        "Big Box $": "other_box",
        "Ovr Domestic": "domestic_override", "Ovr Sea": "sea_override",
        "Ovr Rent": "rent_override", "Ovr Inbound": "inbound_override",
        "Ovr Last-mile": "lastmile_override",
        "LM group": "lastmile_group", "Active": "active",
    }

    if st.button("💾 Save cost edits", type="primary", key="inp_save_btn"):
        orig = {r["id"]: r for r in pdf.to_dict("records")}
        n_changed = 0
        for r in edited.to_dict("records"):
            o = orig.get(r["id"])
            if not o:
                continue
            changes = {}
            for col, field in _FIELD_MAP.items():
                new_v, old_v = r.get(col), o.get(col)
                if pd.isna(new_v) if isinstance(new_v, float) else new_v is None:
                    new_v = None
                if pd.isna(old_v) if isinstance(old_v, float) else old_v is None:
                    old_v = None
                if new_v != old_v:
                    changes[field] = (
                        int(new_v) if field == "active" and new_v is not None
                        else new_v
                    )
            if changes:
                update_cost_product(r["id"], changes)
                n_changed += 1
        if n_changed:
            take_snapshot(REGION_US, reason="edit")
            _clear_caches()
            st.success(f"Saved {n_changed} product(s) — cost table refreshed, snapshot taken.")
            st.rerun(scope="fragment")
        else:
            st.info("No changes to save.")

    st.markdown("---")
    st.markdown("#### Storage days per SKU")
    st.caption(
        "Assumed days each SKU sits in the warehouse before sale — drives "
        "the rent calculation. Default 90 days."
    )
    specs = _c_specs()
    sdf = pd.DataFrame([
        {
            "id": s["id"], "SKU": s["sku"],
            "Unit CBM": s["unit_cbm"], "Unit Wt (kg)": s["unit_weight_kg"],
            "Rent CBM": s["rent_unit_cbm"],
            "Storage days": s["assumed_storage_days"],
        }
        for s in specs
    ])
    edited_specs = st.data_editor(
        sdf, use_container_width=True, hide_index=True, height=360,
        key="inp_specs_editor",
        disabled=["id", "SKU"],
        column_config={"id": None},
    )
    if st.button("💾 Save spec edits", key="inp_specs_save"):
        orig = {r["id"]: r for r in sdf.to_dict("records")}
        n_changed = 0
        for r in edited_specs.to_dict("records"):
            o = orig.get(r["id"])
            if not o:
                continue
            changes = {}
            for col, field in [
                ("Unit CBM", "unit_cbm"), ("Unit Wt (kg)", "unit_weight_kg"),
                ("Rent CBM", "rent_unit_cbm"), ("Storage days", "assumed_storage_days"),
            ]:
                new_v, old_v = r.get(col), o.get(col)
                if pd.isna(new_v) if isinstance(new_v, float) else new_v is None:
                    new_v = None
                if pd.isna(old_v) if isinstance(old_v, float) else old_v is None:
                    old_v = None
                if new_v != old_v:
                    changes[field] = new_v
            if changes:
                update_sku_spec(r["id"], changes)
                n_changed += 1
        if n_changed:
            take_snapshot(REGION_US, reason="edit")
            _clear_caches()
            st.success(f"Saved {n_changed} SKU spec(s).")
            st.rerun(scope="fragment")
        else:
            st.info("No changes to save.")

    st.markdown("---")
    st.markdown("#### Rate tables & global settings")
    rt1, rt2 = st.columns(2)
    with rt1:
        st.markdown("##### Rent age brackets")
        brackets = get_rent_brackets(REGION_US)
        bdf = pd.DataFrame([
            {"From day": b["start_day"], "To day": b["end_day"],
             "$/CBM/day": b["rate_per_cbm_day"]}
            for b in brackets
        ])
        edited_b = st.data_editor(
            bdf, num_rows="dynamic", use_container_width=True,
            hide_index=True, key="inp_brackets_editor",
        )
        if st.button("💾 Save brackets", key="inp_brackets_save"):
            rows = [
                {
                    "region": REGION_US,
                    "start_day": float(r["From day"]),
                    "end_day": float(r["To day"]) if pd.notna(r["To day"]) else None,
                    "rate_per_cbm_day": float(r["$/CBM/day"]),
                }
                for _, r in edited_b.iterrows()
                if pd.notna(r["From day"]) and pd.notna(r["$/CBM/day"])
            ]
            replace_rent_brackets(rows)
            take_snapshot(REGION_US, reason="rates")
            _clear_caches()
            st.success("Rent brackets saved.")
            st.rerun(scope="fragment")

    with rt2:
        st.markdown("##### Inbound weight tiers")
        tiers = get_rate_card(REGION_US)
        tdf = pd.DataFrame([
            {"From kg": t["tier_start_kg"], "To kg": t["tier_end_kg"],
             "Op fee $": t["op_fee"]}
            for t in tiers
        ])
        edited_t = st.data_editor(
            tdf, num_rows="dynamic", use_container_width=True,
            hide_index=True, key="inp_tiers_editor",
        )
        if st.button("💾 Save tiers", key="inp_tiers_save"):
            rows = [
                {
                    "region": REGION_US,
                    "tier_start_kg": float(r["From kg"]),
                    "tier_end_kg": float(r["To kg"]) if pd.notna(r["To kg"]) else None,
                    "op_fee": float(r["Op fee $"]),
                }
                for _, r in edited_t.iterrows()
                if pd.notna(r["From kg"]) and pd.notna(r["Op fee $"])
            ]
            replace_rate_card(rows)
            take_snapshot(REGION_US, reason="rates")
            _clear_caches()
            st.success("Weight tiers saved.")
            st.rerun(scope="fragment")

    sc1, sc2 = st.columns(2)
    with sc1:
        unload = st.number_input(
            "Unload rate ($/CBM)",
            value=float(get_setting("cost_us_unload_rate_per_cbm", "6.2")),
            min_value=0.0, step=0.1, key="inp_unload",
        )
    with sc2:
        days = st.number_input(
            "Default storage days",
            value=int(float(get_setting("cost_us_default_storage_days", "90"))),
            min_value=0, step=30, key="inp_days",
        )
    if st.button("💾 Save settings", key="inp_settings_save"):
        set_setting("cost_us_unload_rate_per_cbm", unload)
        set_setting("cost_us_default_storage_days", days)
        take_snapshot(REGION_US, reason="rates")
        _clear_caches()
        st.success("Settings saved.")
        st.rerun(scope="fragment")


# ===========================================================================
# Render tabs
# ===========================================================================
(
    tab_summary, tab_trends, tab_margin, tab_freight,
    tab_rent, tab_lastmile, tab_inputs,
) = st.tabs([
    "💰 Cost Summary",
    "📈 Cost Trends",
    "💵 Margin",
    "🚢 Freight",
    "🏭 Rent & Inbound",
    "🚚 Last-mile",
    "⚙️ Inputs & Settings",
])

with tab_summary:
    _frag_summary()
with tab_trends:
    _frag_trends()
with tab_margin:
    _frag_margin()
with tab_freight:
    _frag_freight()
with tab_rent:
    _frag_rent_inbound()
with tab_lastmile:
    _frag_lastmile()
with tab_inputs:
    _frag_inputs()
