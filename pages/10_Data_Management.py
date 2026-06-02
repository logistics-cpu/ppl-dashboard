"""Data Management page — Shopify sync, ERP upload, shipment tracking, settings."""

import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from datetime import date, datetime
from core.config import STYLES, COLORS, SIZES, ALL_STYLES, get_colors, get_sizes, WAREHOUSE_DISPLAY_NAMES
from core.database import (
    init_db, get_setting, set_setting, log_sync,
    insert_inventory_snapshot,
    add_production_arrival, get_production_arrivals, delete_production_arrival,
    add_warehouse_transfer, get_warehouse_transfers, delete_warehouse_transfer,
    upsert_weekly_sales, get_last_sync,
    clear_all_sales, clear_all_inventory, clear_all_data,
    get_unmapped_raw_skus, derive_weekly_sales_from_raw,
)
from erp.parser import parse_erp_excel
from core.auth import check_password
from core.theme import inject_css, page_header

if not check_password():
    st.stop()

inject_css()
init_db()
page_header("Data Management", "Sync, upload, and manage your data sources")

# ---------------------------------------------------------------------------
# Custom styling for this page
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Tab styling */
    div[data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 2px solid #E2E8F0;
        padding-bottom: 0;
    }
    div[data-baseweb="tab-list"] button {
        font-weight: 600;
        font-size: 0.9rem;
        padding: 0.6rem 1.2rem;
        border-radius: 8px 8px 0 0;
        color: #475569;
        border: none;
        background: transparent;
    }
    div[data-baseweb="tab-list"] button[aria-selected="true"] {
        color: #1E40AF;
        border-bottom: 3px solid #1E40AF;
        background: #EFF6FF;
    }

    /* Section card wrapper */
    .dm-section {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1.25rem;
    }
    .dm-section-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #1E3A5F;
        margin-bottom: 0.25rem;
    }
    .dm-section-desc {
        font-size: 0.85rem;
        color: #475569;
        margin-bottom: 1rem;
        line-height: 1.5;
    }

    /* Sync status badges */
    .sync-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 0.45rem 0.9rem;
        border-radius: 8px;
        font-size: 0.82rem;
        font-weight: 600;
        margin-bottom: 1rem;
    }
    .sync-badge.success {
        background: #F0FDF4;
        color: #166534;
        border: 1px solid #BBF7D0;
    }
    .sync-badge.pending {
        background: #EFF6FF;
        color: #1E40AF;
        border: 1px solid #BFDBFE;
    }

    /* Metric cards for ERP summary */
    .metric-row {
        display: flex;
        gap: 12px;
        margin-bottom: 1rem;
    }
    .metric-card {
        flex: 1;
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        text-align: center;
    }
    .metric-card .label {
        font-size: 0.75rem;
        font-weight: 600;
        color: #475569;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-card .value {
        font-size: 1.6rem;
        font-weight: 800;
        color: #1E40AF;
        margin-top: 2px;
    }

    /* Settings card */
    .setting-item {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 1.25rem;
        margin-bottom: 0.75rem;
    }
    .setting-label {
        font-size: 0.9rem;
        font-weight: 600;
        color: #1E3A5F;
        margin-bottom: 0.15rem;
    }
    .setting-help {
        font-size: 0.78rem;
        color: #64748B;
        margin-bottom: 0.75rem;
    }

    /* Delete row styling */
    .delete-zone {
        background: #FFF7ED;
        border: 1px dashed #FDBA74;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        margin-top: 1rem;
    }
    .delete-zone-label {
        font-size: 0.82rem;
        font-weight: 600;
        color: #9A3412;
        margin-bottom: 0.5rem;
    }

    /* Table container */
    .table-container {
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        overflow: hidden;
        margin-top: 0.75rem;
        margin-bottom: 0.75rem;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
(
    tab_shopify, tab_dropship, tab_payments, tab_discovery, tab_erp,
    tab_arrivals, tab_transfers, tab_settings, tab_danger,
) = st.tabs([
    "Shopify Sync",
    "Dropship Upload",
    "Payments Upload",
    "SKU Discovery",
    "ERP Upload",
    "Production Arrivals",
    "Warehouse Transfers",
    "Settings",
    "Clear Data",
])

# ─── Shopify Sync ─────────────────────────────────────────────────────────
with tab_shopify:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Shopify Sales Sync</div>
        <div class="dm-section-desc">
            Pull weekly units-sold data per SKU directly from your Shopify store via the API.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Last sync status
    last_sync = get_last_sync("shopify_sales")
    if last_sync:
        ts = last_sync["completed_at"][:19].replace("T", " ")
        count = last_sync["records_synced"]
        st.markdown(
            f'<div class="sync-badge success">Last sync: {ts} &mdash; {count} records</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="sync-badge pending">No Shopify sync has been run yet</div>',
            unsafe_allow_html=True,
        )

    # Date range
    from datetime import timedelta
    today = date.today()
    # Default to last completed Mon-Sun week
    days_since_mon = today.weekday()
    this_monday = today - timedelta(days=days_since_mon)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    col1, col2 = st.columns(2)
    with col1:
        sync_start = st.date_input(
            "From date",
            value=last_monday,
            key="sync_start",
            help="Start of the date range to pull sales data for.",
        )
    with col2:
        sync_end = st.date_input(
            "To date",
            value=last_sunday,
            key="sync_end",
            help="End of the date range (inclusive).",
        )

    st.markdown("")  # spacer
    if st.button("Sync Now", type="primary", use_container_width=False):
        try:
            from shopify_client.sync import sync_weekly_sales
            with st.spinner("Syncing sales from Shopify..."):
                count = sync_weekly_sales(sync_start.isoformat(), sync_end.isoformat())
            st.success(f"Synced {count} weekly sales records from Shopify.")
            log_sync("shopify_sales", "success", count)
        except ImportError:
            st.warning(
                "Shopify client not configured yet. Add your API credentials to the `.env` file first."
            )
        except Exception as e:
            st.error(f"Sync failed: {e}")
            log_sync("shopify_sales", "error", error_message=str(e))

    st.markdown("---")

    # Spreadsheet sales import
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Spreadsheet Sales Import</div>
        <div class="dm-section-desc">
            Upload your PPL Sales & Forecasting Excel file to import historical weekly sales data.
            The file should have tabs named Long, 7/8, and Short with "Week Date" and "Units Sold" columns.
        </div>
    </div>
    """, unsafe_allow_html=True)

    sales_file = st.file_uploader(
        "Upload Sales Excel (.xlsx)",
        type=["xlsx"],
        key="sales_xlsx_upload",
        help="PPL Scaling Sales & Forecasting spreadsheet.",
    )
    if sales_file:
        try:
            import re
            from core.config import SIZES, COLORS

            STYLE_TAB_MAP = {"Long": "Long", "7/8": "7/8", "78": "7/8", "Short": "Short", "Nursing Pillow": "Nursing Pillow"}
            COLOR_MAP_SHEET = {"Black": "Black", "Olive Green": "Olive Green", "Burgundy": "Burgundy"}

            all_rows = []
            xls = pd.ExcelFile(sales_file)

            # Known color headers in the spreadsheet
            COLOR_HEADERS = {
                "black": "Black",
                "olive green": "Olive Green",
                "burgundy": "Burgundy",
                "green": "Olive Green",
                "red": "Burgundy",
            }

            for sheet_name in xls.sheet_names:
                # Match style from sheet name
                style = None
                for key, val in STYLE_TAB_MAP.items():
                    if key.lower() in sheet_name.lower():
                        style = val
                        break
                if not style:
                    continue

                df = pd.read_excel(xls, sheet_name=sheet_name, header=None)

                # Parse the sheet row by row, handling:
                # - Color header rows (e.g., "BLACK", "OLIVE GREEN")
                # - Repeated column header rows (Size, Week, ...)
                # - Data rows with size, week date, units sold
                current_color = "Black"  # default
                current_size = None
                week_col = None
                units_col = None
                size_col = None

                for i in range(len(df)):
                    row_vals = df.iloc[i].values
                    first_val = str(row_vals[0]).strip() if pd.notna(row_vals[0]) else ""

                    # Check for color header row (e.g., "BLACK", "OLIVE GREEN")
                    if first_val.lower() in COLOR_HEADERS:
                        current_color = COLOR_HEADERS[first_val.lower()]
                        continue

                    # Check for column header row (contains "Week Date" and "Units Sold")
                    str_vals = [str(v).strip().lower() for v in row_vals]
                    if any("week date" in v for v in str_vals) and any("units sold" in v for v in str_vals):
                        # Re-detect column positions for this section
                        week_col = None
                        units_col = None
                        size_col = None
                        for j, v in enumerate(str_vals):
                            if "week date" in v:
                                week_col = j
                            elif "units sold" in v:
                                units_col = j
                            elif v == "size":
                                size_col = j
                        continue

                    if week_col is None or units_col is None:
                        continue

                    # Check for size value
                    if size_col is not None and pd.notna(row_vals[size_col]):
                        sz = str(row_vals[size_col]).strip()
                        if sz in get_sizes(style):
                            current_size = sz

                    if not current_size:
                        continue

                    # Get week date and units
                    week_val = str(row_vals[week_col]).strip() if pd.notna(row_vals[week_col]) else ""
                    units_val = row_vals[units_col]

                    if not week_val or week_val == "nan":
                        continue

                    # Parse week date like "12/16-12/22" or "3/10-3/16"
                    m = re.match(r"(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})", week_val)
                    if not m:
                        continue

                    # Skip rows with no units sold (nan/empty)
                    if not pd.notna(units_val):
                        continue
                    try:
                        units = int(float(units_val))
                    except (ValueError, TypeError):
                        continue

                    sm, sd, em, ed = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    # Determine year
                    if sm == 12 and em == 12:
                        year_start, year_end = 2025, 2025
                    elif sm == 12:
                        year_start, year_end = 2025, 2026
                    else:
                        year_start, year_end = 2026, 2026

                    week_start = f"{year_start}-{sm:02d}-{sd:02d}"
                    week_end = f"{year_end}-{em:02d}-{ed:02d}"

                    # Skip future weeks
                    from datetime import date as _date
                    if week_start > _date.today().isoformat():
                        continue

                    all_rows.append({
                        "style": style,
                        "color": current_color,
                        "size": current_size,
                        "week_start": week_start,
                        "week_end": week_end,
                        "units_sold": units,
                    })

            if all_rows:
                preview_df = pd.DataFrame(all_rows)
                st.success(f"Parsed **{len(all_rows)}** sales records from {len(set(r['style'] for r in all_rows))} style tabs.")
                st.dataframe(preview_df.head(20), use_container_width=True, hide_index=True)

                if st.button("Import Sales Data", type="primary", key="import_sales_xlsx"):
                    count = 0
                    for r in all_rows:
                        upsert_weekly_sales(
                            r["style"], r["color"], r["size"],
                            r["week_start"], r["week_end"],
                            r["units_sold"], source="spreadsheet",
                        )
                        count += 1
                    log_sync("spreadsheet_sales", "success", count)
                    st.success(f"Imported **{count}** weekly sales records from spreadsheet.")
                    st.rerun()
            else:
                st.warning("Could not find any sales data in the uploaded file. Make sure it has tabs named Long, 7/8, or Short with 'Week Date' and 'Units Sold' columns.")

        except Exception as e:
            st.error(f"Error reading spreadsheet: {e}")

    st.markdown("---")

    # Shopify CSV import (for historical data beyond API 60-day limit)
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Shopify CSV Import</div>
        <div class="dm-section-desc">
            Import a "Total sales by product" CSV export from Shopify Reports.
            Use this for historical data that the API can't access (older than 60 days).
            Each CSV covers one week — set the week dates below.
        </div>
    </div>
    """, unsafe_allow_html=True)

    csv_file = st.file_uploader(
        "Upload Shopify CSV",
        type=["csv"],
        key="shopify_csv_upload",
        help="Export from Shopify: Analytics → Reports → Total sales by product",
    )

    csv_col1, csv_col2 = st.columns(2)
    with csv_col1:
        csv_week_start = st.date_input(
            "Week start (Monday)",
            value=last_monday,
            key="csv_week_start",
            help="The Monday that starts this week (Mon-Sun).",
        )
    with csv_col2:
        csv_week_end = st.date_input(
            "Week end (Sunday)",
            value=last_sunday,
            key="csv_week_end",
            help="The Sunday that ends this week.",
        )

    if csv_file:
        try:
            import csv
            import io
            from core.sku_mapper import parse_shopify_sku

            content = csv_file.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))

            csv_rows = []
            skipped_skus = []
            for row in reader:
                sku = row.get("Product variant SKU", "").strip().strip('"')
                units_str = row.get("Net items sold", "0").strip()
                try:
                    units = int(float(units_str))
                except (ValueError, TypeError):
                    continue

                if units <= 0:
                    continue

                parsed = parse_shopify_sku(sku)
                if parsed is None:
                    skipped_skus.append((sku, units))
                    continue

                style, color, size = parsed
                csv_rows.append({
                    "style": style,
                    "color": color,
                    "size": size,
                    "week_start": csv_week_start.isoformat(),
                    "week_end": csv_week_end.isoformat(),
                    "units_sold": units,
                    "sku": sku,
                })

            # Aggregate duplicates (same style+color+size can appear from multiple SKUs)
            from collections import defaultdict
            agg = defaultdict(int)
            for r in csv_rows:
                key = (r["style"], r["color"], r["size"])
                agg[key] += r["units_sold"]

            agg_rows = [
                {"Style": s, "Color": c, "Size": sz, "Units Sold": u,
                 "Week": f"{csv_week_start.strftime('%-m/%-d')}-{csv_week_end.strftime('%-m/%-d')}"}
                for (s, c, sz), u in sorted(agg.items())
            ]

            if agg_rows:
                st.success(f"Parsed **{len(agg_rows)}** SKU records ({sum(r['Units Sold'] for r in agg_rows)} total units)")
                st.dataframe(pd.DataFrame(agg_rows), use_container_width=True, hide_index=True)

                if skipped_skus:
                    with st.expander(f"Skipped {len(skipped_skus)} unrecognized SKUs"):
                        for sku, qty in sorted(skipped_skus, key=lambda x: -x[1]):
                            st.text(f"  {sku}: {qty} units")

                if st.button("Import CSV Data", type="primary", key="import_shopify_csv"):
                    count = 0
                    ws = csv_week_start.isoformat()
                    we = csv_week_end.isoformat()
                    for (style, color, size), units in agg.items():
                        upsert_weekly_sales(style, color, size, ws, we, units, source="shopify")
                        count += 1
                    log_sync("shopify_csv", "success", count)
                    st.success(f"Imported **{count}** records for week {csv_week_start} to {csv_week_end}")
                    st.rerun()
            else:
                st.warning("No recognized PPL or Nursing Pillow SKUs found in the CSV.")
                if skipped_skus:
                    with st.expander(f"All {len(skipped_skus)} unrecognized SKUs"):
                        for sku, qty in sorted(skipped_skus, key=lambda x: -x[1]):
                            st.text(f"  {sku}: {qty} units")

        except Exception as e:
            st.error(f"Error reading CSV: {e}")

