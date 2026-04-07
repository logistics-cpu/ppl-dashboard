"""Orchestrate Shopify data sync — products and weekly sales."""

from datetime import datetime, timedelta
from collections import defaultdict
import pytz
from shopify_client.client import ShopifyClient
from shopify_client.queries import PRODUCTS_QUERY, ORDERS_QUERY
from core.sku_mapper import parse_shopify_sku
from core.database import upsert_weekly_sales, get_db

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


def _process_orders(edges, weekly_totals):
    """Process order edges and accumulate into weekly_totals dict."""
    for edge in edges:
        order = edge["node"]
        # Shopify reports use processedAt (when payment was captured), not createdAt.
        # Convert UTC → store timezone (Sydney) so week assignment matches reports.
        timestamp = order.get("processedAt") or order["createdAt"]
        utc_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        sydney_dt = utc_dt.astimezone(STORE_TZ)
        order_date = sydney_dt.date()
        # Week starts on Tuesday (weekday() returns 0=Mon, so Tue=1)
        days_since_tue = (order_date.weekday() - 1) % 7
        week_start = order_date - timedelta(days=days_since_tue)
        week_end = week_start + timedelta(days=6)

        # Count sold items
        for item_edge in order["lineItems"]["edges"]:
            item = item_edge["node"]
            sku = item.get("sku", "")
            qty = item.get("quantity", 0)

            parsed = parse_shopify_sku(sku)
            if parsed is None:
                continue

            style, color, size = parsed
            key = (style, color, size, week_start.isoformat(), week_end.isoformat())
            weekly_totals[key] += qty

        # Subtract refunded items — attribute to the REFUND's week (not order's week)
        # to match Shopify's "Net items sold" report behavior.
        for refund in order.get("refunds", []):
            refund_ts = refund.get("createdAt")
            if refund_ts:
                refund_utc = datetime.fromisoformat(refund_ts.replace("Z", "+00:00"))
                refund_sydney = refund_utc.astimezone(STORE_TZ)
                refund_date = refund_sydney.date()
                r_days_since_tue = (refund_date.weekday() - 1) % 7
                r_week_start = refund_date - timedelta(days=r_days_since_tue)
                r_week_end = r_week_start + timedelta(days=6)
            else:
                # Fallback to order's week if no refund timestamp
                r_week_start = week_start
                r_week_end = week_end

            for rli_edge in refund.get("refundLineItems", {}).get("edges", []):
                rli = rli_edge["node"]
                refund_sku = rli.get("lineItem", {}).get("sku", "")
                refund_qty = rli.get("quantity", 0)

                parsed = parse_shopify_sku(refund_sku)
                if parsed is None:
                    continue

                style, color, size = parsed
                key = (style, color, size, r_week_start.isoformat(), r_week_end.isoformat())
                weekly_totals[key] -= refund_qty


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

    # Aggregate: (style, color, size, week_start) → total net units
    weekly_totals = defaultdict(int)

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
        _process_orders(edges, weekly_totals)

    # Upsert all weekly records
    count = 0
    for (style, color, size, ws, we), units in weekly_totals.items():
        upsert_weekly_sales(style, color, size, ws, we, max(0, units), source="shopify")
        count += 1

    return count
