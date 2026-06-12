"""Seed production Turso with the Product Cost workbook.

Usage:
    python3 -u scripts/seed_costs.py "/path/to/📦 Product Cost 2026.xlsx"

Reads Turso credentials from .streamlit/secrets.toml. Prints progress
unbuffered so it can be watched with tail -f.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main():
    if len(sys.argv) < 2:
        print("usage: python3 -u scripts/seed_costs.py <workbook.xlsx>")
        sys.exit(1)
    wb_path = sys.argv[1]

    with open(os.path.join(os.path.dirname(__file__), "..", ".streamlit", "secrets.toml")) as f:
        txt = f.read()

    def g(k):
        m = re.search(rf'{k}\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else None

    os.environ["TURSO_DB_URL"] = g("TURSO_DB_URL")
    os.environ["TURSO_AUTH_TOKEN"] = g("TURSO_AUTH_TOKEN")

    import core.database as db
    assert db._use_turso, "Turso credentials not found"

    t0 = time.time()
    print(f"[{time.time()-t0:6.1f}s] init_db…", flush=True)
    db.init_db()

    from core.cost_import import seed_from_workbook
    with open(wb_path, "rb") as f:
        file_bytes = f.read()

    stats = seed_from_workbook(
        file_bytes,
        progress=lambda m: print(f"[{time.time()-t0:6.1f}s] {m}", flush=True),
    )
    print(f"\nDone in {time.time()-t0:.1f}s", flush=True)
    print({k: v for k, v in stats.items() if k != "warnings"}, flush=True)
    for w in stats["warnings"]:
        print(f"  warning: {w}", flush=True)


if __name__ == "__main__":
    main()