# ─── Dropship Upload ──────────────────────────────────────────────────────
with tab_dropship:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Dropship Orders Upload</div>
        <div class="dm-section-desc">
            Upload the Chinese ERP export Excel for dropship orders (orders shipped
            from China). Expected columns:<br>
            <code>交易编号</code>, <code>平台订单状态</code>, <code>付款时间</code>,
            <code>SKU</code>, <code>平台SKU</code>, <code>商品数量</code>,
            <code>仓库</code>, <code>国家(中)</code>, <code>物流渠道</code>,
            <code>所属地区（省/州）</code>.<br><br>
            On upload, existing rows in the file's date range are replaced.
        </div>
    </div>
    """, unsafe_allow_html=True)

    from core.database import (
        insert_dropship_rows_bulk, delete_dropship_in_range,
        DROPSHIP_WAREHOUSE_MAP, DROPSHIP_COUNTRY_MAP,
        clear_all_dropship_orders,
    )
    from core.sku_mapper import parse_shopify_sku, parse_erp_sku

    ds_file = st.file_uploader(
        "Upload Dropship Excel",
        type=["xlsx", "xls"],
        key="dropship_upload",
        help="ERP export containing 交易编号, 付款时间, 仓库, etc.",
    )

    if ds_file:
        try:
            ds_df = pd.read_excel(ds_file)

            required = ["交易编号", "付款时间", "SKU", "平台SKU", "商品数量", "仓库", "国家(中)"]
            missing = [c for c in required if c not in ds_df.columns]
            if missing:
                st.error(f"Missing required columns: {', '.join(missing)}")
                st.stop()

            # Parse rows into the storage format
            parsed_rows = []
            for _, r in ds_df.iterrows():
                paid_at_utc = r.get("付款时间")
                if pd.isna(paid_at_utc):
                    paid_at_utc = None
                    paid_at_local = None
                else:
                    paid_at_utc = pd.to_datetime(paid_at_utc)
                    paid_at_local = paid_at_utc.date().isoformat()

                erp_sku = str(r.get("SKU") or "").strip()
                sho_sku = str(r.get("平台SKU") or "").strip()

                # Try mapping (Shopify first, then ERP)
                parsed = parse_shopify_sku(sho_sku) if sho_sku else None
                if parsed is None and erp_sku:
                    parsed = parse_erp_sku(erp_sku)
                style, color, size = parsed if parsed else (None, None, None)

                # Treat NaN / empty as Unknown, not the literal string "nan"
                wh_val = r.get("仓库")
                wh_raw = str(wh_val).strip() if pd.notna(wh_val) else ""
                wh = DROPSHIP_WAREHOUSE_MAP.get(wh_raw, wh_raw or "Unknown")
                ctry_val = r.get("国家(中)")
                ctry_raw = str(ctry_val).strip() if pd.notna(ctry_val) else ""
                ctry = DROPSHIP_COUNTRY_MAP.get(ctry_raw, ctry_raw or "Unknown")

                qty_val = r.get("商品数量")
                try:
                    qty = int(qty_val) if pd.notna(qty_val) else 0
                except (ValueError, TypeError):
                    qty = 0

                def _clean(val):
                    """Strip + treat NaN/empty as None, never the literal string 'nan'."""
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        return None
                    s = str(val).strip()
                    if not s or s.lower() == "nan":
                        return None
                    return s

                parsed_rows.append({
                    "order_number": _clean(r.get("交易编号")),
                    "paid_at_utc": str(paid_at_utc) if paid_at_utc else None,
                    "paid_at_local": paid_at_local,
                    "status": _clean(r.get("平台订单状态")),
                    "erp_sku": erp_sku or None,
                    "shopify_sku": sho_sku or None,
                    "quantity": qty,
                    "warehouse_raw": wh_raw or None,
                    "warehouse": wh,
                    "country_raw": ctry_raw or None,
                    "country": ctry,
                    "region": _clean(r.get("所属地区（省/州）")),
                    "shipping_carrier": _clean(r.get("物流渠道")),
                    "style": style,
                    "color": color,
                    "size": size,
                })

            valid_dates = [r["paid_at_local"] for r in parsed_rows if r["paid_at_local"]]
            if not valid_dates:
                st.error("No valid 付款时间 dates found in the file.")
                st.stop()

            min_date = min(valid_dates)
            max_date = max(valid_dates)
            unique_orders = len({r["order_number"] for r in parsed_rows if r["order_number"]})
            total_units = sum(r["quantity"] for r in parsed_rows)
            mapped_rows = sum(1 for r in parsed_rows if r["style"])

            st.success(
                f"Parsed **{len(parsed_rows)}** line items "
                f"({unique_orders} orders, {total_units} units) "
                f"from {min_date} to {max_date}"
            )
            st.caption(
                f"{mapped_rows} rows mapped to tracked products · "
                f"{len(parsed_rows) - mapped_rows} unmapped"
            )

            # Preview
            preview_df = pd.DataFrame([
                {
                    "Order #": r["order_number"],
                    "Paid": r["paid_at_local"],
                    "Warehouse": r["warehouse"],
                    "Country": r["country"],
                    "Region": r["region"] or "",
                    "Qty": r["quantity"],
                    "Shopify SKU": r["shopify_sku"] or "",
                    "Mapped": f"{r['style']} / {r['color']} / {r['size']}" if r["style"] else "—",
                }
                for r in parsed_rows[:50]
            ])
            with st.expander("Preview first 50 rows"):
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

            st.warning(
                f"Importing will **delete** any existing dropship rows where "
                f"paid_at is between **{min_date}** and **{max_date}**, then insert these {len(parsed_rows)} rows."
            )

            if st.button("Import Dropship Data", type="primary", key="import_dropship"):
                with st.spinner(f"Importing {len(parsed_rows)} rows in bulk batches..."):
                    delete_dropship_in_range(min_date, max_date)
                    n = insert_dropship_rows_bulk(parsed_rows, batch_size=200)
                log_sync("dropship_upload", "success", n)
                st.success(f"Imported **{n}** dropship rows.")
                st.rerun()

        except Exception as e:
            st.error(f"Error reading file: {e}")

    st.markdown("---")
    with st.expander("⚠️ Clear all dropship data"):
        st.caption("Removes all dropship orders. Shopify orders are NOT affected.")
        if st.button("Clear dropship orders", key="clear_dropship_btn"):
            clear_all_dropship_orders()
            st.success("All dropship data cleared.")
            st.rerun()


# ─── Payments Upload ──────────────────────────────────────────────────────
with tab_payments:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Payments / Invoice Upload</div>
        <div class="dm-section-desc">
            Upload the finance tracking Excel (e.g. <code>payment (Albert).xlsx</code>).
            We read the transaction sheet — expected columns:<br>
            <code>Date</code>, <code>Month</code>, <code>Amount</code>,
            <code>Discription</code>, <code>GL</code> (category),
            <code>Country</code>.<br><br>
            <b>Only data from the current calendar year onwards is imported</b>
            — older months in the file are skipped.<br><br>
            On import, existing rows in the file's month range are <b>replaced</b>.
        </div>
    </div>
    """, unsafe_allow_html=True)

    from core.database import (
        insert_payment_rows_bulk, delete_payments_in_range,
        clear_all_payments, PAYMENT_CATEGORIES, PAYMENT_COUNTRY_MAP,
    )

    pay_file = st.file_uploader(
        "Upload Payments Excel",
        type=["xlsx", "xls"],
        key="payments_upload",
        help="The finance tracker .xlsx with Date/Month/Amount/GL/Country columns.",
    )

    pay_sheet = st.text_input(
        "Sheet name to import",
        value="Babybub NEW",
        key="payments_sheet_name",
        help="Name of the sheet that contains the raw transactions.",
    )

    if pay_file:
        try:
            pay_df = pd.read_excel(pay_file, sheet_name=pay_sheet)

            # Required columns. The legacy file uses 'Discription' (sic).
            required = ["Date", "Month", "Amount"]
            missing = [c for c in required if c not in pay_df.columns]
            if missing:
                st.error(
                    f"Sheet `{pay_sheet}` is missing required columns: {', '.join(missing)}. "
                    f"Found: {list(pay_df.columns)}"
                )
                st.stop()

            desc_col = "Discription" if "Discription" in pay_df.columns else "Description"
            gl_col = "GL" if "GL" in pay_df.columns else "Category"
            country_col = "Country" if "Country" in pay_df.columns else None

            # Year cutoff: only import data from the start of the current year.
            current_year = date.today().year

            parsed_rows = []
            skipped = 0
            skipped_old_years = 0
            for _, r in pay_df.iterrows():
                amount = r.get("Amount")
                if pd.isna(amount):
                    skipped += 1
                    continue
                try:
                    amount = float(amount)
                except (TypeError, ValueError):
                    skipped += 1
                    continue

                month_val = r.get("Month")
                if pd.isna(month_val):
                    skipped += 1
                    continue
                month_dt = pd.to_datetime(month_val, errors="coerce")
                if pd.isna(month_dt):
                    skipped += 1
                    continue
                # Skip rows from before the current calendar year
                if month_dt.year < current_year:
                    skipped_old_years += 1
                    continue
                year_month = month_dt.strftime("%Y-%m")

                date_val = r.get("Date")
                date_iso = None
                if pd.notna(date_val):
                    date_dt = pd.to_datetime(date_val, errors="coerce")
                    if pd.notna(date_dt):
                        # The "Date" column often lacks a year — patch from Month.
                        if date_dt.year == 1900:
                            date_dt = date_dt.replace(year=month_dt.year)
                        date_iso = date_dt.date().isoformat()
                if date_iso is None:
                    date_iso = month_dt.date().isoformat()

                category_raw = r.get(gl_col) if gl_col in pay_df.columns else None
                category = (
                    str(category_raw).strip() if pd.notna(category_raw) else None
                )
                has_inv = int(
                    PAYMENT_CATEGORIES.get(category, {}).get("has_invoice", False)
                ) if category else 0

                country_raw = r.get(country_col) if country_col else None
                country = (
                    PAYMENT_COUNTRY_MAP.get(
                        str(country_raw).strip() if pd.notna(country_raw) else "",
                        str(country_raw).strip() if pd.notna(country_raw) else None,
                    )
                )

                desc_val = r.get(desc_col) if desc_col in pay_df.columns else None
                description = (
                    str(desc_val).strip() if pd.notna(desc_val) else None
                )

                parsed_rows.append({
                    "payment_date": date_iso,
                    "year_month": year_month,
                    "amount": amount,
                    "description": description,
                    "category": category,
                    "country": country,
                    "has_invoice": has_inv,
                    "source_file": pay_file.name,
                })

            if not parsed_rows:
                st.warning(
                    f"No valid payment rows parsed. "
                    f"({skipped} rows skipped, {skipped_old_years} pre-{current_year} rows skipped.)"
                )
                st.stop()

            months_seen = sorted({r["year_month"] for r in parsed_rows})
            total_positive = sum(r["amount"] for r in parsed_rows if r["amount"] > 0)
            total_negative = sum(r["amount"] for r in parsed_rows if r["amount"] < 0)
            categorized = sum(1 for r in parsed_rows if r["category"])

            st.success(
                f"Parsed **{len(parsed_rows)}** rows · "
                f"{len(months_seen)} months ({months_seen[0]} → {months_seen[-1]}) · "
                f"{skipped} invalid rows skipped"
            )
            if skipped_old_years > 0:
                st.info(
                    f"📅 **{skipped_old_years} rows from before {current_year}** "
                    f"were skipped — only data from this year onwards is imported."
                )
            st.caption(
                f"Outflows (positive): ${total_positive:,.2f}  ·  "
                f"Negatives (refunds / deposits): ${total_negative:,.2f}  ·  "
                f"{categorized} rows with category"
            )

            # Preview
            preview_df = pd.DataFrame([
                {
                    "Date": r["payment_date"],
                    "Month": r["year_month"],
                    "Amount": r["amount"],
                    "Category": r["category"] or "(uncategorized)",
                    "Country": r["country"] or "—",
                    "Description": (r["description"] or "")[:60],
                }
                for r in parsed_rows[:50]
            ])
            with st.expander(f"Preview first 50 of {len(parsed_rows)} rows"):
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

            st.warning(
                f"Importing will **replace** existing payments where year_month is "
                f"between **{months_seen[0]}** and **{months_seen[-1]}**, then insert these "
                f"{len(parsed_rows)} rows."
            )

            if st.button("Import Payments", type="primary", key="import_payments"):
                with st.spinner(f"Importing {len(parsed_rows)} rows..."):
                    delete_payments_in_range(months_seen[0], months_seen[-1])
                    n = insert_payment_rows_bulk(parsed_rows, batch_size=200)
                log_sync("payments_upload", "success", n)
                st.success(f"Imported **{n}** payment rows.")
                st.rerun()

        except Exception as e:
            st.error(f"Error reading file: {e}")

    st.markdown("---")
    with st.expander("⚠️ Clear all payment data"):
        st.caption("Removes all payment rows. Other data is NOT affected.")
        if st.button("Clear payments", key="clear_payments_btn"):
            clear_all_payments()
            st.success("All payment data cleared.")
            st.rerun()


