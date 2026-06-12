"""
Product cost model — migrated from "📦 Product Cost 2026.xlsx".

Replicates the Excel's US-sheet landed-cost calculation:

    Total cost   = product + agent fee + domestic + sea + rent + inbound
                   + last-mile + pick&pack + pink box + other box
    Landed cost  = product + domestic + sea + inbound + pink box

Component sources:
  - product / agent fee / pick&pack / boxes : manual inputs (cost_products)
  - domestic + sea freight : qty-weighted avg of per-shipment $/unit
        (per-unit = shipment total ÷ total shipment qty — uniform allocation,
        matching the Excel '📦 Shipment Data' → 'SKU Averages' tabs)
  - warehouse rent : unit CBM × Σ over age brackets(days × $/CBM/day)
        for the SKU's assumed storage days ('SKU Rent Calculation')
  - inbound : weight-tier op fee + unit CBM × unload rate ('SKU Master')
  - last-mile : average actual 3PL shipping cost per order
        (singles attribute directly to the SKU; bundle types B/C use
        pooled averages — '📊 仪表板 Dashboard' tab semantics)

All SKUs are stored UPPERCASE; lookups normalize with .upper() because the
ERP exports use inconsistent casing.
"""

from collections import defaultdict
from datetime import date

from core.database import get_db, get_setting, invalidate_cache

REGION_US = "US"

# Accessory SKUs that mark bundle order types in the 3PL billing data
BAG_SKU = "J22165-BABYBUB-28*42"          # legging packaging bag → type B
LARGEBOX_SKU = "J11268-LARGEBOX-46*35*16"  # cozy bundle box → type C


def _u(sku):
    """UPPER-normalize a SKU (None-safe)."""
    return sku.strip().upper() if sku else None


# ===========================================================================
# Pure calculation functions (no DB access — unit-testable)
# ===========================================================================

def compute_rent_per_unit(unit_cbm, assumed_days, brackets):
    """
    Warehouse rent $/unit = CBM × Σ over brackets(days spent in bracket × rate).

    brackets: list of dicts {start_day, end_day (None = open), rate_per_cbm_day}
    Replicates 'SKU Rent Calculation': e.g. 0.0096 CBM @ 90 days with the
    standard bracket table → 0.19296.
    """
    if not unit_cbm or not assumed_days:
        return 0.0
    total_rate_days = 0.0
    for b in brackets:
        start = b["start_day"] or 0
        end = b["end_day"] if b["end_day"] is not None else float("inf")
        days_in_bracket = max(0.0, min(float(assumed_days), end) - start)
        total_rate_days += days_in_bracket * (b["rate_per_cbm_day"] or 0)
    return unit_cbm * total_rate_days


def compute_inbound_per_unit(unit_weight_kg, unit_cbm, rate_card, unload_rate_per_cbm):
    """
    Inbound $/unit = weight-tier op fee + unit CBM × unload rate.

    rate_card: list of dicts {tier_start_kg, tier_end_kg (None = open), op_fee}
    Replicates 'SKU Master' All-in $/unit: e.g. 0.27 kg / 0.000518 CBM
    @ $6.20/CBM → 0.21 + 0.0032116 = 0.2132116.
    """
    if unit_weight_kg is None or unit_cbm is None:
        return None
    op_fee = None
    for t in rate_card:
        start = t["tier_start_kg"] or 0
        end = t["tier_end_kg"] if t["tier_end_kg"] is not None else float("inf")
        # Excel tiers are start < W <= end (0<W≤0.5KG etc.)
        if start < unit_weight_kg <= end or (unit_weight_kg == 0 and start == 0):
            op_fee = t["op_fee"]
            break
    if op_fee is None and rate_card:
        op_fee = rate_card[-1]["op_fee"]  # heavier than last tier → top tier
    return (op_fee or 0) + unit_cbm * (unload_rate_per_cbm or 0)


def classify_order(skus):
    """
    Classify a 3PL order by its distinct SKU set (UPPER), mirroring the
    Excel 'Classification' tab:
      A     = single SKU (cost attributes directly to that SKU)
      B     = 2 SKUs: legging + packaging bag, OR large+small cover pair
      C     = 3 SKUs incl. the cozy-bundle large box
      OTHER = anything else (excluded from per-SKU averages)
    """
    n = len(skus)
    if n == 1:
        return "A"
    if n == 2:
        if BAG_SKU in skus:
            return "B"
        if any("LARGECOVER" in s for s in skus) and any("SMALLCOVER" in s for s in skus):
            return "B"
        return "OTHER"
    if n == 3 and LARGEBOX_SKU in skus:
        return "C"
    return "OTHER"


