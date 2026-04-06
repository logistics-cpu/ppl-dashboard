"""Database schema and CRUD operations — uses Turso (libsql) when configured, else local SQLite."""

import sqlite3
from datetime import datetime
from contextlib import contextmanager
from core.config import DB_PATH, STYLES, COLORS, SIZES

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

def init_db():
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
        """)
        _seed_products(conn)
        _seed_default_settings(conn)


def _seed_products(conn):
    from core.config import ERP_SKU_MAP
    existing = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if existing > 0:
        return
    for style in STYLES:
        for color in COLORS:
            for size in SIZES:
                erp_info = ERP_SKU_MAP.get((color, style))
                erp_sku = f"{erp_info[0]}-{erp_info[1]}-{erp_info[2]}-{size}" if erp_info else None
                conn.execute(
                    "INSERT INTO products (style, color, size, erp_sku) VALUES (?, ?, ?, ?)",
                    (style, color, size, erp_sku),
                )


def _seed_default_settings(conn):
    defaults = {
        "stockout_threshold_days": "14",
        "warning_threshold_days": "30",
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


def get_weekly_sales(style=None, color=None, size=None, start_date=None, end_date=None):
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
        return [dict(r) for r in conn.execute(query, params).fetchall()]


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


def get_latest_inventory(warehouse=None):
    """Get the most recent inventory snapshot."""
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
        return [dict(r) for r in conn.execute(query, params).fetchall()]


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
