"""Parse ERP Excel inventory exports into structured inventory snapshots."""

import pandas as pd
from datetime import date
from core.sku_mapper import parse_erp_sku
from core.config import WAREHOUSES


# Expected ERP columns (Chinese headers)
ERP_COLUMNS = {
    "库存SKU": "sku",
    "仓库名称": "warehouse",
    "仓库库存量": "stock_qty",
    "仓库可用库存量": "available_qty",
    "仓库7天总销量": "sales_7d",
    "仓库28天总销量": "sales_28d",
    "仓库42天总销量": "sales_42d",
    "当前可售天数": "days_available",
    "采购在途量": "in_transit_qty",
    "库存SKU中文名称": "sku_name",
}


def parse_erp_excel(file, snapshot_date=None):
    """
    Parse an ERP inventory export Excel file.

    Args:
        file: file path or file-like object (from Streamlit uploader)
        snapshot_date: date for this snapshot (defaults to today)

    Returns:
        dict with keys:
            'records': list of tuples ready for insert_inventory_snapshot()
            'summary': dict with parsing stats
            'preview': DataFrame for display
    """
    if snapshot_date is None:
        snapshot_date = date.today().isoformat()

    df = pd.read_excel(file, header=0)

    # Rename columns to English
    rename_map = {}
    for cn_col, en_col in ERP_COLUMNS.items():
        if cn_col in df.columns:
            rename_map[cn_col] = en_col
    df = df.rename(columns=rename_map)

    if "sku" not in df.columns:
        return {"records": [], "summary": {"error": "Could not find SKU column"}, "preview": pd.DataFrame()}

    # Parse each SKU and filter to PPL products only
    parsed_rows = []
    skipped = 0

    for _, row in df.iterrows():
        sku = str(row.get("sku", ""))
        result = parse_erp_sku(sku)
        if result is None:
            skipped += 1
            continue

        style, color, size = result
        warehouse_cn = str(row.get("warehouse", ""))
        warehouse = WAREHOUSES.get(warehouse_cn, warehouse_cn)

        # Parse numeric fields safely
        stock_qty = _safe_int(row.get("stock_qty", 0))
        available_qty = _safe_int(row.get("available_qty", 0))
        sales_7d = _safe_int(row.get("sales_7d", 0))
        sales_28d = _safe_int(row.get("sales_28d", 0))
        sales_42d = _safe_int(row.get("sales_42d", 0))
        days_available = _safe_float(row.get("days_available"))
        in_transit_qty = _safe_int(row.get("in_transit_qty", 0))

        parsed_rows.append({
            "style": style,
            "color": color,
            "size": size,
            "warehouse": warehouse,
            "stock_qty": stock_qty,
            "available_qty": available_qty,
            "sales_7d": sales_7d,
            "sales_28d": sales_28d,
            "sales_42d": sales_42d,
            "days_available": days_available,
            "in_transit_qty": in_transit_qty,
        })

    # Build insert tuples
    records = [
        (
            r["style"], r["color"], r["size"], r["warehouse"],
            r["stock_qty"], r["available_qty"],
            r["sales_7d"], r["sales_28d"], r["sales_42d"],
            r["days_available"], r["in_transit_qty"],
            snapshot_date,
        )
        for r in parsed_rows
    ]

    preview_df = pd.DataFrame(parsed_rows) if parsed_rows else pd.DataFrame()

    summary = {
        "total_rows": len(df),
        "ppl_rows": len(parsed_rows),
        "skipped_rows": skipped,
        "unique_skus": len(set((r["style"], r["color"], r["size"]) for r in parsed_rows)),
        "warehouses": sorted(set(r["warehouse"] for r in parsed_rows)),
    }

    return {"records": records, "summary": summary, "preview": preview_df}


def _safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def _safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
