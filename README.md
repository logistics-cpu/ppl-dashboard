# PPL Sales Dashboard

Weekly sales tracking and inventory management for PPL Postpartum Recovery Leggings.

## Quick Start

```bash
cd ~/Desktop/ppl-dashboard
./run.sh
```

Or manually:

```bash
pip3 install streamlit pandas plotly openpyxl python-dotenv requests
streamlit run app.py
```

The dashboard opens at http://localhost:8501

## Setup Shopify API (one-time)

1. Go to Shopify Admin → Settings → Apps and sales channels → Develop apps
2. Click "Create an app" → name it "PPL Dashboard"
3. Configure Admin API scopes: `read_products`, `read_orders`
4. Click "Install app"
5. Copy the Admin API access token (shown only once!)
6. Create a `.env` file in this folder:

```
SHOPIFY_STORE_URL=your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_your_token_here
```

7. Restart the dashboard and go to Data Management → Shopify Sync → click "Sync Now"

## Pages

- **Weekly Sales** — Units sold per SKU per week, with growth % and daily demand
- **Inventory Snapshot** — Current stock levels from ERP upload, with stock life and stockout dates
- **Stockout Alerts** — SKUs at risk, sorted by urgency, with suggested reorder quantities
- **Trends** — Sales charts, inventory distribution, demand heatmap
- **Data Management** — Shopify sync, ERP upload, production arrivals, warehouse transfers, settings

## Data Flow

1. **Sales data**: Shopify API → auto-pulled weekly (click "Sync Now")
2. **Inventory data**: Upload ERP Excel export (from your ERP system)
3. **Inbound shipments**: Manual entry for production arrivals and warehouse transfers

## Product Matrix

- 3 Styles: Long, 7/8, Short
- 3 Colors: Black, Olive Green, Burgundy
- 7 Sizes: XS, S, M, L, XL, 2XL, 3XL
- = 63 SKUs total
