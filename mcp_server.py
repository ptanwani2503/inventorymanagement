"""Inventory MCP server (stdio transport).

RULE: never print() to stdout here - stdout carries the MCP protocol. Use stderr for logs.
Run standalone:  python mcp_server.py   (it will wait silently for a client - that's normal)
"""
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path


from mcp.server.fastmcp import FastMCP

from config import DB_PATH

mcp = FastMCP("inventory")

STATUS_SQL = """CASE WHEN i.quantity = 0 THEN 'OUT_OF_STOCK'
                     WHEN i.quantity <= i.reorder_level THEN 'LOW_STOCK'
                     ELSE 'IN_STOCK' END"""

SELECT_SQL = f"""
SELECT c.category_name AS category, p.product_name, p.description,
       pv.sku, pv.variant_name, pv.price, i.quantity, i.reorder_level,
       {STATUS_SQL} AS status
FROM ProductVariants pv
JOIN Products p   ON p.product_id = pv.product_id
JOIN Categories c ON c.category_id = p.category_id
JOIN Inventory i  ON i.variant_id = pv.variant_id
"""


@contextmanager
def db():
    """One short-lived connection per call: FK enforcement on, commit/rollback, always closed."""
    if not Path(DB_PATH).exists():  # sqlite would silently create an empty DB otherwise
        raise FileNotFoundError(f"Database not found at {DB_PATH}. Run: python init_db.py")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetch(conn, where: str = "", params: tuple = (), limit: int = 50) -> list[dict]:
    sql = SELECT_SQL + (f" WHERE {where}" if where else "") + " ORDER BY c.category_name, p.product_name, pv.sku LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (*params, limit)).fetchall()]


def _list(where: str, params: tuple = (), category: str = "") -> dict:
    clauses, p = [where], list(params)
    if category:
        clauses.append("c.category_name LIKE ?")
        p.append(f"%{category.strip()}%")
    with db() as conn:
        rows = _fetch(conn, " AND ".join(clauses), tuple(p))
    return {"count": len(rows), "results": rows}


# ----------------------------- READ TOOLS -----------------------------------
@mcp.tool()
def search_products(query: str = "", category: str = "", sku: str = "") -> dict:
    """Search products by free text (matches product name, variant name, SKU or category),
    and/or by category name, and/or by exact SKU. Returns price, quantity and stock status."""
    clauses, p = [], []
    if query:
        clauses.append("(p.product_name LIKE ? OR pv.variant_name LIKE ? OR pv.sku LIKE ? OR c.category_name LIKE ?)")
        p += [f"%{query.strip()}%"] * 4
    if category:
        clauses.append("c.category_name LIKE ?")
        p.append(f"%{category.strip()}%")
    if sku:
        clauses.append("UPPER(pv.sku) = UPPER(?)")
        p.append(sku.strip())
    with db() as conn:
        rows = _fetch(conn, " AND ".join(clauses), tuple(p))
    return {"count": len(rows), "results": rows}


@mcp.tool()
def get_low_stock_products(category: str = "") -> dict:
    """Products with 0 < quantity <= reorder_level. Optionally filter by category."""
    return _list("i.quantity > 0 AND i.quantity <= i.reorder_level", category=category)


@mcp.tool()
def get_out_of_stock_products(category: str = "") -> dict:
    """Products with quantity = 0. Optionally filter by category."""
    return _list("i.quantity = 0", category=category)


@mcp.tool()
def get_available_products(category: str = "") -> dict:
    """Products that can be sold right now (quantity > 0, includes low-stock items; see 'status').
    Optionally filter by category, e.g. 'Electronics'."""
    return _list("i.quantity > 0", category=category)


@mcp.tool()
def get_stock_by_sku(sku: str) -> dict:
    """Price and stock quantity for one exact SKU."""
    with db() as conn:
        rows = _fetch(conn, "UPPER(pv.sku) = UPPER(?)", (sku.strip(),))
        if rows:
            return {"found": True, "item": rows[0]}
        similar = [r["sku"] for r in conn.execute(
            "SELECT sku FROM ProductVariants WHERE sku LIKE ? LIMIT 5", (f"%{sku.strip()[:3]}%",))]
    return {"found": False, "error": f"SKU '{sku}' not found", "similar_skus": similar}


# ----------------------------- WRITE TOOLS ----------------------------------
@mcp.tool()
def update_stock(sku: str, quantity: int) -> dict:
    """Set the stock quantity of a SKU to an absolute number (not a delta). quantity must be >= 0."""
    if quantity < 0:
        return {"success": False, "error": "quantity must be >= 0"}
    with db() as conn:
        row = conn.execute(
            "SELECT pv.variant_id, i.quantity FROM ProductVariants pv "
            "JOIN Inventory i ON i.variant_id = pv.variant_id WHERE UPPER(pv.sku) = UPPER(?)",
            (sku.strip(),),
        ).fetchone()
        if not row:
            return {"success": False, "error": f"SKU '{sku}' not found"}
        conn.execute("UPDATE Inventory SET quantity = ? WHERE variant_id = ?", (quantity, row["variant_id"]))
        item = _fetch(conn, "UPPER(pv.sku) = UPPER(?)", (sku.strip(),))[0]
    return {"success": True, "previous_quantity": row["quantity"], "item": item}


@mcp.tool()
def add_product(
    product_name: str,
    category_name: str,
    sku: str,
    price: float,
    quantity: int,
    description: str = "",
    variant_name: str = "Default",
    reorder_level: int = 10,
) -> dict:
    """Add a product variant with initial stock. Creates the category/product if they don't exist.
    To add ANOTHER variant to an existing product, call again with the same product_name and a new sku.
    price must be a plain number (no currency symbol)."""
    sku = sku.strip().upper()
    if not sku or not product_name.strip() or not category_name.strip():
        return {"success": False, "error": "product_name, category_name and sku are required"}
    if price < 0 or quantity < 0 or reorder_level < 0:
        return {"success": False, "error": "price, quantity and reorder_level must be >= 0"}
    with db() as conn:
        if conn.execute("SELECT 1 FROM ProductVariants WHERE UPPER(sku) = ?", (sku,)).fetchone():
            return {"success": False, "error": f"SKU '{sku}' already exists"}
        cat = conn.execute(
            "SELECT category_id FROM Categories WHERE LOWER(category_name) = LOWER(?)", (category_name.strip(),)
        ).fetchone()
        cat_id = cat["category_id"] if cat else conn.execute(
            "INSERT INTO Categories(category_name) VALUES (?)", (category_name.strip(),)).lastrowid
        prod = conn.execute(
            "SELECT product_id FROM Products WHERE category_id = ? AND LOWER(product_name) = LOWER(?)",
            (cat_id, product_name.strip()),
        ).fetchone()
        pid = prod["product_id"] if prod else conn.execute(
            "INSERT INTO Products(category_id, product_name, description) VALUES (?,?,?)",
            (cat_id, product_name.strip(), description)).lastrowid
        vid = conn.execute(
            "INSERT INTO ProductVariants(product_id, sku, variant_name, price) VALUES (?,?,?,?)",
            (pid, sku, variant_name, price)).lastrowid
        conn.execute(
            "INSERT INTO Inventory(variant_id, quantity, reorder_level) VALUES (?,?,?)",
            (vid, quantity, reorder_level))
        item = _fetch(conn, "pv.sku = ?", (sku,))[0]
    return {"success": True, "created": item}


if __name__ == "__main__":
    mcp.run()
