"""Database schema and CRUD operations — uses Turso (libsql) when configured, else local SQLite."""

import sqlite3
import time
from datetime import datetime
from contextlib import contextmanager
from core.config import DB_PATH, STYLES, COLORS, SIZES

# ---------------------------------------------------------------------------
# Simple query cache — avoids repeated HTTP roundtrips to Turso
# ---------------------------------------------------------------------------
_query_cache = {}
_cache_ttl = 30  # seconds


def _cache_get(key):
    """Return cached value if still valid, else None."""
    entry = _query_cache.get(key)
    if entry and (time.time() - entry[1]) < _cache_ttl:
        return entry[0]
    return None


def _cache_set(key, value):
    """Store value in cache."""
    _query_cache[key] = (value, time.time())


def invalidate_cache():
    """Clear all cached queries (call after writes)."""
    _query_cache.clear()

# ---------------------------------------------------------------------------
# Connection layer — Turso cloud or local SQLite
# ---------------------------------------------------------------------------

def _get_turso_config():
    """Return (url, token) from Streamlit secrets or env vars, or (None, None)."""
    try:
        import streamlit as st
        url = st.secrets.get("TURSO_DB_URL", "")
        token = st.secrets.get("TURSO_AUTH_TOKEN", "")
        if url and token:
            return url, token
    except Exception:
        pass
    import os
    url = os.getenv("TURSO_DB_URL", "")
    token = os.getenv("TURSO_AUTH_TOKEN", "")
    if url and token:
        return url, token
    return None, None


_turso_url, _turso_token = _get_turso_config()
_use_turso = bool(_turso_url and _turso_token)

if _use_turso:
    try:
        import libsql_client
        _turso_available = True
    except ImportError:
        _turso_available = False
        _use_turso = False
else:
    _turso_available = False


