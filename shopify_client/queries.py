"""GraphQL query strings for Shopify Admin API."""

# Fetch all products with variants (for SKU mapping)
PRODUCTS_QUERY = """
query Products($cursor: String) {
    products(first: 50, after: $cursor) {
        edges {
            cursor
            node {
                id
                title
                variants(first: 100) {
                    edges {
                        node {
                            id
                            sku
                            title
                            selectedOptions {
                                name
                                value
                            }
                            inventoryQuantity
                        }
                    }
                }
            }
        }
        pageInfo {
            hasNextPage
        }
    }
}
"""

# Fetch orders within a date range (for sales data)
ORDERS_QUERY = """
query Orders($query: String!, $cursor: String) {
    orders(first: 250, after: $cursor, query: $query) {
        edges {
            cursor
            node {
                id
                createdAt
                processedAt
                lineItems(first: 100) {
                    edges {
                        node {
                            sku
                            quantity
                            variant {
                                id
                            }
                        }
                    }
                }
                refunds {
                    createdAt
                    refundLineItems(first: 100) {
                        edges {
                            node {
                                quantity
                                lineItem {
                                    sku
                                }
                            }
                        }
                    }
                }
            }
        }
        pageInfo {
            hasNextPage
        }
    }
}
"""
