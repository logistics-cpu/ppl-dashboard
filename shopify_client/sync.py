"""Orchestrate Shopify data sync — products and weekly sales."""

from datetime import datetime, timedelta
from collections import defaultdict
import pytz
import hashlib
from shopify_client.client import ShopifyClient
from shopify_client.queries import PRODUCTS_QUERY, ORDERS_QUERY, ORDERS_DETAIL_QUERY
from core.sku_mapper import parse_shopify_sku
from core.database import (
    upsert_weekly_sales, get_db,
    upsert_raw_weekly_sales, derive_weekly_sales_from_raw,
    upsert_order, upsert_order_item,
)

# Shopify store timezone — Sydney observes AEDT (UTC+11) Oct-Apr, AEST (UTC+10) Apr-Oct
STORE_TZ = pytz.timezone("Australia/Sydney")


def sync_products():
    """
    Fetch all products from Shopify and update the products table with
    shopify_variant_id and shopify_sku.

    Returns: number of variants mapped.
    """
    client = ShopifyClient()
    edges = client.paginate(
        PRODUCTS_QUERY,
        path_to_edges="products.edges",
        path_to_page_info="products.pageInfo",
    )

    mapped = 0
    with get_db() as conn:
        for edge in edges:
            product = edge["node"]
            for var_edge in product["variants"]["edges"]:
                variant = var_edge["node"]
                sku = variant.get("sku", "")
                variant_id = variant["id"]

                parsed = parse_shopify_sku(sku)
                if parsed is None:
                    continue

                style, color, size = parsed
                conn.execute("""
                    UPDATE products
                    SET shopify_variant_id = ?, shopify_sku = ?
                    WHERE style = ? AND color = ? AND size = ?
                """, (variant_id, sku, style, color, size))
                mapped += 1

    return mapped


def _process_orders(edges, raw_totals):
    """
    Process order edges and accumulate net units (sold - refunded) by raw SKU.

    raw_totals: dict[(shopify_sku, week_start_iso, week_end_iso)] -> int net units
    No SKU filtering happens here — every SKU with non-empty value is captured.
    """
    for edge in edges:
        order = edge["node"]
        # Shopify reports use processedAt (when payment was captured), not createdAt.
        # Convert UTC → store timezone (Sydney) so week assignment matches reports.
        timestamp = order.get("processedAt") or order["createdAt"]
        utc_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        sydney_dt = utc_dt.astimezone(STORE_TZ)
        order_date = sydney_dt.date()
        # Week starts on Monday (weekday() returns 0=Mon)
        days_since_mon = order_date.weekday()
        week_start = order_date - timedelta(days=days_since_mon)
        week_end = week_start + timedelta(days=6)

        # Count sold items (any non-empty SKU)
        for item_edge in order["lineItems"]["edges"]:
            item = item_edge["node"]
            sku = (item.get("sku") or "").strip()
            qty = item.get("quantity", 0)
            if not sku:
                continue
            key = (sku, week_start.isoformat(), week_end.isoformat())
            raw_totals[key] += qty

        # Subtract refunded items — attribute to the REFUND's week (not order's week)
        # to match Shopify's "Net items sold" report behavior.
        for refund in order.get("refunds", []):
            refund_ts = refund.get("createdAt")
            if refund_ts:
                refund_utc = datetime.fromisoformat(refund_ts.replace("Z", "+00:00"))
                refund_sydney = refund_utc.astimezone(STORE_TZ)
                refund_date = refund_sydney.date()
                r_days_since_mon = refund_date.weekday()
                r_week_start = refund_date - timedelta(days=r_days_since_mon)
                r_week_end = r_week_start + timedelta(days=6)
            else:
                # Fallback to order's week if no refund timestamp
                r_week_start = week_start
                r_week_end = week_end

            for rli_edge in refund.get("refundLineItems", {}).get("edges", []):
                rli = rli_edge["node"]
                refund_sku = (rli.get("lineItem", {}).get("sku") or "").strip()
                refund_qty = rli.get("quantity", 0)
                if not refund_sku:
                    continue
                key = (refund_sku, r_week_start.isoformat(), r_week_end.isoformat())
                raw_totals[key] -= refund_qty