def main_sku_for(skus, order_type):
    """Representative SKU for an order: the non-accessory product SKU."""
    if order_type == "A":
        return next(iter(skus))
    non_acc = sorted(s for s in skus if s not in (BAG_SKU, LARGEBOX_SKU))
    return non_acc[0] if non_acc else sorted(skus)[0]


# ===========================================================================
# Generic bulk insert (mirrors insert_dropship_rows_bulk)
# ===========================================================================

def _bulk_insert(conn, table, cols, rows, batch_size=200):
    if not rows:
        return 0
    one = "(" + ",".join("?" * len(cols)) + ")"
    col_list = ",".join(cols)
    inserted = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        sql = f"INSERT INTO {table} ({col_list}) VALUES " + ",".join([one] * len(chunk))
        flat = []
        for r in chunk:
            for c in cols:
                flat.append(r.get(c))
        conn.execute(sql, flat)
        inserted += len(chunk)
    return inserted


# ===========================================================================
# cost_products CRUD
# ===========================================================================

_PRODUCT_COLS = [
    "region", "shopify_sku", "display_sku", "product_name", "category",
    "china_sku1", "china_sku2", "is_composite",
    "product_cost", "agent_fee", "pick_pack", "pink_box", "other_box",
    "domestic_override", "sea_override", "rent_override",
    "inbound_override", "lastmile_override", "lastmile_group", "notes", "active",
]

# Editable manual-input fields (whitelist for update_cost_product)
PRODUCT_EDITABLE_FIELDS = [
    "product_name", "category", "china_sku1", "china_sku2",
    "product_cost", "agent_fee", "pick_pack", "pink_box", "other_box",
    "domestic_override", "sea_override", "rent_override",
    "inbound_override", "lastmile_override", "lastmile_group",
    "notes", "active",
]


def replace_cost_products(rows, region=REGION_US):
    """Delete all cost products for the region and bulk-insert new ones (seed)."""
    with get_db() as conn:
        conn.execute("DELETE FROM cost_products WHERE region = ?", (region,))
        n = _bulk_insert(conn, "cost_products", _PRODUCT_COLS, rows)
        conn.commit()
    invalidate_cache()
    return n


def get_cost_products(region=REGION_US, include_inactive=False):
    sql = "SELECT * FROM cost_products WHERE region = ?"
    if not include_inactive:
        sql += " AND active = 1"
    sql += " ORDER BY category, product_name"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, (region,)).fetchall()]


def update_cost_product(product_id, fields):
    """Update editable fields on one cost product. fields: {col: value}."""
    sets, params = [], []
    for col, val in fields.items():
        if col not in PRODUCT_EDITABLE_FIELDS:
            continue
        if col in ("china_sku1", "china_sku2"):
            val = _u(val)
        sets.append(f"{col} = ?")
        params.append(val)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(product_id)
    with get_db() as conn:
        conn.execute(f"UPDATE cost_products SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    invalidate_cache()


# ===========================================================================
# Specs / rates CRUD
# ===========================================================================

_SPEC_COLS = [
    "region", "sku", "unit_cbm", "unit_weight_kg", "qty_per_ctn",
    "cbm_per_ctn", "vol_weight_ctn", "rent_unit_cbm", "assumed_storage_days",
    "in_sku_master", "in_rent_table",
]


def replace_sku_specs(rows, region=REGION_US):
    with get_db() as conn:
        conn.execute("DELETE FROM cost_sku_specs WHERE region = ?", (region,))
        n = _bulk_insert(conn, "cost_sku_specs", _SPEC_COLS, rows)
        conn.commit()
    invalidate_cache()
    return n


def get_sku_specs(region=REGION_US):
    with get_db() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM cost_sku_specs WHERE region = ? ORDER BY sku",
                (region,),
            ).fetchall()
        ]