# ─── SKU Discovery ────────────────────────────────────────────────────────
with tab_discovery:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">SKU Discovery</div>
        <div class="dm-section-desc">
            Unmapped Shopify SKUs from your sales data, sorted by volume.
            Use this to find new products worth tracking — give the SKU pattern
            to your developer to add to the dashboard.
            <br><br>
            After adding new SKU mappings in the code, click <b>Re-derive sales</b>
            below to backfill historical data without re-syncing Shopify.
        </div>
    </div>
    """, unsafe_allow_html=True)

    disc_col1, disc_col2 = st.columns([1, 1])
    with disc_col1:
        disc_lookback = st.selectbox(
            "Show SKUs sold in last",
            options=[("4 weeks", 28), ("12 weeks", 84), ("26 weeks", 182), ("All time", None)],
            format_func=lambda x: x[0],
            index=1,
            key="disc_lookback",
        )
    with disc_col2:
        disc_limit = st.selectbox(
            "Max SKUs to show",
            options=[20, 50, 100, 200],
            index=1,
            key="disc_limit",
        )

    # Compute date range
    from datetime import date as _disc_date, timedelta as _disc_td
    disc_end = _disc_date.today()
    disc_days = disc_lookback[1]
    disc_start = (disc_end - _disc_td(days=disc_days)).isoformat() if disc_days else None

    unmapped = get_unmapped_raw_skus(start_date=disc_start, limit=disc_limit)

    if unmapped:
        st.warning(f"Found **{len(unmapped)}** unmapped SKUs with sales in this range")
        disc_df = pd.DataFrame([
            {
                "Shopify SKU": r["shopify_sku"],
                "Total Units": r["total_units"],
                "Weeks Active": r["weeks_seen"],
                "Last Sale Week": r["last_week"],
            }
            for r in unmapped
        ])
        st.dataframe(disc_df, use_container_width=True, hide_index=True)
    else:
        st.success("All Shopify SKUs in this range are mapped to tracked products. 🎉")

    st.markdown("---")
    st.markdown("**Re-derive sales from raw data**")
    st.caption(
        "Run this after a developer adds new SKU mappings to `core/config.py`. "
        "It re-processes the raw Shopify data and backfills `weekly_sales` for the newly mapped products."
    )
    if st.button("🔄 Re-derive Weekly Sales", type="primary", key="rederive_sales"):
        with st.spinner("Re-deriving weekly sales from raw data..."):
            mapped, unmapped_count = derive_weekly_sales_from_raw()
        st.success(
            f"Done! Mapped **{mapped}** SKUs into `weekly_sales`. "
            f"{unmapped_count} SKUs still unmapped (see list above)."
        )
        log_sync("rederive", "success", mapped)
        st.rerun()

# ─── ERP Upload ───────────────────────────────────────────────────────────
with tab_erp:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">ERP Inventory Upload</div>
        <div class="dm-section-desc">
            Upload an ERP Excel export to refresh current stock levels across all warehouses.
        </div>
    </div>
    """, unsafe_allow_html=True)

    last_erp = get_last_sync("erp_upload")
    if last_erp:
        ts = last_erp["completed_at"][:19].replace("T", " ")
        count = last_erp["records_synced"]
        st.markdown(
            f'<div class="sync-badge success">Last upload: {ts} &mdash; {count} records</div>',
            unsafe_allow_html=True,
        )

    erp_file = st.file_uploader(
        "Upload ERP Excel (.xlsx)",
        type=["xlsx"],
        key="erp_upload",
        help="Standard ERP inventory export in .xlsx format.",
    )

    if erp_file:
        snap_date = st.date_input(
            "Snapshot date",
            value=date.today(),
            key="snap_date",
            help="The date this inventory snapshot represents.",
        )

        with st.spinner("Parsing ERP file..."):
            result = parse_erp_excel(erp_file, snapshot_date=snap_date.isoformat())

        summary = result["summary"]
        if "error" in summary:
            st.error(summary["error"])
        else:
            # Metric cards
            st.markdown(f"""
            <div class="metric-row">
                <div class="metric-card">
                    <div class="label">Total Rows</div>
                    <div class="value">{summary["total_rows"]:,}</div>
                </div>
                <div class="metric-card">
                    <div class="label">PPL Rows</div>
                    <div class="value">{summary["ppl_rows"]:,}</div>
                </div>
                <div class="metric-card">
                    <div class="label">Unique SKUs</div>
                    <div class="value">{summary["unique_skus"]:,}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            wh_list = ", ".join(summary["warehouses"])
            st.caption(f"Warehouses detected: {wh_list}")

            st.markdown("**Preview**")
            st.markdown('<div class="table-container">', unsafe_allow_html=True)
            st.dataframe(result["preview"], use_container_width=True, height=300)
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("")  # spacer
            if st.button("Import to Database", type="primary", key="import_erp"):
                insert_inventory_snapshot(result["records"])
                log_sync("erp_upload", "success", len(result["records"]))
                st.success(f"Imported **{len(result['records'])}** inventory records.")
                st.rerun()

# ─── Production Arrivals ──────────────────────────────────────────────────
with tab_arrivals:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Record Production Arrival</div>
        <div class="dm-section-desc">
            Log batches arriving from your supplier to the China warehouse.
            Each entry records the style, color, size, quantity, and date.
        </div>
    </div>
    """, unsafe_allow_html=True)

    arr_style = st.selectbox("Style", ALL_STYLES, key="arr_style")

    with st.form("arrival_form"):
        col2, col3 = st.columns(2)
        with col2:
            arr_color = st.selectbox("Color", get_colors(arr_style), key=f"arr_color_{arr_style}")
        with col3:
            arr_size = st.selectbox("Size", get_sizes(arr_style), key=f"arr_size_{arr_style}")

        col4, col5 = st.columns(2)
        with col4:
            arr_qty = st.number_input(
                "Quantity",
                min_value=1,
                value=100,
                key="arr_qty",
                help="Number of units in this batch.",
            )
        with col5:
            arr_date = st.date_input(
                "Arrival Date",
                value=date.today(),
                key="arr_date",
                help="Date the batch arrived (or is expected).",
            )

        arr_notes = st.text_input(
            "Notes (optional)",
            key="arr_notes",
            placeholder="e.g., PO #1234, delayed shipment",
        )
        submitted = st.form_submit_button("Add Arrival", type="primary")

        if submitted:
            add_production_arrival(
                arr_style, arr_color, arr_size, arr_qty,
                arr_date.isoformat(), arr_notes,
            )
            st.success(f"Added: **{arr_qty}** units of {arr_color} {arr_style} {arr_size}")
            st.rerun()

    # Recent arrivals table
    st.markdown("**Recent Arrivals**")
    arrivals = get_production_arrivals(50)
    if arrivals:
        df = pd.DataFrame(arrivals)
        display_cols = ["id", "style", "color", "size", "qty", "arrival_date", "notes"]
        st.markdown('<div class="table-container">', unsafe_allow_html=True)
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # Delete control
        st.markdown('<div class="delete-zone">', unsafe_allow_html=True)
        st.markdown('<div class="delete-zone-label">Remove an arrival record</div>', unsafe_allow_html=True)
        dc1, dc2 = st.columns([3, 1])
        with dc1:
            del_id = st.number_input(
                "Arrival ID to delete",
                min_value=0,
                value=0,
                key="del_arr",
                label_visibility="collapsed",
                help="Enter the ID from the table above.",
            )
        with dc2:
            if st.button("Delete", key="del_arr_btn", type="secondary") and del_id > 0:
                delete_production_arrival(del_id)
                st.success(f"Deleted arrival #{del_id}")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No production arrivals recorded yet. Use the form above to add one.")