class _DictRow:
    """Lightweight dict-like row that works like sqlite3.Row."""
    def __init__(self, keys, values):
        self._data = dict(zip(keys, values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def get(self, key, default=None):
        return self._data.get(key, default)


class _TursoConnWrapper:
    """Wraps libsql_client sync client to behave like sqlite3.Connection."""
    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=()):
        # Convert ? placeholders to positional args for libsql_client
        result = self._client.execute(sql, list(params))
        return _TursoResultWrapper(result)

    def executemany(self, sql, params_list):
        for params in params_list:
            self._client.execute(sql, list(params))

    def executescript(self, sql):
        # Split on semicolons and execute each statement
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._client.execute(stmt)
                except Exception:
                    pass  # Skip empty or comment-only statements

    def commit(self):
        pass  # Turso auto-commits

    def close(self):
        pass  # Reuse connection


class _TursoResultWrapper:
    """Wraps libsql_client result to provide fetchone/fetchall with dict rows."""
    def __init__(self, result):
        self._columns = result.columns if hasattr(result, 'columns') else []
        self._rows = result.rows if hasattr(result, 'rows') else []
        self._idx = 0

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        if self._columns:
            return _DictRow(self._columns, row)
        return row

    def fetchall(self):
        rows = self._rows[self._idx:]
        self._idx = len(self._rows)
        if self._columns:
            return [_DictRow(self._columns, row) for row in rows]
        return rows


# Singleton Turso client
_turso_client = None


def _get_turso_conn():
    global _turso_client
    if _turso_client is None:
        # Convert libsql:// to https:// for HTTP client
        url = _turso_url.replace("libsql://", "https://")
        _turso_client = libsql_client.create_client_sync(
            url=url,
            auth_token=_turso_token,
        )
    return _TursoConnWrapper(_turso_client)


@contextmanager
def get_db():
    if _use_turso:
        conn = _get_turso_conn()
        try:
            yield conn
        except Exception:
            raise
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

_db_initialized = False

def init_db():
    global _db_initialized
    if _db_initialized:
        return
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                style TEXT NOT NULL,
                color TEXT NOT NULL,
                size TEXT NOT NULL,
                shopify_variant_id TEXT,
                shopify_sku TEXT,
                erp_sku TEXT,
                UNIQUE(style, color, size)
            );

            CREATE TABLE IF NOT EXISTS weekly_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                style TEXT NOT NULL,
                color TEXT NOT NULL,
                size TEXT NOT NULL,
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                units_sold INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'shopify',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(style, color, size, week_start)
            );

            CREATE TABLE IF NOT EXISTS raw_weekly_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shopify_sku TEXT NOT NULL,
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                units_sold INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'shopify',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(shopify_sku, week_start, source)
            );

            CREATE TABLE IF NOT EXISTS orders (
                shopify_order_id TEXT PRIMARY KEY,
                order_number TEXT,
                created_at_utc TIMESTAMP NOT NULL,
                processed_at_utc TIMESTAMP,
                created_at_local DATE NOT NULL,
                financial_status TEXT,
                fulfillment_status TEXT,
                source_name TEXT,
                tags TEXT,
                total_price REAL,
                subtotal_price REAL,
                total_discounts REAL,
                total_tax REAL,
                total_shipping REAL,
                currency TEXT,
                customer_id_hash TEXT,
                ship_country TEXT,
                ship_country_code TEXT,
                ship_state TEXT,
                ship_state_code TEXT,
                ship_city TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shopify_order_id TEXT NOT NULL,
                line_item_id TEXT,
                shopify_sku TEXT,
                product_title TEXT,
                variant_title TEXT,
                quantity INTEGER NOT NULL,
                unit_price REAL,
                style TEXT,
                color TEXT,
                size TEXT,
                UNIQUE(shopify_order_id, line_item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_orders_local_date ON orders(created_at_local);
            CREATE INDEX IF NOT EXISTS idx_orders_country ON orders(ship_country_code);
            CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(shopify_order_id);
            CREATE INDEX IF NOT EXISTS idx_order_items_sku ON order_items(shopify_sku);

            CREATE TABLE IF NOT EXISTS dropship_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL,
                paid_at_utc TIMESTAMP,
                paid_at_local DATE,
                status TEXT,
                erp_sku TEXT,
                shopify_sku TEXT,
                quantity INTEGER NOT NULL DEFAULT 0,
                warehouse_raw TEXT,
                warehouse TEXT,
                country_raw TEXT,
                country TEXT,
                region TEXT,
                shipping_carrier TEXT,
                style TEXT,
                color TEXT,
                size TEXT,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_dropship_paid_date ON dropship_orders(paid_at_local);
            CREATE INDEX IF NOT EXISTS idx_dropship_warehouse ON dropship_orders(warehouse);
            CREATE INDEX IF NOT EXISTS idx_dropship_country ON dropship_orders(country);
            CREATE INDEX IF NOT EXISTS idx_dropship_order_num ON dropship_orders(order_number);

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_date DATE,
                year_month TEXT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                category TEXT,
                country TEXT,
                has_invoice INTEGER NOT NULL DEFAULT 0,
                source_file TEXT,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_payments_month ON payments(year_month);
            CREATE INDEX IF NOT EXISTS idx_payments_category ON payments(category);
            CREATE INDEX IF NOT EXISTS idx_payments_country ON payments(country);

            CREATE TABLE IF NOT EXISTS inventory_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                style TEXT NOT NULL,
                color TEXT NOT NULL,
                size TEXT NOT NULL,
                warehouse TEXT NOT NULL,
                stock_qty INTEGER NOT NULL DEFAULT 0,
                available_qty INTEGER NOT NULL DEFAULT 0,
                sales_7d INTEGER NOT NULL DEFAULT 0,
                sales_28d INTEGER NOT NULL DEFAULT 0,
                sales_42d INTEGER NOT NULL DEFAULT 0,
                days_available REAL,
                in_transit_qty INTEGER NOT NULL DEFAULT 0,
                snapshot_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS production_arrivals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                style TEXT NOT NULL,
                color TEXT NOT NULL,
                size TEXT NOT NULL,
                qty INTEGER NOT NULL,
                arrival_date DATE NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS warehouse_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                style TEXT NOT NULL,
                color TEXT NOT NULL,
                size TEXT NOT NULL,
                qty INTEGER NOT NULL,
                from_warehouse TEXT NOT NULL DEFAULT 'China HQ',
                to_warehouse TEXT NOT NULL,
                transfer_date DATE NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT NOT NULL,
                status TEXT NOT NULL,
                records_synced INTEGER DEFAULT 0,
                error_message TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );

            -- ════════════════════════════════════════════════════════════
            -- Product Cost model (migrated from "📦 Product Cost 2026.xlsx")
            -- All SKU columns store UPPERCASE-normalized values; joins are
            -- case-insensitive. region defaults 'US' — other regions later.
            -- ════════════════════════════════════════════════════════════

            -- One row per Shopify SKU (the Excel 'US ' master sheet)
            CREATE TABLE IF NOT EXISTS cost_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                shopify_sku TEXT NOT NULL,
                display_sku TEXT,
                product_name TEXT,
                category TEXT,
                china_sku1 TEXT,
                china_sku2 TEXT,
                is_composite INTEGER NOT NULL DEFAULT 0,
                -- manual $ inputs
                product_cost REAL,
                agent_fee REAL,
                pick_pack REAL,
                pink_box REAL,
                other_box REAL,
                -- when set, used INSTEAD of the computed lookup (composites)
                domestic_override REAL,
                sea_override REAL,
                rent_override REAL,
                inbound_override REAL,
                lastmile_override REAL,
                lastmile_group TEXT NOT NULL DEFAULT 'SINGLE',
                notes TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(region, shopify_sku)
            );

            -- Composite product definitions (forward path for bundles)
            CREATE TABLE IF NOT EXISTS cost_product_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                parent_sku TEXT NOT NULL,
                component_sku TEXT NOT NULL,
                multiplier REAL NOT NULL DEFAULT 1
            );

            -- Per-SKU physical specs (MasterData + SKU Master merged)
            CREATE TABLE IF NOT EXISTS cost_sku_specs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                sku TEXT NOT NULL,
                unit_cbm REAL,
                unit_weight_kg REAL,
                qty_per_ctn REAL,
                cbm_per_ctn REAL,
                vol_weight_ctn REAL,
                rent_unit_cbm REAL,
                assumed_storage_days INTEGER NOT NULL DEFAULT 90,
                in_sku_master INTEGER NOT NULL DEFAULT 0,
                in_rent_table INTEGER NOT NULL DEFAULT 0,
                UNIQUE(region, sku)
            );

            -- Freight shipments: header (totals once) + per-SKU lines
            CREATE TABLE IF NOT EXISTS cost_shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                shipment_id TEXT NOT NULL,
                ship_date DATE,
                dom_total REAL NOT NULL DEFAULT 0,
                sea_total REAL NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(region, shipment_id)
            );
            CREATE TABLE IF NOT EXISTS cost_shipment_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                shipment_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                qty INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cost_shiplines_sku
                ON cost_shipment_lines(sku);
            CREATE INDEX IF NOT EXISTS idx_cost_shiplines_ship
                ON cost_shipment_lines(shipment_id);

            -- Warehouse rent age brackets ($/CBM/day) — editable
            CREATE TABLE IF NOT EXISTS cost_rent_brackets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                start_day REAL NOT NULL,
                end_day REAL,
                rate_per_cbm_day REAL NOT NULL
            );

            -- Inbound op-fee weight tiers — editable
            CREATE TABLE IF NOT EXISTS cost_rate_card (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                tier_start_kg REAL NOT NULL,
                tier_end_kg REAL,
                op_fee REAL NOT NULL
            );

            -- Last-mile: one row per 3PL ORDER (classified billing export)
            CREATE TABLE IF NOT EXISTS cost_lastmile_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL DEFAULT 'US',
                order_id TEXT NOT NULL,
                ship_date DATE NOT NULL,
                country TEXT,
                shipping_cost REAL NOT NULL,
                sku_key TEXT,
                main_sku TEXT,
                order_type TEXT NOT NULL,
                total_qty INTEGER,
                num_skus INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_lastmile_mainsku
                ON cost_lastmile_orders(main_sku);
            CREATE INDEX IF NOT EXISTS idx_lastmile_date
                ON cost_lastmile_orders(ship_date);
            CREATE INDEX IF NOT EXISTS idx_lastmile_type
                ON cost_lastmile_orders(order_type);

            -- Cost history: one row per SKU per day (re-snapshot replaces)
            CREATE TABLE IF NOT EXISTS cost_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date DATE NOT NULL,
                region TEXT NOT NULL DEFAULT 'US',
                shopify_sku TEXT NOT NULL,
                product_cost REAL,
                agent_fee REAL,
                domestic_freight REAL,
                sea_freight REAL,
                warehouse_rent REAL,
                inbound REAL,
                local_shipping REAL,
                pick_pack REAL,
                pink_box REAL,
                other_box REAL,
                total_cost REAL,
                landed_cost REAL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(snapshot_date, region, shopify_sku)
            );
            CREATE INDEX IF NOT EXISTS idx_cost_snap_sku
                ON cost_snapshots(shopify_sku);
        """)
        _seed_products(conn)
        _seed_default_settings(conn)
    _db_initialized = True


def _seed_products(conn):
    from core.config import ERP_SKU_MAP, STYLE_CONFIG, NP_SKU_MAP
    for style, cfg in STYLE_CONFIG.items():
        for color in cfg["colors"]:
            for size in cfg["sizes"]:
                # Determine ERP SKU
                erp_info = ERP_SKU_MAP.get((color, style))
                if erp_info:
                    erp_sku = f"{erp_info[0]}-{erp_info[1]}-{erp_info[2]}-{size}"
                else:
                    # Check NP exact-match map
                    erp_sku = None
                    for sku, (s, c, sz) in NP_SKU_MAP.items():
                        if s == style and c == color and sz == size:
                            erp_sku = sku
                            break
                conn.execute(
                    "INSERT OR IGNORE INTO products (style, color, size, erp_sku) VALUES (?, ?, ?, ?)",
                    (style, color, size, erp_sku),
                )


def _seed_default_settings(conn):
    defaults = {
        "stockout_threshold_days": "14",
        "warning_threshold_days": "30",
        # Product Cost model (US region)
        "cost_us_unload_rate_per_cbm": "6.2",
        "cost_us_default_storage_days": "90",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )


# --- Weekly Sales CRUD ---

def upsert_weekly_sales(style, color, size, week_start, week_end, units_sold, source="shopify"):
    with get_db() as conn:
        # Never overwrite spreadsheet-imported data with Shopify data.
        # Spreadsheet data is manually verified and takes priority.
        if source == "shopify":
            existing = conn.execute("""
                SELECT source FROM weekly_sales
                WHERE style=? AND color=? AND size=? AND week_start=?
            """, (style, color, size, week_start)).fetchone()
            if existing and existing["source"] == "spreadsheet":
                # Allow Shopify to overwrite spreadsheet records with 0 units
                existing_units = conn.execute("""
                    SELECT units_sold FROM weekly_sales
                    WHERE style=? AND color=? AND size=? AND week_start=?
                """, (style, color, size, week_start)).fetchone()
                if existing_units and existing_units["units_sold"] > 0:
                    return  # Protect non-zero spreadsheet data

        conn.execute("""
            INSERT INTO weekly_sales (style, color, size, week_start, week_end, units_sold, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(style, color, size, week_start)
            DO UPDATE SET units_sold=excluded.units_sold, source=excluded.source
        """, (style, color, size, week_start, week_end, units_sold, source))
        invalidate_cache()


# --- Orders & Order Items ---

def upsert_order(order_data):
    """Insert or replace an order row. order_data is a dict matching the orders table columns."""
    cols = [
        "shopify_order_id", "order_number", "created_at_utc", "processed_at_utc",
        "created_at_local", "financial_status", "fulfillment_status", "source_name",
        "tags", "total_price", "subtotal_price", "total_discounts", "total_tax",
        "total_shipping", "currency", "customer_id_hash", "ship_country",
        "ship_country_code", "ship_state", "ship_state_code", "ship_city",
    ]
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO orders ({','.join(cols)}) VALUES ({placeholders})"
    values = tuple(order_data.get(c) for c in cols)
    with get_db() as conn:
        conn.execute(sql, values)


def upsert_order_item(item_data):
    """Insert or replace an order_item row."""
    cols = [
        "shopify_order_id", "line_item_id", "shopify_sku", "product_title",
        "variant_title", "quantity", "unit_price", "style", "color", "size",
    ]
    placeholders = ",".join("?" * len(cols))
    sql = (
        f"INSERT INTO order_items ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(shopify_order_id, line_item_id) DO UPDATE SET "
        f"quantity=excluded.quantity, unit_price=excluded.unit_price, "
        f"style=excluded.style, color=excluded.color, size=excluded.size"
    )
    values = tuple(item_data.get(c) for c in cols)
    with get_db() as conn:
        conn.execute(sql, values)


def get_orders(start_date=None, end_date=None, country=None, limit=500):
    """Fetch orders, optionally filtered. Most recent first."""
    query = "SELECT * FROM orders WHERE 1=1"
    params = []
    if start_date:
        query += " AND created_at_local >= ?"
        params.append(start_date)
    if end_date:
        query += " AND created_at_local <= ?"
        params.append(end_date)
    if country:
        query += " AND ship_country_code = ?"
        params.append(country)
    query += " ORDER BY created_at_local DESC, created_at_utc DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_order_items(shopify_order_id):
    """Fetch all line items for a given order."""
    with get_db() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM order_items WHERE shopify_order_id = ?",
                (shopify_order_id,),
            ).fetchall()
        ]


def get_orders_count(start_date=None, end_date=None):
    """Count orders in date range."""
    query = "SELECT COUNT(*) AS n FROM orders WHERE 1=1"
    params = []
    if start_date:
        query += " AND created_at_local >= ?"
        params.append(start_date)
    if end_date:
        query += " AND created_at_local <= ?"
        params.append(end_date)
    with get_db() as conn:
        row = conn.execute(query, params).fetchone()
        return row["n"] if row else 0


# --- Dropship Orders (uploaded from Excel) ---

# Warehouse Chinese → display name (extends config.WAREHOUSES).
# Both 默认仓库 and 东莞爆品仓 are mapped to "China" so they aggregate together.
DROPSHIP_WAREHOUSE_MAP = {
    "默认仓库": "China",
    "东莞爆品仓": "China",
    "美国新泽西仓-递四方(新)": "US NJ",
    "美国洛杉矶3仓-递四方(新)": "US LA",
    "加拿大温哥华仓-递四方(新)": "Canada",
    "澳洲悉尼仓-递四方(新)": "Australia",
    "美东仓库-中邮海外仓": "US East (China Post)",
    "美西仓库-中邮海外仓": "US West (China Post)",
}

# Common country Chinese → English
DROPSHIP_COUNTRY_MAP = {
    "美国": "United States",
    "英国": "United Kingdom",
    "加拿大": "Canada",
    "澳大利亚": "Australia",
    "荷兰": "Netherlands",
    "爱尔兰": "Ireland",
    "中国香港": "Hong Kong",
    "沙特阿拉伯": "Saudi Arabia",
    "德国": "Germany",
    "葡萄牙": "Portugal",
    "中国台湾": "Taiwan",
    "阿尔巴尼亚": "Albania",
    "新加坡": "Singapore",
    "菲律宾": "Philippines",
    "比利时": "Belgium",
    "法国": "France",
    "西班牙": "Spain",
    "意大利": "Italy",
    "日本": "Japan",
    "韩国": "South Korea",
    "瑞典": "Sweden",
    "挪威": "Norway",
    "丹麦": "Denmark",
    "芬兰": "Finland",
    "波兰": "Poland",
    "新西兰": "New Zealand",
    "墨西哥": "Mexico",
    "巴西": "Brazil",
    "阿联酋": "United Arab Emirates",
    "瑞士": "Switzerland",
    "奥地利": "Austria",
    "捷克": "Czechia",
    "希腊": "Greece",
    "俄罗斯": "Russia",
    "土耳其": "Turkey",
    "印度": "India",
    "中国": "China",
    "马来西亚": "Malaysia",
    "印度尼西亚": "Indonesia",
    "泰国": "Thailand",
    "越南": "Vietnam",
    "南非": "South Africa",
    "以色列": "Israel",
    "马耳他": "Malta",
    "卢森堡": "Luxembourg",
    "斯洛伐克": "Slovakia",
    "匈牙利": "Hungary",
    "罗马尼亚": "Romania",
    "克罗地亚": "Croatia",
    "保加利亚": "Bulgaria",
    "塞尔维亚": "Serbia",
    "乌克兰": "Ukraine",
    "智利": "Chile",
    "阿根廷": "Argentina",
    "哥伦比亚": "Colombia",
    "秘鲁": "Peru",
    "埃及": "Egypt",
    "肯尼亚": "Kenya",
    "尼日利亚": "Nigeria",
}


_DROPSHIP_COLS = [
    "order_number", "paid_at_utc", "paid_at_local", "status",
    "erp_sku", "shopify_sku", "quantity",
    "warehouse_raw", "warehouse", "country_raw", "country", "region",
    "shipping_carrier", "style", "color", "size",
]


def insert_dropship_row(row):
    """Insert a single dropship line item row. row is a dict matching the table columns."""
    placeholders = ",".join("?" * len(_DROPSHIP_COLS))
    sql = f"INSERT INTO dropship_orders ({','.join(_DROPSHIP_COLS)}) VALUES ({placeholders})"
    with get_db() as conn:
        conn.execute(sql, tuple(row.get(c) for c in _DROPSHIP_COLS))


def insert_dropship_rows_bulk(rows, batch_size=200):
    """
    Bulk-insert many dropship rows using multi-VALUES INSERTs.
    Drastically reduces HTTP roundtrips to Turso (1 call per batch instead of per row).

    Args:
        rows: list of dicts matching _DROPSHIP_COLS
        batch_size: number of rows per INSERT statement
    """
    if not rows:
        return 0

    one_row_placeholders = "(" + ",".join("?" * len(_DROPSHIP_COLS)) + ")"
    col_list = ",".join(_DROPSHIP_COLS)
    inserted = 0

    with get_db() as conn:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            multi_values = ",".join([one_row_placeholders] * len(chunk))
            sql = f"INSERT INTO dropship_orders ({col_list}) VALUES {multi_values}"
            flat_params = []
            for row in chunk:
                for c in _DROPSHIP_COLS:
                    flat_params.append(row.get(c))
            conn.execute(sql, flat_params)
            inserted += len(chunk)
    return inserted


def delete_dropship_in_range(start_date, end_date):
    """Delete dropship rows where paid_at_local falls in [start_date, end_date]."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM dropship_orders WHERE paid_at_local BETWEEN ? AND ?",
            (start_date, end_date),
        )
        conn.commit()


