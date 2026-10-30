"""Creates database.db with sample data (safe to run many times).
    python init_db.py            create/refresh the database
    python init_db.py --reset    delete and rebuild from scratch
    python init_db.py --show     print everything in the database
"""
import sqlite3
import sys

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS Categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT, category_name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS Products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL,
    product_name TEXT NOT NULL, description TEXT,
    FOREIGN KEY (category_id) REFERENCES Categories(category_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS ProductVariants (
    variant_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
    sku TEXT NOT NULL UNIQUE, variant_name TEXT NOT NULL, price REAL NOT NULL CHECK (price >= 0),
    FOREIGN KEY (product_id) REFERENCES Products(product_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS Inventory (
    inventory_id INTEGER PRIMARY KEY AUTOINCREMENT, variant_id INTEGER NOT NULL UNIQUE,
    quantity INTEGER NOT NULL CHECK (quantity >= 0), reorder_level INTEGER NOT NULL CHECK (reorder_level >= 0),
    FOREIGN KEY (variant_id) REFERENCES ProductVariants(variant_id) ON DELETE CASCADE);
"""

# category, product, description, sku, variant, price, quantity, reorder_level
SEED = [
    ("Electronics", "Pro Laptop 15", "15-inch performance laptop", "LAP-001", "16GB / 512GB", 64999, 4, 5),   # low
    ("Electronics", "Pro Laptop 15", "15-inch performance laptop", "LAP-002", "32GB / 1TB", 89999, 12, 5),    # ok
    ("Electronics", "Wireless Ergonomic Mouse", "Ergonomic wireless mouse", "EMOUSE-BLK", "Black", 1299, 15, 5),
    ("Electronics", "Mechanical Keyboard", "Blue-switch keyboard", "MKEY-BLUE", "Blue Switch", 3499, 3, 5),   # low
    ("Electronics", "Noise-Cancelling Headphones", "Over-ear Bluetooth", "HDP-001", "Black", 8999, 0, 10),    # out
    ("Office Supplies", "Executive Notebook", "A5 notebook", "NOTE-A5-BRN", "Brown", 349, 0, 10),            # out
    ("Office Supplies", "Gel Pen Pack", "Pack of 10 gel pens", "PEN-001", "Black", 199, 120, 30),
    ("Furniture", "Ergonomic Mesh Chair", "Mesh office chair", "CHAIR-MSH-BLK", "Black", 12999, 8, 2),
    ("Furniture", "Standing Desk", "Electric height-adjustable desk", "DSK-001", "120cm", 24999, 3, 4),      # low
]


def add(conn, cat, prod, desc, sku, variant, price, qty, reorder):
    if conn.execute("SELECT 1 FROM ProductVariants WHERE sku = ?", (sku,)).fetchone():
        return
    conn.execute("INSERT OR IGNORE INTO Categories(category_name) VALUES (?)", (cat,))
    cid = conn.execute("SELECT category_id FROM Categories WHERE category_name = ?", (cat,)).fetchone()[0]
    row = conn.execute("SELECT product_id FROM Products WHERE category_id = ? AND product_name = ?", (cid, prod)).fetchone()
    pid = row[0] if row else conn.execute(
        "INSERT INTO Products(category_id, product_name, description) VALUES (?,?,?)", (cid, prod, desc)).lastrowid
    vid = conn.execute("INSERT INTO ProductVariants(product_id, sku, variant_name, price) VALUES (?,?,?,?)",
                       (pid, sku, variant, price)).lastrowid
    conn.execute("INSERT INTO Inventory(variant_id, quantity, reorder_level) VALUES (?,?,?)", (vid, qty, reorder))


def init() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for row in SEED:
        add(conn, *row)
    conn.commit()
    conn.close()


def show() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT c.category_name AS category, p.product_name, pv.sku, pv.price, i.quantity, i.reorder_level,
               CASE WHEN i.quantity = 0 THEN 'OUT_OF_STOCK'
                    WHEN i.quantity <= i.reorder_level THEN 'LOW_STOCK' ELSE 'IN_STOCK' END AS status
        FROM Products p JOIN Categories c ON c.category_id = p.category_id
        JOIN ProductVariants pv ON pv.product_id = p.product_id
        JOIN Inventory i ON i.variant_id = pv.variant_id ORDER BY c.category_name, pv.sku""").fetchall()
    print(f"Database: {DB_PATH}  ({len(rows)} variants)")
    for r in rows:
        print(f"  {r['sku']:<14} {r['product_name']:<28} {r['category']:<16} Rs{r['price']:<8} qty={r['quantity']:<4} {r['status']}")
    conn.close()


if __name__ == "__main__":
    if "--reset" in sys.argv and DB_PATH.exists():
        DB_PATH.unlink()
    init()
    print(f"Database ready: {DB_PATH}")
    if "--show" in sys.argv:
        show()
