"""Product matrix and constants for PPL Dashboard."""

import os
from dotenv import load_dotenv

load_dotenv()


def _get_config(key, default=""):
    """Read from Streamlit secrets first, then fall back to env vars."""
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return val
    except Exception:
        pass
    return os.getenv(key, default)


# Shopify API
SHOPIFY_STORE_URL = _get_config("SHOPIFY_STORE_URL")
SHOPIFY_ACCESS_TOKEN = _get_config("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_API_VERSION = _get_config("SHOPIFY_API_VERSION", "2025-10")

# Product matrix
STYLES = ["Long", "7/8", "Short"]
COLORS = ["Black", "Olive Green", "Burgundy"]
SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL"]

# Warehouses (ERP names → display names)
WAREHOUSES = {
    "默认仓库": "China HQ",
    "美国洛杉矶3仓-递四方(新)": "US LA",
    "美国新泽西仓-递四方(新)": "US NJ",
    "加拿大温哥华仓-递四方(新)": "Canada",
    "澳洲悉尼仓-递四方(新)": "Australia",
}

WAREHOUSE_DISPLAY_NAMES = list(WAREHOUSES.values())

# ERP SKU mapping: (color, style) → SKU prefix pattern
# ERP format: {prefix}-{color_code}-{style_code}-{size}
ERP_SKU_MAP = {
    ("Black", "Long"):  ("108731", "blackBB", "high"),
    ("Black", "7/8"):   ("108731", "blackBB", "high7"),
    ("Black", "Short"): ("108731", "blackBB", "highshort"),
    ("Olive Green", "Long"):  ("136181", "armygreen", "high"),
    ("Olive Green", "7/8"):   ("136181", "armygreen", "high7"),
    ("Olive Green", "Short"): ("136181", "armygreen", "highshort"),
    ("Burgundy", "Long"):  ("136181", "wine", "high"),
    ("Burgundy", "7/8"):   ("136181", "wine", "high7"),
    ("Burgundy", "Short"): ("136181", "wine", "highshort"),
}

# Reverse map: ERP SKU prefix → (color, style)
ERP_SKU_REVERSE = {}
for (color, style), (prefix, color_code, style_code) in ERP_SKU_MAP.items():
    sku_prefix = f"{prefix}-{color_code}-{style_code}"
    ERP_SKU_REVERSE[sku_prefix] = (color, style)

# Alternate ERP SKU prefixes (same product, different SKU format)
ERP_SKU_REVERSE["108731-Newblack-highshort"] = ("Black", "Short")

# Shopify SKU mapping
# Shopify format: 108731-pplegging-{style_code}-{color_code}-newlogo-{size}
SHOPIFY_STYLE_MAP = {"full": "Long", "7/8s": "7/8", "short": "Short"}
SHOPIFY_COLOR_MAP = {
    "black": "Black",
    "olivegreen": "Olive Green",
    "green": "Olive Green",
    "burgundy": "Burgundy",
    "red": "Burgundy",
}

# Default settings
DEFAULT_STOCKOUT_THRESHOLD_DAYS = 14
DEFAULT_WARNING_THRESHOLD_DAYS = 30

# Database
_data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(_data_dir, exist_ok=True)
DB_PATH = os.path.join(_data_dir, "ppl_dashboard.db")