def get_dropship_orders(
    start_date=None, end_date=None, warehouse=None, country=None,
    limit=2000,
):
    query = "SELECT * FROM dropship_orders WHERE 1=1"
    params = []
    if start_date:
        query += " AND paid_at_local >= ?"
        params.append(start_date)
    if end_date:
        query += " AND paid_at_local <= ?"
        params.append(end_date)
    if warehouse:
        query += " AND warehouse = ?"
        params.append(warehouse)
    if country:
        query += " AND country = ?"
        params.append(country)
    query += " ORDER BY paid_at_local DESC, order_number DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_dropship_summary(start_date=None, end_date=None):
    """Return aggregated stats for dropship orders in the date range."""
    base = "FROM dropship_orders WHERE 1=1"
    params = []
    if start_date:
        base += " AND paid_at_local >= ?"
        params.append(start_date)
    if end_date:
        base += " AND paid_at_local <= ?"
        params.append(end_date)
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(DISTINCT order_number) AS orders, SUM(quantity) AS units {base}",
            params,
        ).fetchone()
        by_wh = conn.execute(
            f"SELECT warehouse, COUNT(DISTINCT order_number) AS orders, SUM(quantity) AS units "
            f"{base} GROUP BY warehouse ORDER BY orders DESC",
            params,
        ).fetchall()
        by_country = conn.execute(
            f"SELECT country, COUNT(DISTINCT order_number) AS orders, SUM(quantity) AS units "
            f"{base} GROUP BY country ORDER BY orders DESC LIMIT 20",
            params,
        ).fetchall()
        return {
            "total_orders": (total["orders"] or 0) if total else 0,
            "total_units": (total["units"] or 0) if total else 0,
            "by_warehouse": [dict(r) for r in by_wh],
            "by_country": [dict(r) for r in by_country],
        }


