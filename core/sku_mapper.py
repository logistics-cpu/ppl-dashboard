"""Map Shopify and ERP SKUs to unified (style, color, size) tuples."""

import re
from core.config import (
    SHOPIFY_STYLE_MAP, SHOPIFY_COLOR_MAP,
    ERP_SKU_REVERSE, SIZES, NP_SKU_MAP, HYDRATION_SKU_MAP,
)


def parse_shopify_sku(sku):
    """
    Parse a Shopify SKU string into (style, color, size).

    Supported formats:
      PPL:
        108731-pplegging-full-black-newlogo-M       → (Long, Black, M)
        108731-pplegging-7/8s-green-newlogo-L       → (7/8, Olive Green, L)
        108731-Newblack-highshort-XS                 → (Short, Black, XS)
        108731-Newblack-high-M                       → (Long, Black, M)
        108731-Newblack-high7-L                      → (7/8, Black, L)
      Nursing Pillow:
        J11268-breastfeeding-pillow-Large            → (Nursing Pillow, —, Large)
        J11268-breastfeeding-pillow-Set              → (Nursing Pillow, —, Set)

    Returns (style, color, size) or None if can't parse.
    """
    if not sku:
        return None

    # --- Nursing Pillow (exact match) ---
    np_result = NP_SKU_MAP.get(sku)
    if np_result:
        return np_result

    # --- Hydration (exact match) ---
    hyd_result = HYDRATION_SKU_MAP.get(sku)
    if hyd_result:
        return hyd_result

    # --- PPL patterns ---

    # Pattern 1: 108731-pplegging-{style}-{color}-newlogo-{size}
    m = re.match(r"108731-pplegging-([\w/]+)-(\w+)-newlogo-(\w+)", sku, re.IGNORECASE)
    if m:
        style_code = m.group(1).lower()
        color_code = m.group(2).lower()
        size_code = m.group(3).upper()
        style = SHOPIFY_STYLE_MAP.get(style_code)
        color = SHOPIFY_COLOR_MAP.get(color_code)
        if style and color and size_code in SIZES:
            return (style, color, size_code)

    # Pattern 2 & 3 REMOVED — 108731-legging-* SKUs are maternity leggings, not PPL

    # Pattern 4: 108731-Newblack-highshort-{size}  (Short Black, alternate format)
    m = re.match(r"108731-Newblack-highshort-(\w+)$", sku, re.IGNORECASE)
    if m:
        size_code = m.group(1).upper()
        if size_code in SIZES:
            return ("Short", "Black", size_code)

    # Pattern 5: 108731-Newblack-high7-{size}  (7/8 Black, alternate format)
    m = re.match(r"108731-Newblack-high7-(\w+)$", sku, re.IGNORECASE)
    if m:
        size_code = m.group(1).upper()
        if size_code in SIZES:
            return ("7/8", "Black", size_code)

    # Pattern 6: 108731-Newblack-high-{size}  (Long Black, alternate format)
    m = re.match(r"108731-Newblack-high-(\w+)$", sku, re.IGNORECASE)
    if m:
        size_code = m.group(1).upper()
        if size_code in SIZES:
            return ("Long", "Black", size_code)

    return None


def parse_erp_sku(sku):
    """
    Parse an ERP SKU string into (style, color, size).

    Returns (style, color, size) or None if can't parse.
    """
    if not sku:
        return None

    # --- Nursing Pillow (exact match) ---
    np_result = NP_SKU_MAP.get(sku)
    if np_result:
        return np_result

    # --- Hydration (exact match) ---
    hyd_result = HYDRATION_SKU_MAP.get(sku)
    if hyd_result:
        return hyd_result

    # --- PPL patterns ---
    # Try each known prefix pattern (longest match first to avoid partial matches)
    for prefix, (color, style) in sorted(ERP_SKU_REVERSE.items(), key=lambda x: -len(x[0])):
        if sku.startswith(prefix + "-"):
            size_part = sku[len(prefix) + 1:]
            if size_part.upper() in SIZES:
                return (style, color, size_part.upper())

    return None


def is_tracked_sku(sku):
    """Check if a SKU belongs to any tracked product."""
    return parse_erp_sku(sku) is not None


# Backward compat alias
is_ppl_erp_sku = is_tracked_sku
