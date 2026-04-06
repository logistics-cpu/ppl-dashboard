"""Business logic: growth rates, demand forecasting, stockout calculations."""

from datetime import datetime, timedelta


def weekly_growth_rate(current_units, previous_units):
    """Calculate week-over-week growth rate."""
    if not previous_units or previous_units == 0:
        return None
    return (current_units - previous_units) / previous_units


def daily_demand(units_sold, days=7):
    """Average daily demand from weekly units sold."""
    if days == 0:
        return 0
    return units_sold / days


def adjusted_daily_demand(base_demand, growth_rate):
    """Apply growth rate to base demand for forecasting."""
    if growth_rate is None:
        return base_demand
    return base_demand * (1 + growth_rate)


def closing_stock(opening_stock, units_sold, inbound_qty=0):
    """Calculate closing stock for a week."""
    return opening_stock - units_sold + inbound_qty


def stock_life_days(current_stock, avg_daily_demand):
    """How many days of stock remain at current demand rate."""
    if not avg_daily_demand or avg_daily_demand <= 0:
        return None  # infinite / no demand
    return current_stock / avg_daily_demand


def stockout_date(current_stock, avg_daily_demand, from_date=None):
    """Projected date when stock runs out."""
    days = stock_life_days(current_stock, avg_daily_demand)
    if days is None:
        return None
    if from_date is None:
        from_date = datetime.now().date()
    elif isinstance(from_date, str):
        from_date = datetime.strptime(from_date, "%Y-%m-%d").date()
    return from_date + timedelta(days=int(days))


def suggested_reorder_qty(avg_daily_demand, lead_time_days=60, safety_stock_days=14):
    """Suggest reorder quantity based on demand and lead time."""
    if not avg_daily_demand or avg_daily_demand <= 0:
        return 0
    return int(avg_daily_demand * (lead_time_days + safety_stock_days))


def build_weekly_table(sales_rows, inventory_rows=None):
    """
    Build the weekly tracking table from raw sales data.

    sales_rows: list of dicts with keys: week_start, week_end, units_sold
                sorted by week_start ascending
    inventory_rows: optional list of inventory snapshots for opening stock

    Returns list of dicts with calculated columns added.
    """
    result = []
    prev_units = None
    prev_demand = None

    for i, row in enumerate(sales_rows):
        units = row["units_sold"]
        growth = weekly_growth_rate(units, prev_units)
        demand = daily_demand(units)
        adj_demand = adjusted_daily_demand(demand, growth) if growth is not None else demand

        entry = {
            "week_start": row["week_start"],
            "week_end": row["week_end"],
            "units_sold": units,
            "growth_rate": growth,
            "daily_demand": round(demand, 2),
            "adjusted_daily_demand": round(adj_demand, 2),
        }

        result.append(entry)
        prev_units = units
        prev_demand = demand

    return result