def clear_all_dropship_orders():
    """Delete ALL dropship rows."""
    with get_db() as conn:
        conn.execute("DELETE FROM dropship_orders")
        conn.commit()


# --- Payments (invoice tracking from finance Excel) ---

# Category registry — based on the 'Categories' sheet of the legacy tracker.
# has_invoice indicates whether the China agency issues a formal invoice for it.
PAYMENT_CATEGORIES = {
    "Stock payments":              {"has_invoice": False, "is_stock_payment": True},
    "Product costs":               {"has_invoice": False},
    "Sea/Air Freight":             {"has_invoice": False},
    "4PX Invoice":                 {"has_invoice": True},
    "Dropshipping Invoice":        {"has_invoice": True},
    "Fedex Private Address Fee":   {"has_invoice": True},
    "Inbound":                     {"has_invoice": False},
    "Refunds":                     {"has_invoice": False},
    "Rent":                        {"has_invoice": False},
    "Supplies":                    {"has_invoice": False},
    "Other Cost - Packaging Cost": {"has_invoice": False},
}

# Country codes used in the legacy Excel
PAYMENT_COUNTRY_MAP = {
    "US": "US",
    "USA": "US",
    "United States": "US",
    "UK": "UK",
    "United Kingdom": "UK",
    "GB": "UK",
    "Great Britain": "UK",
    "CA": "CA",
    "Canada": "CA",
    "AUS": "AU",
    "AU": "AU",
    "Australia": "AU",
    "Other": "Other",
}