# ─── Warehouse Transfers ──────────────────────────────────────────────────
with tab_transfers:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Record Warehouse Transfer</div>
        <div class="dm-section-desc">
            Log stock transferred from China HQ to an overseas warehouse.
            This keeps regional inventory counts accurate for forecasting.
        </div>
    </div>
    """, unsafe_allow_html=True)

    tr_style = st.selectbox("Style", ALL_STYLES, key="tr_style")

    with st.form("transfer_form"):
        col2, col3 = st.columns(2)
        with col2:
            tr_color = st.selectbox("Color", get_colors(tr_style), key=f"tr_color_{tr_style}")
        with col3:
            tr_size = st.selectbox("Size", get_sizes(tr_style), key=f"tr_size_{tr_style}")

        col4, col5, col6 = st.columns(3)
        with col4:
            tr_qty = st.number_input(
                "Quantity",
                min_value=1,
                value=100,
                key="tr_qty",
                help="Number of units being transferred.",
            )
        with col5:
            overseas = [w for w in WAREHOUSE_DISPLAY_NAMES if w != "China HQ"]
            tr_dest = st.selectbox(
                "Destination",
                overseas,
                key="tr_dest",
                help="Target overseas warehouse.",
            )
        with col6:
            tr_date = st.date_input(
                "Transfer Date",
                value=date.today(),
                key="tr_date",
                help="Date the transfer was shipped or received.",
            )

        tr_notes = st.text_input(
            "Notes (optional)",
            key="tr_notes",
            placeholder="e.g., via ocean freight, tracking #XYZ",
        )
        submitted = st.form_submit_button("Add Transfer", type="primary")

        if submitted:
            add_warehouse_transfer(
                tr_style, tr_color, tr_size, tr_qty,
                tr_dest, tr_date.isoformat(), tr_notes,
            )
            st.success(f"Added: **{tr_qty}** units of {tr_color} {tr_style} {tr_size} to {tr_dest}")
            st.rerun()

    # Recent transfers table
    st.markdown("**Recent Transfers**")
    transfers = get_warehouse_transfers(50)
    if transfers:
        df = pd.DataFrame(transfers)
        display_cols = ["id", "style", "color", "size", "qty", "to_warehouse", "transfer_date", "notes"]
        st.markdown('<div class="table-container">', unsafe_allow_html=True)
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # Delete control
        st.markdown('<div class="delete-zone">', unsafe_allow_html=True)
        st.markdown('<div class="delete-zone-label">Remove a transfer record</div>', unsafe_allow_html=True)
        dc1, dc2 = st.columns([3, 1])
        with dc1:
            del_id = st.number_input(
                "Transfer ID to delete",
                min_value=0,
                value=0,
                key="del_tr",
                label_visibility="collapsed",
                help="Enter the ID from the table above.",
            )
        with dc2:
            if st.button("Delete", key="del_tr_btn", type="secondary") and del_id > 0:
                delete_warehouse_transfer(del_id)
                st.success(f"Deleted transfer #{del_id}")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("No warehouse transfers recorded yet. Use the form above to add one.")

# ─── Settings ─────────────────────────────────────────────────────────────
with tab_settings:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title">Dashboard Settings</div>
        <div class="dm-section-desc">
            Configure alert thresholds used across the dashboard for inventory health calculations.
        </div>
    </div>
    """, unsafe_allow_html=True)

    stockout_days = int(get_setting("stockout_threshold_days", "14"))
    warning_days = int(get_setting("warning_threshold_days", "30"))

    # Stockout threshold
    st.markdown("""
    <div class="setting-item">
        <div class="setting-label">Stockout Alert Threshold</div>
        <div class="setting-help">SKUs with fewer days of stock remaining than this value are flagged as critical.</div>
    </div>
    """, unsafe_allow_html=True)
    new_stockout = st.slider(
        "Stockout Alert Threshold (days)",
        1, 60, stockout_days,
        key="stockout_slider",
        label_visibility="collapsed",
    )

    # Warning threshold
    st.markdown("""
    <div class="setting-item">
        <div class="setting-label">Warning Threshold</div>
        <div class="setting-help">SKUs below this number of days are highlighted as warnings (above stockout but still low).</div>
    </div>
    """, unsafe_allow_html=True)
    new_warning = st.slider(
        "Warning Threshold (days)",
        1, 90, warning_days,
        key="warning_slider",
        label_visibility="collapsed",
    )

    st.markdown("")  # spacer
    if st.button("Save Settings", type="primary"):
        set_setting("stockout_threshold_days", new_stockout)
        set_setting("warning_threshold_days", new_warning)
        st.success("Settings saved successfully.")

