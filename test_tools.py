"""Tests every tool on a temporary database (your real database.db is never touched).
Run:  python test_tools.py"""
import os
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")  # must be set BEFORE importing project files

import init_db  # noqa: E402

init_db.init()

from mcp_server import (  # noqa: E402
    add_product, get_available_products, get_low_stock_products,
    get_out_of_stock_products, get_stock_by_sku, search_products, update_stock,
)

checks = [
    ("Search 'Mouse'", lambda: search_products("Mouse"), lambda r: r["count"] >= 1),
    ("Low stock", get_low_stock_products, lambda r: r["count"] > 0 and all(0 < x["quantity"] <= x["reorder_level"] for x in r["results"])),
    ("Out of stock", get_out_of_stock_products, lambda r: r["count"] > 0 and all(x["quantity"] == 0 for x in r["results"])),
    ("Available in Electronics", lambda: get_available_products("Electronics"), lambda r: r["count"] > 0 and all(x["category"] == "Electronics" and x["quantity"] > 0 for x in r["results"])),
    ("Price/stock of LAP-001", lambda: get_stock_by_sku("LAP-001"), lambda r: r["found"] and r["item"]["quantity"] == 4),
    ("SKU lookup is case-insensitive", lambda: get_stock_by_sku("lap-001"), lambda r: r["found"]),
    ("Update LAP-001 to 25", lambda: update_stock("LAP-001", 25), lambda r: r["success"] and r["item"]["quantity"] == 25 and r["item"]["status"] == "IN_STOCK"),
    ("Add Wireless Mouse MOU-101", lambda: add_product("Wireless Mouse", "Electronics", "MOU-101", 799, 50), lambda r: r["success"] and r["created"]["quantity"] == 50),
    ("Duplicate SKU rejected", lambda: add_product("X", "Electronics", "MOU-101", 1, 1), lambda r: r["success"] is False),
    ("Unknown SKU handled", lambda: update_stock("NOPE", 1), lambda r: r["success"] is False),
    ("Negative quantity rejected", lambda: update_stock("LAP-001", -5), lambda r: r["success"] is False),
]

passed = 0
for name, run, ok in checks:
    try:
        good = ok(run())
    except Exception as e:
        good = False
        print(f"   error: {type(e).__name__}: {e}")
    passed += good
    print(("PASS" if good else "FAIL") + f": {name}")
print(f"\n{passed}/{len(checks)} passed")
raise SystemExit(0 if passed == len(checks) else 1)