def insert_payment_rows_bulk(rows, batch_size=200):
    """Bulk insert payment rows (one INSERT per batch_size rows)."""
    if not rows:
        return 0
    cols = [
        "payment_date", "year_month", "amount", "description",
        "category", "country", "has_invoice", "source_file",
    ]
    one = "(" + ",".join("?" * len(cols)) + ")"
    inserted = 0
    with get_db() as conn:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            multi = ",".join([one] * len(chunk))
            sql = f"INSERT INTO payments ({','.join(cols)}) VALUES {multi}"
            flat = []
            for r in chunk:
                for c in cols:
                    flat.append(r.get(c))
            conn.execute(sql, flat)
            inserted += len(chunk)
    return inserted


def delete_payments_in_range(start_ym, end_ym):
    """Delete payment rows where year_month is between start_ym and end_ym (YYYY-MM)."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM payments WHERE year_month BETWEEN ? AND ?",
            (start_ym, end_ym),
        )
        conn.commit()


def get_payments(start_ym=None, end_ym=None, category=None, country=None,
                 include_stock=True, limit=5000):
    """Fetch payment rows, optionally filtered."""
    query = "SELECT * FROM payments WHERE 1=1"
    params = []
    if start_ym:
        query += " AND year_month >= ?"
        params.append(start_ym)
    if end_ym:
        query += " AND year_month <= ?"
        params.append(end_ym)
    if category:
        query += " AND category = ?"
        params.append(category)
    if country:
        query += " AND country = ?"
        params.append(country)
    if not include_stock:
        query += " AND category != 'Stock payments'"
    query += " ORDER BY year_month DESC, payment_date DESC LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_payment_summary_by_category(start_ym=None, end_ym=None,
                                     include_stock=True):
    """Return total amount per category in the date range."""
    where = "1=1"
    params = []
    if start_ym:
        where += " AND year_month >= ?"
        params.append(start_ym)
    if end_ym:
        where += " AND year_month <= ?"
        params.append(end_ym)
    if not include_stock:
        where += " AND category != 'Stock payments'"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT category, SUM(amount) AS total, COUNT(*) AS n "
            f"FROM payments WHERE {where} GROUP BY category "
            f"ORDER BY ABS(SUM(amount)) DESC",
            params,
        ).fetchall()]


def get_payment_summary_by_month_category(start_ym=None, end_ym=None,
                                           include_stock=True):
    """Return rows of (year_month, category, total) for monthly comparison."""
    where = "1=1"
    params = []
    if start_ym:
        where += " AND year_month >= ?"
        params.append(start_ym)
    if end_ym:
        where += " AND year_month <= ?"
        params.append(end_ym)
    if not include_stock:
        where += " AND category != 'Stock payments'"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT year_month, category, SUM(amount) AS total "
            f"FROM payments WHERE {where} GROUP BY year_month, category "
            f"ORDER BY year_month",
            params,
        ).fetchall()]


def get_payment_summary_by_month_country(start_ym=None, end_ym=None,
                                          include_stock=True):
    """Return rows of (year_month, country, total) for country trends."""
    where = "1=1"
    params = []
    if start_ym:
        where += " AND year_month >= ?"
        params.append(start_ym)
    if end_ym:
        where += " AND year_month <= ?"
        params.append(end_ym)
    if not include_stock:
        where += " AND category != 'Stock payments'"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT year_month, COALESCE(country, 'Unknown') AS country, "
            f"SUM(amount) AS total FROM payments WHERE {where} "
            f"GROUP BY year_month, country ORDER BY year_month",
            params,
        ).fetchall()]


def get_payment_available_months():
    """Return list of YYYY-MM months that have payment data."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT year_month FROM payments ORDER BY year_month DESC"
        ).fetchall()
    return [r["year_month"] for r in rows if r["year_month"]]