def sync_weekly_sales(start_date, end_date):
    """
    Fetch orders from Shopify within date range and aggregate into
    weekly sales per SKU (net items sold = sold - refunded).

    Uses -status:cancelled to include all non-cancelled orders (open + closed)
    and matches Shopify's "Net items sold" report exactly.

    Args:
        start_date: ISO date string (e.g., "2025-12-16")
        end_date: ISO date string (e.g., "2026-03-28")

    Returns: number of weekly sales records upserted.
    """
    client = ShopifyClient()

    # Aggregate: (shopify_sku, week_start, week_end) → net units (NO SKU FILTERING)
    raw_totals = defaultdict(int)

    # Three-pass approach to match Shopify's "Net items sold" report exactly:
    #   Pass 1: financial_status:paid — fully paid orders
    #   Pass 2: financial_status:partially_refunded — orders with some items refunded
    #   Pass 3: financial_status:refunded — fully refunded (net 0, but ensures correct week)
    for fin_status in ("paid", "partially_refunded", "refunded"):
        # Use created_at filter (fast, indexed) with 1-day buffer on each side
        # to catch orders near week boundaries. processedAt is used for week
        # assignment in _process_orders to match Shopify's report dates.
        from datetime import date as date_cls
        buf_start = (date_cls.fromisoformat(start_date) - timedelta(days=1)).isoformat()
        buf_end = (date_cls.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        query_str = f"created_at:>={buf_start} created_at:<={buf_end} financial_status:{fin_status} -status:cancelled"
        edges = client.paginate(
            ORDERS_QUERY,
            variables={"query": query_str},
            path_to_edges="orders.edges",
            path_to_page_info="orders.pageInfo",
        )
        _process_orders(edges, raw_totals)

    # Step 1: write ALL raw SKU data — including unmapped products.
    # This makes future product additions backfill instantly from raw data.
    from datetime import date as date_cls2
    req_start = date_cls2.fromisoformat(start_date)
    req_week_start = req_start - timedelta(days=req_start.weekday())  # Monday of req start

    raw_count = 0
    for (sku, ws, we), units in raw_totals.items():
        if date_cls2.fromisoformat(ws) < req_week_start:
            continue  # Skip weeks before the requested range
        upsert_raw_weekly_sales(sku, ws, we, max(0, units), source="shopify")
        raw_count += 1

    # Step 2: derive weekly_sales from raw data using current SKU mappings.
    # Unmapped SKUs stay in raw_weekly_sales for the SKU Discovery panel.
    derive_weekly_sales_from_raw(start_date=req_week_start.isoformat(), end_date=end_date)

    return raw_count


def _money(node, *path):
    """Safely extract a money amount from nested dict path. Returns float or None."""
    cur = node
    for p in path:
        if not isinstance(cur, dict) or cur.get(p) is None:
            return None
        cur = cur[p]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def _hash_customer_id(customer):
    """Hash the Shopify customer GID for anonymized dedup. Returns None if no customer."""
    if not customer or not customer.get("id"):
        return None
    return hashlib.sha256(customer["id"].encode("utf-8")).hexdigest()[:16]


def sync_orders(start_date, end_date):
    """
    Fetch full order details from Shopify for analytics (geo, channel, basket).

    Stores into `orders` and `order_items` tables. Does NOT modify weekly_sales —
    that's handled by sync_weekly_sales().

    Args:
        start_date: ISO date string (e.g., "2026-05-01")
        end_date: ISO date string (e.g., "2026-05-25")

    Returns: (orders_synced, items_synced)
    """
    client = ShopifyClient()

    from datetime import date as date_cls
    buf_start = (date_cls.fromisoformat(start_date) - timedelta(days=1)).isoformat()
    buf_end = (date_cls.fromisoformat(end_date) + timedelta(days=1)).isoformat()

    orders_synced = 0
    items_synced = 0

    # Three-pass approach to match Shopify's report logic and capture refunded orders too.
    for fin_status in ("paid", "partially_refunded", "refunded"):
        query_str = (
            f"created_at:>={buf_start} created_at:<={buf_end} "
            f"financial_status:{fin_status} -status:cancelled"
        )
        edges = client.paginate(
            ORDERS_DETAIL_QUERY,
            variables={"query": query_str},
            path_to_edges="orders.edges",
            path_to_page_info="orders.pageInfo",
        )

        for edge in edges:
            order = edge["node"]
            # Local date for filtering
            ts = order.get("processedAt") or order["createdAt"]
            utc_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(STORE_TZ)
            local_date = local_dt.date()

            # Skip orders outside requested range (buffer may include extras)
            if local_date.isoformat() < start_date or local_date.isoformat() > end_date:
                continue

            ship = order.get("shippingAddress") or {}
            order_row = {
                "shopify_order_id": order["id"],
                "order_number": order.get("name"),
                "created_at_utc": order["createdAt"],
                "processed_at_utc": order.get("processedAt"),
                "created_at_local": local_date.isoformat(),
                "financial_status": order.get("displayFinancialStatus"),
                "fulfillment_status": order.get("displayFulfillmentStatus"),
                "source_name": order.get("sourceName"),
                "tags": ",".join(order.get("tags") or []),
                "total_price": _money(order, "currentTotalPriceSet", "presentmentMoney", "amount"),
                "subtotal_price": _money(order, "currentSubtotalPriceSet", "presentmentMoney", "amount"),
                "total_discounts": _money(order, "currentTotalDiscountsSet", "presentmentMoney", "amount"),
                "total_tax": _money(order, "currentTotalTaxSet", "presentmentMoney", "amount"),
                "total_shipping": _money(order, "totalShippingPriceSet", "presentmentMoney", "amount"),
                "currency": (order.get("currentTotalPriceSet") or {}).get("presentmentMoney", {}).get("currencyCode"),
                "customer_id_hash": _hash_customer_id(order.get("customer")),
                "ship_country": ship.get("country"),
                "ship_country_code": ship.get("countryCodeV2"),
                "ship_state": ship.get("province"),
                "ship_state_code": ship.get("provinceCode"),
                "ship_city": ship.get("city"),
            }
            upsert_order(order_row)
            orders_synced += 1

            # Line items
            for item_edge in order.get("lineItems", {}).get("edges", []):
                li = item_edge["node"]
                sku = (li.get("sku") or "").strip()
                parsed = parse_shopify_sku(sku) if sku else None
                style, color, size = parsed if parsed else (None, None, None)
                item_row = {
                    "shopify_order_id": order["id"],
                    "line_item_id": li.get("id"),
                    "shopify_sku": sku,
                    "product_title": li.get("name"),
                    "variant_title": li.get("variantTitle"),
                    "quantity": li.get("quantity", 0),
                    "unit_price": _money(li, "originalUnitPriceSet", "presentmentMoney", "amount"),
                    "style": style,
                    "color": color,
                    "size": size,
                }
                upsert_order_item(item_row)
                items_synced += 1

    return (orders_synced, items_synced)