def update_sku_spec(spec_id, fields):
    allowed = {"unit_cbm", "unit_weight_kg", "rent_unit_cbm", "assumed_storage_days"}
    sets, params = [], []
    for col, val in fields.items():
        if col in allowed:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        return
    params.append(spec_id)
    with get_db() as conn:
        conn.execute(f"UPDATE cost_sku_specs SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    invalidate_cache()


def replace_rent_brackets(rows, region=REGION_US):
    with get_db() as conn:
        conn.execute("DELETE FROM cost_rent_brackets WHERE region = ?", (region,))
        n = _bulk_insert(
            conn, "cost_rent_brackets",
            ["region", "start_day", "end_day", "rate_per_cbm_day"], rows,
        )
        conn.commit()
    invalidate_cache()
    return n


def get_rent_brackets(region=REGION_US):
    with get_db() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM cost_rent_brackets WHERE region = ? ORDER BY start_day",
                (region,),
            ).fetchall()
        ]


def replace_rate_card(rows, region=REGION_US):
    with get_db() as conn:
        conn.execute("DELETE FROM cost_rate_card WHERE region = ?", (region,))
        n = _bulk_insert(
            conn, "cost_rate_card",
            ["region", "tier_start_kg", "tier_end_kg", "op_fee"], rows,
        )
        conn.commit()
    invalidate_cache()
    return n


def get_rate_card(region=REGION_US):
    with get_db() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM cost_rate_card WHERE region = ? ORDER BY tier_start_kg",
                (region,),
            ).fetchall()
        ]


# ===========================================================================
# Freight shipments
# ===========================================================================

def replace_shipments(headers, lines, region=REGION_US):
    """Seed: replace all shipments + lines for the region."""
    with get_db() as conn:
        conn.execute("DELETE FROM cost_shipments WHERE region = ?", (region,))
        conn.execute("DELETE FROM cost_shipment_lines WHERE region = ?", (region,))
        nh = _bulk_insert(
            conn, "cost_shipments",
            ["region", "shipment_id", "ship_date", "dom_total", "sea_total", "notes"],
            headers,
        )
        nl = _bulk_insert(
            conn, "cost_shipment_lines",
            ["region", "shipment_id", "sku", "qty"], lines,
        )
        conn.commit()
    invalidate_cache()
    return nh, nl