def clear_all_payments():
    """Delete ALL payment rows."""
    with get_db() as conn:
        conn.execute("DELETE FROM payments")
        conn.commit()


# --- Standard dropship reporting rules (from legacy Excel tracker) ---

DROPSHIP_TARGET_COUNTRIES = ["United States", "Canada", "Australia"]
DROPSHIP_TARGET_COUNTRY_LABELS = {
    "United States": "US",
    "Canada": "CA",
    "Australia": "AU",
}
# Excluded from US numbers — these regions aren't part of the dropship target.
DROPSHIP_EXCLUDED_REGIONS = ("Hawaii", "Alaska", "Puerto Rico")


def _dropship_standard_where():
    """SQL WHERE fragment + params applying the standard dropship rules:
       - China warehouse only
       - Destinations limited to US / CA / AU
       - US excludes Hawaii, Alaska, Puerto Rico
    """
    placeholders = ",".join("?" * len(DROPSHIP_TARGET_COUNTRIES))
    excl_placeholders = ",".join("?" * len(DROPSHIP_EXCLUDED_REGIONS))
    where = (
        f"warehouse = 'China' "
        f"AND country IN ({placeholders}) "
        f"AND NOT (country = 'United States' AND region IN ({excl_placeholders}))"
    )
    params = list(DROPSHIP_TARGET_COUNTRIES) + list(DROPSHIP_EXCLUDED_REGIONS)
    return where, params


def get_dropship_available_months():
    """Return distinct YYYY-MM months that have dropship data (under standard rules)."""
    where, params = _dropship_standard_where()
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT substr(paid_at_local, 1, 7) AS ym "
            f"FROM dropship_orders WHERE {where} AND paid_at_local IS NOT NULL "
            f"ORDER BY ym DESC",
            params,
        ).fetchall()
    return [r["ym"] for r in rows if r["ym"]]


def get_dropship_monthly_breakdown(start_date=None, end_date=None):
    """
    Return rows of (year_month, country, units) under standard dropship rules.
    Useful for the historical trend chart.
    """
    where, params = _dropship_standard_where()
    if start_date:
        where += " AND paid_at_local >= ?"
        params.append(start_date)
    if end_date:
        where += " AND paid_at_local <= ?"
        params.append(end_date)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT substr(paid_at_local, 1, 7) AS year_month, country, "
            f"SUM(quantity) AS units "
            f"FROM dropship_orders WHERE {where} AND paid_at_local IS NOT NULL "
            f"GROUP BY year_month, country ORDER BY year_month",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_dropship_vs_local_monthly(start_date=None, end_date=None):
    """
    Compare dropshipped (from China) vs locally shipped units for each
    target destination (US / CA / AU), grouped by month.

    Local warehouse mapping:
      US destination → US LA, US NJ, US East (China Post), US West (China Post)
      CA destination → Canada warehouse
      AU destination → Australia warehouse
      China warehouse → always Dropship

    HI / AK / PR still excluded from US destination (matches the rest of the page).

    Returns rows of (year_month, country, origin_type, units) where
    origin_type is 'Dropship' or 'Local'.
    """
    where = (
        "country IN ('United States', 'Canada', 'Australia') "
        "AND NOT (country = 'United States' "
        "         AND region IN ('Hawaii', 'Alaska', 'Puerto Rico'))"
    )
    params = []
    if start_date:
        where += " AND paid_at_local >= ?"
        params.append(start_date)
    if end_date:
        where += " AND paid_at_local <= ?"
        params.append(end_date)

    # Classify each row's origin as Dropship / Local / Other.
    # Only "Dropship" and "Local" rows are useful — "Other" (e.g. US order from
    # Australia warehouse) is rare/edge-case and is filtered out in Python.
    sql = f"""
        SELECT substr(paid_at_local, 1, 7) AS year_month,
               country,
               CASE
                   WHEN warehouse = 'China' THEN 'Dropship'
                   WHEN country = 'United States'
                        AND warehouse IN ('US LA', 'US NJ',
                                          'US East (China Post)',
                                          'US West (China Post)') THEN 'Local'
                   WHEN country = 'Canada' AND warehouse = 'Canada' THEN 'Local'
                   WHEN country = 'Australia' AND warehouse = 'Australia' THEN 'Local'
                   ELSE 'Other'
               END AS origin_type,
               SUM(quantity) AS units
        FROM dropship_orders
        WHERE {where} AND paid_at_local IS NOT NULL
        GROUP BY year_month, country, origin_type
        ORDER BY year_month
    """
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows if r["origin_type"] in ("Dropship", "Local")]


# Shared classification CASE — used by both per-SKU and summary queries.
_LVD_CLASSIFICATION = """
    CASE
        WHEN warehouse IN ('US LA', 'US NJ', 'US East (China Post)', 'US West (China Post)')
             AND country = 'United States'
             AND (region IS NULL OR region NOT IN ('Hawaii', 'Alaska', 'Puerto Rico'))
            THEN 'Local'
        WHEN warehouse = 'Canada' AND country = 'Canada' THEN 'Local'
        WHEN warehouse = 'Australia' AND country = 'Australia' THEN 'Local'
        ELSE 'Dropship'
    END
"""


