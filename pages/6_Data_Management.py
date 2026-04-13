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
tab_shopify, tab_erp, tab_arrivals, tab_transfers, tab_settings = st.tabs([
    "Shopify Sync",
    "ERP Upload",
    "Production Arrivals",
    "Warehouse Transfers",
    "Settings",
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
    yesterday = date.today() - timedelta(days=1)
    last_week_start = yesterday - timedelta(days=6)

    col1, col2 = st.columns(2)
    with col1:
        sync_start = st.date_input(
            "From date",
            value=last_week_start,
            key="sync_start",
            help="Start of the date range to pull sales data for.",
        )
    with col2:
        sync_end = st.date_input(
            "To date",
            value=yesterday,
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
