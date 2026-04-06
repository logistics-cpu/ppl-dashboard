"""Shopify GraphQL Admin API client with rate limiting."""

import time
import requests
from core.config import SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN, SHOPIFY_API_VERSION


class ShopifyClient:
    def __init__(self):
        if not SHOPIFY_STORE_URL or not SHOPIFY_ACCESS_TOKEN:
            raise ValueError(
                "Shopify credentials not configured. "
                "Set SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN in .env file."
            )

        store = SHOPIFY_STORE_URL.replace("https://", "").replace("http://", "").rstrip("/")
        self.url = f"https://{store}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
        self.headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json",
        }
        self._cost_available = 1000

    def execute(self, query, variables=None, max_retries=3):
        """Execute a GraphQL query with automatic rate limiting and retry."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        # Simple rate limiting: wait if we're low on points
        if self._cost_available < 100:
            time.sleep(2)

        for attempt in range(max_retries):
            try:
                response = requests.post(self.url, json=payload, headers=self.headers, timeout=60)
                response.raise_for_status()
                data = response.json()

                if "errors" in data:
                    raise Exception(f"GraphQL errors: {data['errors']}")

                # Track rate limit from extensions
                extensions = data.get("extensions", {})
                cost = extensions.get("cost", {})
                throttle = cost.get("throttleStatus", {})
                self._cost_available = throttle.get("currentlyAvailable", self._cost_available)

                return data.get("data", {})

            except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 30  # 30s, 60s, 90s
                    time.sleep(wait)
                else:
                    raise

    def paginate(self, query, variables=None, path_to_edges=None, path_to_page_info=None):
        """
        Auto-paginate a GraphQL query using cursor-based pagination.

        path_to_edges: dot-separated path to edges array, e.g. "products.edges"
        path_to_page_info: dot-separated path to pageInfo, e.g. "products.pageInfo"
        """
        if variables is None:
            variables = {}

        all_edges = []
        has_next = True
        cursor = None

        while has_next:
            if cursor:
                variables["cursor"] = cursor

            data = self.execute(query, variables)

            edges = _get_nested(data, path_to_edges) or []
            all_edges.extend(edges)

            page_info = _get_nested(data, path_to_page_info) or {}
            has_next = page_info.get("hasNextPage", False)
            if edges:
                cursor = edges[-1].get("cursor")
            else:
                break

        return all_edges


def _get_nested(data, path):
    """Get a nested value from a dict using dot-separated path."""
    if not path:
        return data
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current