def get_local_vs_dropship_by_sku(start_ym, end_ym=None, limit=500):
    """
    Per-SKU local vs dropship breakdown for a YYYY-MM range.

    Grouped by ERP SKU only — the ERP SKU represents what was ACTUALLY
    picked from the warehouse. The Shopify SKU in the ERP data often
    points to an upsell variant that triggered the order rather than the
    physical product, so we ignore it for counting purposes (just show
    the dominant Shopify SKU for reference).

    Classification rules:
      LOCAL = warehouse-home shipping to its home country and NOT
              Hawaii / Alaska / Puerto Rico.
      DROPSHIP = everything else — China origin to anywhere, any warehouse
              to HI/AK/PR, or cross-region.

    Returns rows: erp_sku, shopify_sku, local_units, dropship_units,
                   total_units, dropship_pct
    """
    end_ym = end_ym or start_ym
    sql = f"""
        WITH base AS (
            SELECT
                COALESCE(NULLIF(erp_sku, ''), '(no ERP SKU)') AS erp_sku,
                shopify_sku,
                {_LVD_CLASSIFICATION} AS origin,
                quantity
            FROM dropship_orders
            WHERE substr(paid_at_local, 1, 7) BETWEEN ? AND ?
        ),
        dominant AS (
            -- Pick the most-used Shopify SKU per ERP SKU just for the
            -- display column. Push NULL / nan / 'no platformsku' to the
            -- end so they don't win unless they're all we have.
            SELECT erp_sku, shopify_sku
            FROM (
                SELECT erp_sku, shopify_sku,
                       ROW_NUMBER() OVER (
                           PARTITION BY erp_sku
                           ORDER BY
                               CASE
                                   WHEN shopify_sku IS NULL
                                        OR shopify_sku = ''
                                        OR LOWER(shopify_sku) IN ('nan', 'no platformsku')
                                   THEN 1 ELSE 0
                               END,
                               SUM(quantity) DESC
                       ) AS rn
                FROM base
                GROUP BY erp_sku, shopify_sku
            )
            WHERE rn = 1
        )
        SELECT
            b.erp_sku,
            d.shopify_sku,
            SUM(CASE WHEN b.origin = 'Local' THEN b.quantity ELSE 0 END) AS local_units,
            SUM(CASE WHEN b.origin = 'Dropship' THEN b.quantity ELSE 0 END) AS dropship_units,
            SUM(b.quantity) AS total_units
        FROM base b
        LEFT JOIN dominant d ON d.erp_sku = b.erp_sku
        GROUP BY b.erp_sku, d.shopify_sku
        HAVING SUM(b.quantity) > 0
        ORDER BY total_units DESC
        LIMIT ?
    """
    with get_db() as conn:
        rows = conn.execute(sql, (start_ym, end_ym, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        total = d["total_units"] or 0
        d["dropship_pct"] = (d["dropship_units"] / total * 100) if total else 0
        out.append(d)
    return out


def get_local_vs_dropship_summary(start_ym, end_ym=None):
    """
    Aggregate Local vs Dropship totals for a YYYY-MM range.
    Same classification rules as the per-SKU function.
    """
    end_ym = end_ym or start_ym
    sql = f"""
        SELECT
            SUM(CASE WHEN {_LVD_CLASSIFICATION} = 'Local' THEN quantity ELSE 0 END) AS local_units,
            SUM(CASE WHEN {_LVD_CLASSIFICATION} = 'Dropship' THEN quantity ELSE 0 END) AS dropship_units,
            SUM(quantity) AS total_units,
            COUNT(DISTINCT erp_sku) AS sku_count
        FROM dropship_orders
        WHERE substr(paid_at_local, 1, 7) BETWEEN ? AND ?
    """
    with get_db() as conn:
        r = conn.execute(sql, (start_ym, end_ym)).fetchone()
    return dict(r) if r else {}


def get_dropship_sku_breakdown_for_month(year_month):
    """
    Return SKU × country breakdown for a single month (YYYY-MM string).
    One row per (erp_sku, shopify_sku, country).
    """
    where, params = _dropship_standard_where()
    where += " AND substr(paid_at_local, 1, 7) = ?"
    params.append(year_month)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT erp_sku, shopify_sku, country, SUM(quantity) AS units "
            f"FROM dropship_orders WHERE {where} "
            f"GROUP BY erp_sku, shopify_sku, country",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# --- Raw Weekly Sales (all SKUs, including unmapped) ---

def upsert_raw_weekly_sales(shopify_sku, week_start, week_end, units_sold, source="shopify"):
    """Insert or update a raw SKU-level weekly sales row."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO raw_weekly_sales (shopify_sku, week_start, week_end, units_sold, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(shopify_sku, week_start, source)
            DO UPDATE SET units_sold=excluded.units_sold, week_end=excluded.week_end
        """, (shopify_sku, week_start, week_end, units_sold, source))
        invalidate_cache()


def get_unmapped_raw_skus(start_date=None, end_date=None, limit=50):
    """
    Return unmapped SKUs from raw_weekly_sales sorted by total units desc.
    A SKU is "unmapped" if parse_shopify_sku returns None.

    Returns list of dicts: {shopify_sku, total_units, weeks_seen, last_week}
    """
    from core.sku_mapper import parse_shopify_sku

    with get_db() as conn:
        query = """
            SELECT shopify_sku, SUM(units_sold) AS total_units,
                   COUNT(DISTINCT week_start) AS weeks_seen,
                   MAX(week_start) AS last_week
            FROM raw_weekly_sales
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND week_start >= ?"
            params.append(start_date)
        if end_date:
            query += " AND week_start <= ?"
            params.append(end_date)
        query += " GROUP BY shopify_sku ORDER BY total_units DESC"
        rows = conn.execute(query, params).fetchall()

    # Filter to only unmapped SKUs
    unmapped = []
    for r in rows:
        sku = r["shopify_sku"]
        if parse_shopify_sku(sku) is None:
            unmapped.append({
                "shopify_sku": sku,
                "total_units": r["total_units"],
                "weeks_seen": r["weeks_seen"],
                "last_week": r["last_week"],
            })
        if len(unmapped) >= limit:
            break
    return unmapped


def derive_weekly_sales_from_raw(start_date=None, end_date=None):
    """
    Re-derive weekly_sales from raw_weekly_sales using current SKU mappings.

    For each raw row, attempts to parse the SKU. If mappable, upserts to weekly_sales.
    Aggregates across multiple SKUs that map to the same (style, color, size).

    Returns: (mapped_count, unmapped_count) — counts of distinct SKUs.
    """
    from core.sku_mapper import parse_shopify_sku
    from collections import defaultdict

    with get_db() as conn:
        query = "SELECT shopify_sku, week_start, week_end, units_sold, source FROM raw_weekly_sales WHERE 1=1"
        params = []
        if start_date:
            query += " AND week_start >= ?"
            params.append(start_date)
        if end_date:
            query += " AND week_start <= ?"
            params.append(end_date)
        rows = conn.execute(query, params).fetchall()

    # Aggregate by (style, color, size, week_start, week_end)
    agg = defaultdict(int)
    mapped_skus = set()
    unmapped_skus = set()
    for r in rows:
        sku = r["shopify_sku"]
        parsed = parse_shopify_sku(sku)
        if parsed is None:
            unmapped_skus.add(sku)
            continue
        mapped_skus.add(sku)
        style, color, size = parsed
        key = (style, color, size, r["week_start"], r["week_end"])
        agg[key] += r["units_sold"]

    # Upsert aggregated values
    for (style, color, size, ws, we), units in agg.items():
        upsert_weekly_sales(style, color, size, ws, we, max(0, units), source="shopify")

    return (len(mapped_skus), len(unmapped_skus))


def get_weekly_sales(style=None, color=None, size=None, start_date=None, end_date=None):
    """Fetch weekly sales records, optionally filtered."""
    cache_key = ("weekly_sales", style, color, size, start_date, end_date)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    query = "SELECT * FROM weekly_sales WHERE 1=1"
    params = []
    if style:
        query += " AND style = ?"
        params.append(style)
    if color:
        query += " AND color = ?"
        params.append(color)
    if size:
        query += " AND size = ?"
        params.append(size)
    if start_date:
        query += " AND week_start >= ?"
        params.append(start_date)
    if end_date:
        query += " AND week_start <= ?"
        params.append(end_date)
    query += " ORDER BY week_start, size"
    with get_db() as conn:
        result = [dict(r) for r in conn.execute(query, params).fetchall()]
    _cache_set(cache_key, result)
    return result


# --- Inventory Snapshots CRUD ---

def insert_inventory_snapshot(records):
    """Insert a batch of inventory snapshot records."""
    with get_db() as conn:
        conn.executemany("""
            INSERT INTO inventory_snapshots
            (style, color, size, warehouse, stock_qty, available_qty,
             sales_7d, sales_28d, sales_42d, days_available, in_transit_qty, snapshot_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
    invalidate_cache()


def get_latest_inventory(warehouse=None):
    """Get the most recent inventory snapshot."""
    cache_key = ("latest_inventory", warehouse)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    query = """
        SELECT i.* FROM inventory_snapshots i
        INNER JOIN (
            SELECT style, color, size, warehouse, MAX(snapshot_date) as max_date
            FROM inventory_snapshots
            GROUP BY style, color, size, warehouse
        ) latest ON i.style = latest.style AND i.color = latest.color
            AND i.size = latest.size AND i.warehouse = latest.warehouse
            AND i.snapshot_date = latest.max_date
    """
    params = []
    if warehouse:
        query = query.replace("GROUP BY", f"WHERE warehouse = ? GROUP BY")
        params.append(warehouse)
    query += " ORDER BY i.color, i.style, i.size"
    with get_db() as conn:
        result = [dict(r) for r in conn.execute(query, params).fetchall()]
    _cache_set(cache_key, result)
    return result


# --- Production Arrivals CRUD ---

def add_production_arrival(style, color, size, qty, arrival_date, notes=""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO production_arrivals (style, color, size, qty, arrival_date, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (style, color, size, qty, arrival_date, notes),
        )


def get_production_arrivals(limit=50):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM production_arrivals ORDER BY arrival_date DESC LIMIT ?", (limit,)
        ).fetchall()]


def delete_production_arrival(record_id):
    with get_db() as conn:
        conn.execute("DELETE FROM production_arrivals WHERE id = ?", (record_id,))


# --- Warehouse Transfers CRUD ---

def add_warehouse_transfer(style, color, size, qty, to_warehouse, transfer_date, notes=""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO warehouse_transfers (style, color, size, qty, to_warehouse, transfer_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (style, color, size, qty, to_warehouse, transfer_date, notes),
        )


def get_warehouse_transfers(limit=50):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM warehouse_transfers ORDER BY transfer_date DESC LIMIT ?", (limit,)
        ).fetchall()]


