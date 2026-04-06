"""Map Shopify and ERP SKUs to unified (style, color, size) tuples."""

import re
from core.config import (
    SHOPIFY_STYLE_MAP, SHOPIFY_COLOR_MAP,
    ERP_SKU_REVERSE, SIZES,
)


def parse_shopify_sku(sku):
    """
    Parse a Shopify SKU string into (style, color, size).

    Supported formats:
      108731-pplegging-full-black-newlogo-M       → (Long, Black, M)
      108731-pplegging-7/8s-green-newlogo-L       → (7/8, Olive Green, L)
      108731-pplegging-short-red-newlogo-S         → (Short, Burgundy, S)
      108731-legging-black-M                       → (Long, Black, M)
      108731-legging-short-black-newlogo-XL        → (Short, Black, XL)
      108731-Newblack-highshort-XS                 → (Short, Black, XS)
      108731-Newblack-high-M                       → (Long, Black, M)
      108731-Newblack-high7-L                      → (7/8, Black, L)

    Returns (style, color, size) or None if can't parse.
    """
    if not sku:
        return None

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

    # Pattern 2: 108731-legging-short-{color}-newlogo-{size}  (Short style, no "pp")
    m = re.match(r"108731-legging-short-(\w+)-newlogo-(\w+)", sku, re.IGNORECASE)
    if m:
        color_code = m.group(1).lower()
        size_code = m.group(2).upper()
        color = SHOPIFY_COLOR_MAP.get(color_code)
        if color and size_code in SIZES:
            return ("Short", color, size_code)

    # Pattern 3: 108731-legging-{color}-{size}  (Long style, no "pp", no "newlogo")
    m = re.match(r"108731-legging-(\w+)-(\w+)$", sku, re.IGNORECASE)
    if m:
        color_code = m.group(1).lower()
        size_code = m.group(2).upper()
        color = SHOPIFY_COLOR_MAP.get(color_code)
        if color and size_code in SIZES:
            return ("Long", color, size_code)

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
    Formats:
      108731-blackBB-high-M       → (Long, Black, M)
      108731-blackBB-high7-XL     → (7/8, Black, XL)
      136181-armygreen-highshort-S → (Short, Olive Green, S)

    Returns (style, color, size) or None if can't parse.
    """
    if not sku:
        return None

    # Try each known prefix pattern (longest match first to avoid partial matches)
    for prefix, (color, style) in sorted(ERP_SKU_REVERSE.items(), key=lambda x: -len(x[0])):
        if sku.startswith(prefix + "-"):
            size_part = sku[len(prefix) + 1:]
            if size_part.upper() in SIZES:
                return (style, color, size_part.upper())

    return None


def is_ppl_erp_sku(sku):
    """Check if an ERP SKU belongs to a PPL product."""
    return parse_erp_sku(sku) is not None
