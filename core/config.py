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

# ---------------------------------------------------------------------------
# Product registry — per-style configuration
# ---------------------------------------------------------------------------
PRODUCT_GROUPS = {
    "PPL": ["Long", "7/8", "Short"],
    "Nursing Pillow": ["Nursing Pillow"],
    "Hydration": ["Hydration"],
}

STYLE_CONFIG = {
    "Long":           {"colors": ["Black", "Olive Green", "Burgundy"], "sizes": ["XS", "S", "M", "L", "XL", "2XL", "3XL"]},
    "7/8":            {"colors": ["Black", "Olive Green", "Burgundy"], "sizes": ["XS", "S", "M", "L", "XL", "2XL", "3XL"]},
    "Short":          {"colors": ["Black", "Olive Green", "Burgundy"], "sizes": ["XS", "S", "M", "L", "XL", "2XL", "3XL"]},
    "Nursing Pillow": {"colors": ["\u2014"], "sizes": ["Large", "Set"]},
    "Hydration":      {"colors": ["\u2014"], "sizes": ["Passionfruit Orange", "Lemonade", "Lemon Lime", "Variety Pack 15", "Variety Pack 30"]},
}

ALL_STYLES = list(STYLE_CONFIG.keys())


def get_colors(style):
    """Return the color list for a given style."""
    return STYLE_CONFIG.get(style, {}).get("colors", [])


def get_sizes(style):
    """Return the size list for a given style."""
    return STYLE_CONFIG.get(style, {}).get("sizes", [])


# Union of all sizes across all styles (for ordering)
ALL_SIZES = []
for _cfg in STYLE_CONFIG.values():
    for _s in _cfg["sizes"]:
        if _s not in ALL_SIZES:
            ALL_SIZES.append(_s)

# PPL-only aliases (backward compatibility)
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

# Nursing Pillow SKU mapping (exact-match, same for Shopify and ERP)
NP_SKU_MAP = {
    "J11268-breastfeeding-pillow-Large": ("Nursing Pillow", "\u2014", "Large"),
    "J11268-breastfeeding-pillow-Set":   ("Nursing Pillow", "\u2014", "Set"),
}

# Hydration SKU mapping (exact-match)
# Shopify SKUs use "bph-*" prefix; ERP SKUs use "J27385-*" prefix.
# The bare "bph" SKU is ignored (error SKU per business rules).
HYDRATION_SKU_MAP = {
    # Shopify
    "bph-passionfruitorange": ("Hydration", "\u2014", "Passionfruit Orange"),
    "bph-lemonade":           ("Hydration", "\u2014", "Lemonade"),
    "bph-lemonandlime":       ("Hydration", "\u2014", "Lemon Lime"),
    "bph-variety15":          ("Hydration", "\u2014", "Variety Pack 15"),
    "bph-variety30":          ("Hydration", "\u2014", "Variety Pack 30"),
    # ERP
    "J27385-orange":          ("Hydration", "\u2014", "Passionfruit Orange"),
    "J27385-lemonade":        ("Hydration", "\u2014", "Lemonade"),
    "J27385-lemolime":        ("Hydration", "\u2014", "Lemon Lime"),
    "J27385-mix":             ("Hydration", "\u2014", "Variety Pack 15"),
    "J27385-mix-30":          ("Hydration", "\u2014", "Variety Pack 30"),
}

# Default settings
DEFAULT_STOCKOUT_THRESHOLD_DAYS = 14
DEFAULT_WARNING_THRESHOLD_DAYS = 30

# Database
_data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(_data_dir, exist_ok=True)
DB_PATH = os.path.join(_data_dir, "ppl_dashboard.db")