def delete_warehouse_transfer(record_id):
    with get_db() as conn:
        conn.execute("DELETE FROM warehouse_transfers WHERE id = ?", (record_id,))


# --- Sync Log ---

def log_sync(sync_type, status, records_synced=0, error_message=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sync_log (sync_type, status, records_synced, error_message, completed_at) VALUES (?, ?, ?, ?, ?)",
            (sync_type, status, records_synced, error_message, datetime.now().isoformat()),
        )


def clear_all_sales():
    """Delete ALL weekly sales data (both derived and raw)."""
    with get_db() as conn:
        conn.execute("DELETE FROM weekly_sales")
        conn.execute("DELETE FROM raw_weekly_sales")
        conn.commit()
    invalidate_cache()


def clear_all_orders():
    """Delete ALL order-level data."""
    with get_db() as conn:
        conn.execute("DELETE FROM order_items")
        conn.execute("DELETE FROM orders")
        conn.commit()
    invalidate_cache()


def clear_all_inventory():
    """Delete ALL inventory snapshots."""
    with get_db() as conn:
        conn.execute("DELETE FROM inventory_snapshots")
        conn.commit()


def clear_all_data():
    """Delete ALL data: sales, raw, orders, inventory, arrivals, transfers, sync logs."""
    with get_db() as conn:
        conn.execute("DELETE FROM weekly_sales")
        conn.execute("DELETE FROM raw_weekly_sales")
        conn.execute("DELETE FROM order_items")
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM dropship_orders")
        conn.execute("DELETE FROM inventory_snapshots")
        conn.execute("DELETE FROM production_arrivals")
        conn.execute("DELETE FROM warehouse_transfers")
        conn.execute("DELETE FROM sync_log")
        conn.commit()
    invalidate_cache()


def get_last_sync(sync_type=None):
    query = "SELECT * FROM sync_log"
    params = []
    if sync_type:
        query += " WHERE sync_type = ?"
        params.append(sync_type)
    query += " ORDER BY id DESC LIMIT 1"
    with get_db() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None