# ─── Clear Data ──────────────────────────────────────────────────────────
with tab_danger:
    st.markdown("""
    <div class="dm-section">
        <div class="dm-section-title" style="color:#DC2626;">Clear Data</div>
        <div class="dm-section-desc">
            Permanently delete data from the database. This cannot be undone.
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**Clear Sales Data**")
        st.caption("Deletes all weekly sales records. You'll need to re-sync Shopify or re-import spreadsheet.")
        if st.button("Clear Sales Data", key="clear_sales_btn"):
            st.session_state["confirm_clear"] = "sales"

    with col2:
        st.markdown("**Clear Inventory Data**")
        st.caption("Deletes all inventory snapshots. You'll need to re-upload ERP data.")
        if st.button("Clear Inventory Data", key="clear_inv_btn"):
            st.session_state["confirm_clear"] = "inventory"

    with col3:
        st.markdown("**Clear ALL Data**")
        st.caption("Deletes everything: sales, inventory, arrivals, transfers, and sync logs.")
        if st.button("Clear ALL Data", key="clear_all_btn"):
            st.session_state["confirm_clear"] = "all"

    # Confirmation dialog
    confirm = st.session_state.get("confirm_clear")
    if confirm:
        labels = {"sales": "sales data", "inventory": "inventory data", "all": "ALL data"}
        st.warning(f"Are you sure you want to delete **{labels[confirm]}**? This cannot be undone.")
        c1, c2, _ = st.columns([1, 1, 4])
        with c1:
            if st.button("Yes, delete", type="primary", key="confirm_yes"):
                if confirm == "sales":
                    clear_all_sales()
                    st.success("All sales data cleared.")
                elif confirm == "inventory":
                    clear_all_inventory()
                    st.success("All inventory data cleared.")
                elif confirm == "all":
                    clear_all_data()
                    st.success("All data cleared.")
                st.session_state.pop("confirm_clear", None)
                st.rerun()
        with c2:
            if st.button("Cancel", key="confirm_cancel"):
                st.session_state.pop("confirm_clear", None)
                st.rerun()