def add_shipment(shipment_id, ship_date, dom_total, sea_total, lines, region=REGION_US, notes=None):
    """Add one freight shipment with its SKU/qty lines (replaces same id)."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM cost_shipments WHERE region = ? AND shipment_id = ?",
            (region, shipment_id),
        )
        conn.execute(
            "DELETE FROM cost_shipment_lines WHERE region = ? AND shipment_id = ?",
            (region, shipment_id),
        )
        conn.execute(
            "INSERT INTO cost_shipments (region, shipment_id, ship_date, dom_total, sea_total, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (region, shipment_id, ship_date, dom_total, sea_total, notes),
        )
        _bulk_insert(
            conn, "cost_shipment_lines",
            ["region", "shipment_id", "sku", "qty"],
            [
                {"region": region, "shipment_id": shipment_id,
                 "sku": _u(l["sku"]), "qty": l["qty"]}
                for l in lines
            ],
        )
        conn.commit()
    invalidate_cache()


def delete_shipment(shipment_id, region=REGION_US):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM cost_shipments WHERE region = ? AND shipment_id = ?",
            (region, shipment_id),
        )
        conn.execute(
            "DELETE FROM cost_shipment_lines WHERE region = ? AND shipment_id = ?",
            (region, shipment_id),
        )
        conn.commit()
    invalidate_cache()


def get_shipments(region=REGION_US):
    """Shipment headers with computed per-unit rates (totals ÷ Σ qty)."""
    sql = """
        SELECT s.shipment_id, s.ship_date, s.dom_total, s.sea_total, s.notes,
               COALESCE(q.total_qty, 0) AS total_qty,
               COALESCE(q.n_skus, 0) AS n_skus
        FROM cost_shipments s
        LEFT JOIN (
            SELECT shipment_id, SUM(qty) AS total_qty, COUNT(*) AS n_skus
            FROM cost_shipment_lines WHERE region = ?
            GROUP BY shipment_id
        ) q ON q.shipment_id = s.shipment_id
        WHERE s.region = ?
        ORDER BY s.ship_date DESC, s.shipment_id DESC
    """
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(sql, (region, region)).fetchall()]
    for r in rows:
        tq = r["total_qty"] or 0
        r["dom_per_unit"] = (r["dom_total"] / tq) if tq else None
        r["sea_per_unit"] = (r["sea_total"] / tq) if tq else None
    return rows


def get_shipment_lines(region=REGION_US, shipment_id=None):
    sql = "SELECT * FROM cost_shipment_lines WHERE region = ?"
    params = [region]
    if shipment_id:
        sql += " AND shipment_id = ?"
        params.append(shipment_id)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# Standard volumetric divisor: vol weight (kg) = CBM × 1,000,000 / 6,000
_VOL_WEIGHT_PER_CBM = 1000000.0 / 6000.0  # ≈ 166.67 kg per CBM


def _freight_line_rates(region=REGION_US):
    """
    Per-shipment-line $/unit rates, replicating the Excel allocation:
      dom $/unit = dom_total × unit_CBM ÷ Σ(shipment line CBM)
      sea $/unit = sea_total × unit_vol_weight ÷ Σ(shipment line vol weight)
    Unit CBM/weight come from the carton specs (MasterData): CBM/Ctn ÷
    Qty/Ctn and Vol Weight/Ctn ÷ Qty/Ctn. Lines whose SKU has no carton
    spec get no rate (the workbook leaves them blank too).

    Returns [{sku, shipment_id, ship_date, qty, dom_per_unit, sea_per_unit}]
    """
    specs = {s["sku"]: s for s in get_sku_specs(region)}
    headers = {
        h["shipment_id"]: h
        for h in get_shipments(region)
    }
    lines = get_shipment_lines(region)

    def _unit_cbm(sku):
        s = specs.get(_u(sku))
        if not s:
            return None
        if s["cbm_per_ctn"] and s["qty_per_ctn"]:
            return s["cbm_per_ctn"] / s["qty_per_ctn"]
        return s["unit_cbm"]

    def _unit_volw(sku):
        s = specs.get(_u(sku))
        if not s:
            return None
        if s["vol_weight_ctn"] and s["qty_per_ctn"]:
            return s["vol_weight_ctn"] / s["qty_per_ctn"]
        cbm = _unit_cbm(sku)
        return cbm * _VOL_WEIGHT_PER_CBM if cbm else None

    # Shipment totals over lines that HAVE specs (mirrors the workbook,
    # where spec-less lines drop out of the SUMPRODUCT denominators)
    ship_cbm = defaultdict(float)
    ship_volw = defaultdict(float)
    for l in lines:
        cbm, volw = _unit_cbm(l["sku"]), _unit_volw(l["sku"])
        if cbm:
            ship_cbm[l["shipment_id"]] += l["qty"] * cbm
        if volw:
            ship_volw[l["shipment_id"]] += l["qty"] * volw

    out = []
    for l in lines:
        h = headers.get(l["shipment_id"])
        if not h:
            continue
        cbm, volw = _unit_cbm(l["sku"]), _unit_volw(l["sku"])
        dom = sea = None
        if cbm and ship_cbm[l["shipment_id"]]:
            dom = h["dom_total"] * cbm / ship_cbm[l["shipment_id"]]
        if volw and ship_volw[l["shipment_id"]]:
            sea = h["sea_total"] * volw / ship_volw[l["shipment_id"]]
        out.append({
            "sku": _u(l["sku"]),
            "shipment_id": l["shipment_id"],
            "ship_date": h["ship_date"],
            "qty": l["qty"],
            "dom_per_unit": dom,
            "sea_per_unit": sea,
        })
    return out


def get_freight_averages(region=REGION_US):
    """
    Per-SKU average dom/sea $/unit — a simple mean across the SKU's
    shipments, matching the Excel 'SKU Averages' tab.

    Returns {UPPER_SKU: {avg_dom, avg_sea, avg_total, n_shipments,
                         total_qty, min_total, max_total}}
    """
    per_sku = defaultdict(list)
    for r in _freight_line_rates(region):
        if r["dom_per_unit"] is None and r["sea_per_unit"] is None:
            continue
        per_sku[r["sku"]].append(r)

    out = {}
    for sku, rs in per_sku.items():
        doms = [r["dom_per_unit"] for r in rs if r["dom_per_unit"] is not None]
        seas = [r["sea_per_unit"] for r in rs if r["sea_per_unit"] is not None]
        totals = [
            (r["dom_per_unit"] or 0) + (r["sea_per_unit"] or 0) for r in rs
        ]
        avg_dom = sum(doms) / len(doms) if doms else 0.0
        avg_sea = sum(seas) / len(seas) if seas else 0.0
        out[sku] = {
            "sku": sku,
            "avg_dom": avg_dom,
            "avg_sea": avg_sea,
            "avg_total": avg_dom + avg_sea,
            "n_shipments": len(rs),
            "total_qty": sum(r["qty"] for r in rs),
            "min_total": min(totals) if totals else None,
            "max_total": max(totals) if totals else None,
        }
    return out


def get_freight_per_shipment_series(region=REGION_US):
    """Per-shipment $/unit time series per SKU (for trend charts)."""
    return _freight_line_rates(region)


# ===========================================================================
# Last-mile (3PL billing)
# ===========================================================================

_LASTMILE_COLS = [
    "region", "order_id", "ship_date", "country", "shipping_cost",
    "sku_key", "main_sku", "order_type", "total_qty", "num_skus",
]


def insert_lastmile_orders_bulk(rows, region=REGION_US):
    for r in rows:
        r["region"] = region
    with get_db() as conn:
        n = _bulk_insert(conn, "cost_lastmile_orders", _LASTMILE_COLS, rows)
        conn.commit()
    invalidate_cache()
    return n


def delete_lastmile_in_range(start_date, end_date, region=REGION_US):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM cost_lastmile_orders "
            "WHERE region = ? AND ship_date BETWEEN ? AND ?",
            (region, start_date, end_date),
        )
        conn.commit()
    invalidate_cache()


def get_lastmile_averages(region=REGION_US, start_date=None, end_date=None):
    """
    Per-SKU last-mile averages over type-A (single-SKU) orders, plus pooled
    averages for bundle types B and C. Default window = all-time, matching
    the Excel's all-history averages.

    Returns (singles, pooled):
      singles: {UPPER_SKU: {avg_cost, n_orders, min_cost, max_cost}}
      pooled:  {"TYPE_B": avg, "TYPE_C": avg}
    """
    where = "WHERE region = ?"
    params = [region]
    if start_date:
        where += " AND ship_date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND ship_date <= ?"
        params.append(end_date)

    singles_sql = f"""
        SELECT main_sku, AVG(shipping_cost) AS avg_cost, COUNT(*) AS n_orders,
               MIN(shipping_cost) AS min_cost, MAX(shipping_cost) AS max_cost
        FROM cost_lastmile_orders
        {where} AND order_type = 'A' AND main_sku IS NOT NULL
        GROUP BY main_sku
    """
    pooled_sql = f"""
        SELECT order_type, AVG(shipping_cost) AS avg_cost, COUNT(*) AS n_orders
        FROM cost_lastmile_orders
        {where} AND order_type IN ('B', 'C')
        GROUP BY order_type
    """
    singles, pooled = {}, {}
    with get_db() as conn:
        for r in conn.execute(singles_sql, params).fetchall():
            d = dict(r)
            singles[_u(d["main_sku"])] = d
        for r in conn.execute(pooled_sql, params).fetchall():
            pooled[f"TYPE_{r['order_type']}"] = r["avg_cost"]
    return singles, pooled


def get_withbag_sums(region=REGION_US, start_date=None, end_date=None):
    """
    Last-mile cost sums for type-B "product + packaging bag" orders,
    keyed by the product SKU (the non-bag SKU). Returned as sums so the
    caller can pool across old/new SKU variants:
        {UPPER_SKU: {"sum": float, "n": int}}
    """
    where = "WHERE region = ? AND order_type = 'B' AND sku_key LIKE ?"
    params = [region, f"%{BAG_SKU}%"]
    if start_date:
        where += " AND ship_date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND ship_date <= ?"
        params.append(end_date)
    out = defaultdict(lambda: {"sum": 0.0, "n": 0})
    with get_db() as conn:
        for r in conn.execute(
            f"SELECT sku_key, shipping_cost FROM cost_lastmile_orders {where}",
            params,
        ).fetchall():
            for sku in (r["sku_key"] or "").split(";"):
                if sku and sku != BAG_SKU:
                    out[sku]["sum"] += r["shipping_cost"] or 0
                    out[sku]["n"] += 1
    return dict(out)


COVER_COLOR_TOKENS = ["ICE", "NEWYELLOW", "NEWBLUE", "NEWPINK"]


def _coverpair_token(sku_key):
    """Color token for a large+small cover-pair order, else None."""
    if "LARGECOVER" not in sku_key or "SMALLCOVER" not in sku_key:
        return None
    for tok in COVER_COLOR_TOKENS:
        if tok in sku_key:
            return tok
    return None


def get_coverpair_averages(region=REGION_US, start_date=None, end_date=None):
    """
    Average last-mile cost per cover-pair color (large + small pillow cover
    shipped together). Replicates the Excel's bundle-name lookups for the
    'Blissful Bundle Covers <color>' / Ice combo cover rows.

    Returns {color_token: avg_cost}, e.g. {"ICE": 6.46, "NEWYELLOW": 5.53}.
    """
    where = "WHERE region = ? AND sku_key LIKE '%LARGECOVER%' AND sku_key LIKE '%SMALLCOVER%'"
    params = [region]
    if start_date:
        where += " AND ship_date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND ship_date <= ?"
        params.append(end_date)
    sums = defaultdict(lambda: [0.0, 0])
    with get_db() as conn:
        for r in conn.execute(
            f"SELECT sku_key, shipping_cost FROM cost_lastmile_orders {where}", params,
        ).fetchall():
            tok = _coverpair_token(r["sku_key"] or "")
            if tok:
                sums[tok][0] += r["shipping_cost"] or 0
                sums[tok][1] += 1
    return {tok: (s / n if n else None) for tok, (s, n) in sums.items()}


def get_lastmile_monthly(region=REGION_US, main_sku=None, order_type=None):
    """Monthly avg last-mile cost time series (for trend charts)."""
    sql = """
        SELECT substr(ship_date, 1, 7) AS ym,
               AVG(shipping_cost) AS avg_cost,
               COUNT(*) AS n_orders
        FROM cost_lastmile_orders
        WHERE region = ?
    """
    params = [region]
    if main_sku:
        sql += " AND main_sku = ?"
        params.append(_u(main_sku))
    if order_type:
        sql += " AND order_type = ?"
        params.append(order_type)
    sql += " GROUP BY ym ORDER BY ym"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_lastmile_summary(region=REGION_US):
    with get_db() as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS n, MIN(ship_date) AS first_date, "
            "MAX(ship_date) AS last_date, "
            "SUM(CASE WHEN order_type = 'OTHER' THEN 1 ELSE 0 END) AS n_other "
            "FROM cost_lastmile_orders WHERE region = ?",
            (region,),
        ).fetchone()
    return dict(r) if r else {}


# ===========================================================================
# Cost assembly — the heart of the model
# ===========================================================================

def assemble_cost_table(region=REGION_US, lastmile_start=None, lastmile_end=None):
    """
    Compute the full cost breakdown for every active cost product.
    Mirrors the Excel US sheet: per component, the value is
      override (if set) → lookup(china_sku1) → lookup(china_sku2) → 0.0
    Missing lookups contribute 0 (Excel's IFERROR → "") but the component
    name is recorded in `missing` so the UI can flag understated rows.

    Returns a list of dicts, one per product.
    """
    products = get_cost_products(region)
    specs = {s["sku"]: s for s in get_sku_specs(region)}
    freight = get_freight_averages(region)
    brackets = get_rent_brackets(region)
    rate_card = get_rate_card(region)
    unload_rate = float(get_setting("cost_us_unload_rate_per_cbm", "6.2"))
    default_days = int(float(get_setting("cost_us_default_storage_days", "90")))
    singles, pooled = get_lastmile_averages(region, lastmile_start, lastmile_end)
    coverpairs = get_coverpair_averages(region, lastmile_start, lastmile_end)
    withbag = get_withbag_sums(region, lastmile_start, lastmile_end)

    # Each component falls back sku1 → sku2 INDEPENDENTLY, exactly like the
    # Excel's per-column IFERROR(VLOOKUP(sku1), VLOOKUP(sku2)) chains —
    # a SKU can be in the freight data but not the rent table, etc.
    def _candidates(p):
        return [k for k in (_u(p["china_sku1"]), _u(p["china_sku2"])) if k]

    out = []
    for p in products:
        missing = []
        cands = _candidates(p)

        # --- freight (domestic + sea) ---
        fr = next((freight[k] for k in cands if k in freight), None)
        if p["domestic_override"] is not None:
            domestic = p["domestic_override"]
        elif fr:
            domestic = fr["avg_dom"] or 0.0
        else:
            domestic = 0.0
            missing.append("domestic")
        if p["sea_override"] is not None:
            sea = p["sea_override"]
        elif fr:
            sea = fr["avg_sea"] or 0.0
        else:
            sea = 0.0
            missing.append("sea")

        # --- warehouse rent (only SKUs present in the rent table) ---
        if p["rent_override"] is not None:
            rent = p["rent_override"]
        else:
            rent_spec = next(
                (specs[k] for k in cands if k in specs and specs[k]["in_rent_table"]),
                None,
            )
            if rent_spec:
                cbm = rent_spec["rent_unit_cbm"] or rent_spec["unit_cbm"]
                days = rent_spec["assumed_storage_days"] or default_days
                rent = compute_rent_per_unit(cbm, days, brackets) if cbm else 0.0
            else:
                rent = 0.0
                missing.append("rent")

        # --- inbound (only SKUs present in the SKU Master) ---
        if p["inbound_override"] is not None:
            inbound = p["inbound_override"]
        else:
            inb_spec = next(
                (specs[k] for k in cands
                 if k in specs and specs[k]["in_sku_master"]
                 and specs[k]["unit_cbm"] is not None
                 and specs[k]["unit_weight_kg"] is not None),
                None,
            )
            if inb_spec:
                inbound = compute_inbound_per_unit(
                    inb_spec["unit_weight_kg"], inb_spec["unit_cbm"],
                    rate_card, unload_rate,
                )
            else:
                inbound = 0.0
                missing.append("inbound")

        # --- last-mile ---
        if p["lastmile_override"] is not None:
            lastmile = p["lastmile_override"]
        elif p["lastmile_group"] in ("TYPE_B", "TYPE_C"):
            lastmile = pooled.get(p["lastmile_group"])
            if lastmile is None:
                lastmile = 0.0
                missing.append("lastmile")
        elif p["lastmile_group"].startswith("COVERPAIR:"):
            lastmile = coverpairs.get(p["lastmile_group"].split(":", 1)[1])
            if lastmile is None:
                lastmile = 0.0
                missing.append("lastmile")
        elif p["lastmile_group"] == "WITHBAG":
            # Product ships with the packaging bag (leggings): pool the
            # type-B order costs across the product's old/new SKU variants.
            tot = sum(withbag[k]["sum"] for k in cands if k in withbag)
            n = sum(withbag[k]["n"] for k in cands if k in withbag)
            if n:
                lastmile = tot / n
            else:
                lastmile = 0.0
                missing.append("lastmile")
        else:
            lm = next(
                (singles[k]["avg_cost"] for k in cands if k in singles), None,
            )
            if lm is None:
                lm = 0.0
                missing.append("lastmile")
            lastmile = lm

        product_cost = p["product_cost"] or 0.0
        agent_fee = p["agent_fee"] or 0.0
        pick_pack = p["pick_pack"] or 0.0
        pink_box = p["pink_box"] or 0.0
        other_box = p["other_box"] or 0.0

        total = (product_cost + agent_fee + domestic + sea + rent
                 + inbound + lastmile + pick_pack + pink_box + other_box)
        landed = product_cost + domestic + sea + inbound + pink_box

        out.append({
            "id": p["id"],
            "shopify_sku": p["shopify_sku"],
            "display_sku": p["display_sku"] or p["shopify_sku"],
            "product_name": p["product_name"],
            "category": p["category"],
            "china_sku1": p["china_sku1"],
            "china_sku2": p["china_sku2"],
            "is_composite": p["is_composite"],
            "lastmile_group": p["lastmile_group"],
            "product_cost": product_cost,
            "agent_fee": agent_fee,
            "domestic_freight": domestic,
            "sea_freight": sea,
            "warehouse_rent": rent,
            "inbound": inbound,
            "local_shipping": lastmile,
            "pick_pack": pick_pack,
            "pink_box": pink_box,
            "other_box": other_box,
            "total_cost": total,
            "landed_cost": landed,
            "missing": missing,
        })
    return out


# ===========================================================================
# Snapshots (cost history)
# ===========================================================================

_SNAPSHOT_COLS = [
    "snapshot_date", "region", "shopify_sku",
    "product_cost", "agent_fee", "domestic_freight", "sea_freight",
    "warehouse_rent", "inbound", "local_shipping",
    "pick_pack", "pink_box", "other_box",
    "total_cost", "landed_cost", "reason",
]


def take_snapshot(region=REGION_US, reason="manual"):
    """
    Snapshot today's assembled costs into cost_snapshots.
    Same-day re-snapshots replace (daily granularity).
    """
    table = assemble_cost_table(region)
    today = date.today().isoformat()
    rows = []
    for r in table:
        rows.append({
            "snapshot_date": today,
            "region": region,
            "shopify_sku": r["shopify_sku"],
            "product_cost": r["product_cost"],
            "agent_fee": r["agent_fee"],
            "domestic_freight": r["domestic_freight"],
            "sea_freight": r["sea_freight"],
            "warehouse_rent": r["warehouse_rent"],
            "inbound": r["inbound"],
            "local_shipping": r["local_shipping"],
            "pick_pack": r["pick_pack"],
            "pink_box": r["pink_box"],
            "other_box": r["other_box"],
            "total_cost": r["total_cost"],
            "landed_cost": r["landed_cost"],
            "reason": reason,
        })
    if not rows:
        return 0
    one = "(" + ",".join("?" * len(_SNAPSHOT_COLS)) + ")"
    col_list = ",".join(_SNAPSHOT_COLS)
    with get_db() as conn:
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            sql = (
                f"INSERT OR REPLACE INTO cost_snapshots ({col_list}) VALUES "
                + ",".join([one] * len(chunk))
            )
            flat = []
            for row in chunk:
                for c in _SNAPSHOT_COLS:
                    flat.append(row.get(c))
            conn.execute(sql, flat)
        conn.commit()
    invalidate_cache()
    return len(rows)


def get_snapshots(region=REGION_US, shopify_skus=None, start_date=None, end_date=None):
    sql = "SELECT * FROM cost_snapshots WHERE region = ?"
    params = [region]
    if shopify_skus:
        placeholders = ",".join("?" * len(shopify_skus))
        sql += f" AND shopify_sku IN ({placeholders})"
        params.extend(_u(s) for s in shopify_skus)
    if start_date:
        sql += " AND snapshot_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND snapshot_date <= ?"
        params.append(end_date)
    sql += " ORDER BY snapshot_date, shopify_sku"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_snapshot_dates(region=REGION_US):
    with get_db() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT snapshot_date, reason, COUNT(*) AS n_skus "
                "FROM cost_snapshots WHERE region = ? "
                "GROUP BY snapshot_date, reason ORDER BY snapshot_date DESC",
                (region,),
            ).fetchall()
        ]


# ===========================================================================
# 3PL billing export classifier (recurring upload)
# ===========================================================================

def classify_billing_export(line_rows):
    """
    Convert raw 3PL billing line rows into order-level classified rows
    ready for insert_lastmile_orders_bulk. Mirrors the Excel
    'Classification' tab logic.

    line_rows: list of dicts with keys:
      order_id, ship_date (ISO str), country, sku, qty, amount,
      has_reversal (bool), cross_month_rebill (bool)
    Rows for the same order_id are aggregated; amounts summed.

    Returns (orders, stats) where stats counts skipped/excluded rows.
    """
    stats = {
        "lines_in": len(line_rows),
        "skipped_reversal": 0,
        "skipped_no_date": 0,
        "skipped_no_order": 0,
    }
    grouped = {}
    for r in line_rows:
        if r.get("has_reversal") or r.get("cross_month_rebill"):
            stats["skipped_reversal"] += 1
            continue
        oid = r.get("order_id")
        if not oid:
            stats["skipped_no_order"] += 1
            continue
        if not r.get("ship_date"):
            stats["skipped_no_date"] += 1
            continue
        g = grouped.setdefault(oid, {
            "order_id": oid,
            "ship_date": r["ship_date"],
            "country": r.get("country"),
            "shipping_cost": 0.0,
            "sku_qty": defaultdict(int),
        })
        g["shipping_cost"] += float(r.get("amount") or 0)
        sku = _u(r.get("sku"))
        if sku:
            g["sku_qty"][sku] += int(r.get("qty") or 0)

    orders = []
    type_counts = defaultdict(int)
    for g in grouped.values():
        skus = set(g["sku_qty"].keys())
        if not skus:
            continue
        otype = classify_order(skus)
        type_counts[otype] += 1
        orders.append({
            "order_id": g["order_id"],
            "ship_date": g["ship_date"],
            "country": g["country"],
            "shipping_cost": round(g["shipping_cost"], 4),
            "sku_key": ";".join(sorted(skus)),
            "main_sku": main_sku_for(skus, otype),
            "order_type": otype,
            "total_qty": sum(g["sku_qty"].values()),
            "num_skus": len(skus),
        })
    stats["orders_out"] = len(orders)
    stats["type_counts"] = dict(type_counts)
    return orders, stats
