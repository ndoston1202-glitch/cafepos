"""CafePOS - kafe uchun yengil POS tizimi.

Faqat Python standart kutubxonasi ishlatiladi (pip install shart emas).
Ishga tushirish:  python server.py   ->  http://localhost:8000
"""

import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import printing
from printing import PrintError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.environ.get("CAFEPOS_UPLOADS", os.path.join(BASE_DIR, "uploads"))
MAX_BODY = 8 * 1024 * 1024
MAX_IMAGE = 3 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DB_PATH = os.environ.get("CAFEPOS_DB", os.path.join(BASE_DIR, "cafepos.db"))
PORT = int(os.environ.get("CAFEPOS_PORT", "8000"))
SESSION_DAYS = 7

ROLES = ("admin", "cashier", "waiter", "cook")
PAYMENT_METHODS = ("cash", "card", "payme", "click")
PRINTER_KINDS = ("system", "network", "windows")  # windows = eski versiyadagi ulashilgan printer

db_lock = threading.Lock()


# ---------------------------------------------------------------- baza

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES categories(id),
    name TEXT NOT NULL,
    price INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS halls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    seats INTEGER NOT NULL DEFAULT 4,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER REFERENCES tables(id),
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    waiter_id INTEGER REFERENCES users(id),
    cashier_id INTEGER REFERENCES users(id),
    discount INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    payment_method TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER REFERENCES products(id),
    name TEXT NOT NULL,
    price INTEGER NOT NULL,
    qty INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 9100,
    width INTEGER NOT NULL DEFAULT 80,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS kitchen_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    printer_id INTEGER REFERENCES printers(id),
    lines TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    ready_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON kitchen_tickets(status);
"""

# Eski bazalarga yangi ustunlar qo'shiladi (ma'lumot o'chmaydi)
MIGRATIONS = [
    ("tables", "hall_id", "INTEGER REFERENCES halls(id)"),
    ("products", "printer_id", "INTEGER REFERENCES printers(id)"),
    ("products", "cost", "INTEGER NOT NULL DEFAULT 0"),  # tannarx
    ("products", "image", "TEXT"),
    ("order_items", "cost", "INTEGER NOT NULL DEFAULT 0"),  # sotilgan paytdagi tannarx
    # Oshxona printeriga / ekraniga allaqachon yuborilgan miqdor
    ("order_items", "printed_qty", "INTEGER NOT NULL DEFAULT 0"),
    ("order_items", "kds_qty", "INTEGER NOT NULL DEFAULT 0"),
]


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return digest.hex(), salt


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(conn):
    conn.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        pw, salt = hash_password("admin123")
        conn.execute(
            "INSERT INTO users (username, full_name, role, password_hash, salt) VALUES (?,?,?,?,?)",
            ("admin", "Administrator", "admin", pw, salt),
        )
        # Namuna ma'lumotlar - admin keyin o'zgartiradi
        demo = {
            "Issiq ichimliklar": [("Choy", 5000), ("Kofe Amerikano", 15000), ("Kapuchino", 20000)],
            "Sovuq ichimliklar": [("Limonad", 18000), ("Coca-Cola 0.5", 10000)],
            "Taomlar": [("Osh", 35000), ("Lag'mon", 30000), ("Shashlik", 25000)],
            "Shirinliklar": [("Chizkeyk", 25000), ("Medovik", 22000)],
        }
        for sort, (cat, items) in enumerate(demo.items()):
            cur = conn.execute("INSERT INTO categories (name, sort) VALUES (?,?)", (cat, sort))
            for name, price in items:
                conn.execute(
                    "INSERT INTO products (category_id, name, price) VALUES (?,?,?)",
                    (cur.lastrowid, name, price),
                )
        for i in range(1, 9):
            conn.execute("INSERT INTO tables (name, seats) VALUES (?, ?)", (f"Stol {i}", 4))
    if conn.execute("SELECT COUNT(*) FROM halls").fetchone()[0] == 0:
        seed_halls(conn)
    conn.commit()


def seed_halls(conn):
    """Zallar yo'q bo'lsa: mavjud stollar "Asosiy zal"ga o'tadi, banket zali va kabinalar qo'shiladi."""
    main = conn.execute("INSERT INTO halls (name, sort) VALUES ('Asosiy zal', 0)").lastrowid
    conn.execute("UPDATE tables SET hall_id = ? WHERE hall_id IS NULL", (main,))
    banquet = conn.execute("INSERT INTO halls (name, sort) VALUES ('Banket zali', 1)").lastrowid
    conn.execute("INSERT INTO tables (name, seats, hall_id) VALUES ('Banket', 30, ?)", (banquet,))
    cabins = conn.execute("INSERT INTO halls (name, sort) VALUES ('Kabinalar', 2)").lastrowid
    for i in range(1, 5):
        conn.execute("INSERT INTO tables (name, seats, hall_id) VALUES (?, 6, ?)", (f"Kabina {i}", cabins))


# ---------------------------------------------------------------- yordamchilar


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Deferred:
    """Uzoq davom etadigan ish (tarmoqni qidirish) - baza qulfidan tashqarida bajariladi."""

    def __init__(self, fn):
        self.fn = fn


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def require(data, *fields):
    for f in fields:
        if data.get(f) in (None, ""):
            raise ApiError(400, f"'{f}' maydoni to'ldirilmagan")


def to_int(value, field, minimum=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{field}' butun son bo'lishi kerak")
    if minimum is not None and n < minimum:
        raise ApiError(400, f"'{field}' kamida {minimum} bo'lishi kerak")
    return n


def order_items_with_printer(conn, order_id):
    # printer_id taomning HOZIRGI printeridan olinadi (o'chirilgan printer hisobga olinmaydi)
    return rows(
        conn.execute(
            """SELECT i.*, pr.id AS printer_id FROM order_items i
               LEFT JOIN products p ON p.id = i.product_id
               LEFT JOIN printers pr ON pr.id = p.printer_id AND pr.active = 1
               WHERE i.order_id = ? ORDER BY i.id""",
            (order_id,),
        )
    )


def order_detail(conn, order_id):
    order = conn.execute(
        """SELECT o.*, t.name AS table_name, h.name AS hall_name, w.full_name AS waiter_name
           FROM orders o
           LEFT JOIN tables t ON t.id = o.table_id
           LEFT JOIN halls h ON h.id = t.hall_id
           LEFT JOIN users w ON w.id = o.waiter_id
           WHERE o.id = ?""",
        (order_id,),
    ).fetchone()
    if not order:
        raise ApiError(404, "Buyurtma topilmadi")
    order = dict(order)
    all_items = order_items_with_printer(conn, order_id)
    # qty = 0 bo'lgan qatorlar oshxonaga yuborilganidan keyin bekor qilingan taomlar
    order["items"] = [i for i in all_items if i["qty"] > 0]
    order["subtotal"] = sum(i["price"] * i["qty"] for i in order["items"])
    order["pending_print"] = sum(
        abs(i["qty"] - i["printed_qty"]) for i in all_items if i["printer_id"]
    )
    order["pending_kds"] = sum(abs(i["qty"] - i["kds_qty"]) for i in all_items)
    if order["status"] == "open":
        order["total"] = max(order["subtotal"] - order["discount"], 0)
    return order


def open_order(conn, order_id):
    order = order_detail(conn, order_id)
    if order["status"] != "open":
        raise ApiError(409, "Buyurtma allaqachon yopilgan")
    return order


# ---------------------------------------------------------------- API
# Har bir handler: (conn, user, params, data, query) -> javob
# ROUTES: (metod, regex, ruxsat etilgan rollar yoki None = hamma kirganlar)

ROUTES = []


def route(method, pattern, roles=None):
    def wrap(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), roles, fn))
        return fn

    return wrap


@route("GET", "/api/me")
def me(conn, user, params, data, query):
    return user


# --- kategoriyalar


@route("GET", "/api/categories")
def list_categories(conn, user, params, data, query):
    return rows(conn.execute("SELECT * FROM categories ORDER BY sort, name"))


@route("POST", "/api/categories", ("admin",))
def create_category(conn, user, params, data, query):
    require(data, "name")
    cur = conn.execute(
        "INSERT INTO categories (name, sort) VALUES (?, ?)",
        (data["name"].strip(), to_int(data.get("sort", 0), "sort")),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/categories/(\d+)", ("admin",))
def update_category(conn, user, params, data, query):
    require(data, "name")
    conn.execute(
        "UPDATE categories SET name = ?, sort = ? WHERE id = ?",
        (data["name"].strip(), to_int(data.get("sort", 0), "sort"), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/categories/(\d+)", ("admin",))
def delete_category(conn, user, params, data, query):
    used = conn.execute(
        "SELECT COUNT(*) FROM products WHERE category_id = ? AND active = 1", (params[0],)
    ).fetchone()[0]
    if used:
        raise ApiError(409, "Kategoriyada taomlar bor. Avval ularni o'chiring yoki ko'chiring")
    conn.execute("UPDATE products SET category_id = NULL WHERE category_id = ?", (params[0],))
    conn.execute("DELETE FROM categories WHERE id = ?", (params[0],))
    return {"ok": True}


# --- taomlar


@route("GET", "/api/products")
def list_products(conn, user, params, data, query):
    return rows(
        conn.execute(
            """SELECT p.*, c.name AS category_name, pr.name AS printer_name FROM products p
               LEFT JOIN categories c ON c.id = p.category_id
               LEFT JOIN printers pr ON pr.id = p.printer_id AND pr.active = 1
               WHERE p.active = 1 ORDER BY c.sort, c.name, p.name"""
        )
    )


def product_values(data):
    require(data, "name", "price")
    return (
        data.get("category_id") or None,
        data["name"].strip(),
        to_int(data["price"], "price", 0),
        to_int(data.get("cost") or 0, "cost", 0),
        data.get("printer_id") or None,
    )


def remove_image_file(name):
    if name:
        path = os.path.join(UPLOAD_DIR, os.path.basename(name))
        if os.path.isfile(path):
            os.remove(path)


def save_product_image(conn, product_id, data):
    """data["image"] = "data:image/jpeg;base64,..." (yangi rasm) yoki data["remove_image"] = true"""
    old = conn.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
    old = old["image"] if old else None
    image = data.get("image")
    if image:
        m = re.fullmatch(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=\s]+)", image)
        if not m or m.group(1) not in IMAGE_TYPES:
            raise ApiError(400, "Rasm JPG, PNG yoki WEBP bo'lishi kerak")
        try:
            content = base64.b64decode(m.group(2), validate=False)
        except ValueError:
            raise ApiError(400, "Rasm buzilgan")
        if len(content) > MAX_IMAGE:
            raise ApiError(400, "Rasm hajmi 3 MB dan oshmasin")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        name = f"p{product_id}_{secrets.token_hex(4)}.{IMAGE_TYPES[m.group(1)]}"
        with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
            f.write(content)
        conn.execute("UPDATE products SET image = ? WHERE id = ?", (name, product_id))
        remove_image_file(old)
    elif data.get("remove_image"):
        conn.execute("UPDATE products SET image = NULL WHERE id = ?", (product_id,))
        remove_image_file(old)


@route("POST", "/api/products", ("admin",))
def create_product(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO products (category_id, name, price, cost, printer_id) VALUES (?,?,?,?,?)",
        product_values(data),
    )
    save_product_image(conn, cur.lastrowid, data)
    return {"id": cur.lastrowid}


@route("PUT", r"/api/products/(\d+)", ("admin",))
def update_product(conn, user, params, data, query):
    conn.execute(
        "UPDATE products SET category_id = ?, name = ?, price = ?, cost = ?, printer_id = ? WHERE id = ?",
        (*product_values(data), params[0]),
    )
    save_product_image(conn, params[0], data)
    return {"ok": True}


@route("DELETE", r"/api/products/(\d+)", ("admin",))
def delete_product(conn, user, params, data, query):
    # Eski buyurtmalar tarixi saqlanishi uchun o'chirmaymiz, faqat yashiramiz
    conn.execute("UPDATE products SET active = 0 WHERE id = ?", (params[0],))
    return {"ok": True}


# --- zallar


@route("GET", "/api/halls")
def list_halls(conn, user, params, data, query):
    return rows(
        conn.execute(
            """SELECT h.*, (SELECT COUNT(*) FROM tables t WHERE t.hall_id = h.id AND t.active = 1) AS tables
               FROM halls h ORDER BY h.sort, h.id"""
        )
    )


@route("POST", "/api/halls", ("admin",))
def create_hall(conn, user, params, data, query):
    require(data, "name")
    cur = conn.execute(
        "INSERT INTO halls (name, sort) VALUES (?, ?)",
        (data["name"].strip(), to_int(data.get("sort") or 0, "sort")),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/halls/(\d+)", ("admin",))
def update_hall(conn, user, params, data, query):
    require(data, "name")
    conn.execute(
        "UPDATE halls SET name = ?, sort = ? WHERE id = ?",
        (data["name"].strip(), to_int(data.get("sort") or 0, "sort"), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/halls/(\d+)", ("admin",))
def delete_hall(conn, user, params, data, query):
    used = conn.execute(
        "SELECT COUNT(*) FROM tables WHERE hall_id = ? AND active = 1", (params[0],)
    ).fetchone()[0]
    if used:
        raise ApiError(409, "Zalda stollar bor. Avval ularni o'chiring yoki boshqa zalga o'tkazing")
    conn.execute("UPDATE tables SET hall_id = NULL WHERE hall_id = ?", (params[0],))
    conn.execute("DELETE FROM halls WHERE id = ?", (params[0],))
    return {"ok": True}


# --- stollar


def table_values(conn, data):
    require(data, "name")
    hall_id = data.get("hall_id") or None
    if hall_id and not conn.execute("SELECT 1 FROM halls WHERE id = ?", (hall_id,)).fetchone():
        raise ApiError(404, "Zal topilmadi")
    return data["name"].strip(), to_int(data.get("seats") or 4, "seats", 1), hall_id


@route("GET", "/api/tables")
def list_tables(conn, user, params, data, query):
    tables = rows(
        conn.execute(
            """SELECT t.*, h.name AS hall_name FROM tables t
               LEFT JOIN halls h ON h.id = t.hall_id
               WHERE t.active = 1 ORDER BY COALESCE(h.sort, 999), h.id, t.id"""
        )
    )
    open_orders = {
        r["table_id"]: dict(r)
        for r in conn.execute(
            """SELECT o.id, o.table_id, o.created_at, o.discount, u.full_name AS waiter_name,
                      COALESCE(SUM(i.price * i.qty), 0) AS subtotal
               FROM orders o
               LEFT JOIN order_items i ON i.order_id = o.id
               LEFT JOIN users u ON u.id = o.waiter_id
               WHERE o.status = 'open' AND o.table_id IS NOT NULL
               GROUP BY o.id"""
        )
    }
    for t in tables:
        t["order"] = open_orders.get(t["id"])
    return tables


@route("POST", "/api/tables", ("admin",))
def create_table(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO tables (name, seats, hall_id) VALUES (?, ?, ?)",
        table_values(conn, data),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/tables/(\d+)", ("admin",))
def update_table(conn, user, params, data, query):
    conn.execute(
        "UPDATE tables SET name = ?, seats = ?, hall_id = ? WHERE id = ?",
        (*table_values(conn, data), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/tables/(\d+)", ("admin",))
def delete_table(conn, user, params, data, query):
    busy = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE table_id = ? AND status = 'open'", (params[0],)
    ).fetchone()[0]
    if busy:
        raise ApiError(409, "Stolda ochiq buyurtma bor")
    conn.execute("UPDATE tables SET active = 0 WHERE id = ?", (params[0],))
    return {"ok": True}


# --- buyurtmalar


@route("GET", "/api/orders")
def list_orders(conn, user, params, data, query):
    status = query.get("status", ["open"])[0]
    return rows(
        conn.execute(
            """SELECT o.*, t.name AS table_name, h.name AS hall_name, u.full_name AS waiter_name,
                      COALESCE((SELECT SUM(price * qty) FROM order_items WHERE order_id = o.id), 0) AS subtotal
               FROM orders o
               LEFT JOIN tables t ON t.id = o.table_id
               LEFT JOIN halls h ON h.id = t.hall_id
               LEFT JOIN users u ON u.id = o.waiter_id
               WHERE o.status = ? ORDER BY o.id DESC LIMIT 200""",
            (status,),
        )
    )


@route("POST", "/api/orders")
def create_order(conn, user, params, data, query):
    order_type = data.get("type", "dine_in")
    if order_type not in ("dine_in", "takeaway"):
        raise ApiError(400, "Noto'g'ri buyurtma turi")
    table_id = None
    if order_type == "dine_in":
        require(data, "table_id")
        table_id = to_int(data["table_id"], "table_id")
        if not conn.execute(
            "SELECT 1 FROM tables WHERE id = ? AND active = 1", (table_id,)
        ).fetchone():
            raise ApiError(404, "Stol topilmadi")
        existing = conn.execute(
            "SELECT id FROM orders WHERE table_id = ? AND status = 'open'", (table_id,)
        ).fetchone()
        if existing:
            return order_detail(conn, existing["id"])
    cur = conn.execute(
        "INSERT INTO orders (table_id, type, waiter_id, created_at) VALUES (?,?,?,?)",
        (table_id, order_type, user["id"], now()),
    )
    return order_detail(conn, cur.lastrowid)


@route("GET", r"/api/orders/(\d+)")
def get_order(conn, user, params, data, query):
    return order_detail(conn, params[0])


@route("POST", r"/api/orders/(\d+)/items")
def add_item(conn, user, params, data, query):
    open_order(conn, params[0])
    require(data, "product_id")
    qty = to_int(data.get("qty", 1), "qty", 1)
    product = conn.execute(
        "SELECT * FROM products WHERE id = ? AND active = 1", (data["product_id"],)
    ).fetchone()
    if not product:
        raise ApiError(404, "Taom topilmadi")
    existing = conn.execute(
        "SELECT id FROM order_items WHERE order_id = ? AND product_id = ? AND price = ?",
        (params[0], product["id"], product["price"]),
    ).fetchone()
    if existing:
        conn.execute("UPDATE order_items SET qty = qty + ? WHERE id = ?", (qty, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, name, price, cost, qty) VALUES (?,?,?,?,?,?)",
            (params[0], product["id"], product["name"], product["price"], product["cost"], qty),
        )
    return order_detail(conn, params[0])


@route("PUT", r"/api/orders/(\d+)/items/(\d+)")
def update_item(conn, user, params, data, query):
    open_order(conn, params[0])
    qty = to_int(data.get("qty"), "qty", 0)
    if qty == 0:
        # Oshxonaga hali yuborilmagan bo'lsa o'chiramiz, aks holda "bekor" bo'lib yuborilishi uchun qoldiramiz
        conn.execute(
            """DELETE FROM order_items WHERE id = ? AND order_id = ?
               AND printed_qty = 0 AND kds_qty = 0""",
            (params[1], params[0]),
        )
        conn.execute(
            "UPDATE order_items SET qty = 0 WHERE id = ? AND order_id = ?", (params[1], params[0])
        )
    else:
        conn.execute(
            "UPDATE order_items SET qty = ? WHERE id = ? AND order_id = ?", (qty, params[1], params[0])
        )
    return order_detail(conn, params[0])


@route("POST", r"/api/orders/(\d+)/pay", ("admin", "cashier"))
def pay_order(conn, user, params, data, query):
    order = open_order(conn, params[0])
    if not order["items"]:
        raise ApiError(400, "Bo'sh buyurtmani yopib bo'lmaydi")
    method = data.get("method")
    if method not in PAYMENT_METHODS:
        raise ApiError(400, "To'lov turini tanlang")
    discount = to_int(data.get("discount", 0), "discount", 0)
    if discount > order["subtotal"]:
        raise ApiError(400, "Chegirma summadan katta bo'lishi mumkin emas")
    conn.execute(
        """UPDATE orders SET status = 'paid', discount = ?, total = ?, payment_method = ?,
                  cashier_id = ?, closed_at = ? WHERE id = ?""",
        (discount, order["subtotal"] - discount, method, user["id"], now(), params[0]),
    )
    return order_detail(conn, params[0])


@route("POST", r"/api/orders/(\d+)/cancel", ("admin", "cashier"))
def cancel_order(conn, user, params, data, query):
    open_order(conn, params[0])
    conn.execute(
        "UPDATE orders SET status = 'cancelled', cashier_id = ?, closed_at = ? WHERE id = ?",
        (user["id"], now(), params[0]),
    )
    # Bekor qilingan buyurtma oshxona ekranida qolmasin
    conn.execute(
        "UPDATE kitchen_tickets SET status = 'cancelled' WHERE order_id = ? AND status = 'new'",
        (params[0],),
    )
    return {"ok": True}


# --- printerlar (ESC/POS termoprinterlar, printing.py)


def send_to_printer(printer, payload):
    try:
        printing.send(printer, payload)
    except PrintError as e:
        raise ApiError(502, str(e))


kitchen_ticket_bytes = printing.kitchen_ticket


def printer_values(data):
    require(data, "name", "kind")
    if data["kind"] not in PRINTER_KINDS:
        raise ApiError(400, "Noto'g'ri printer turi")
    if not data.get("address"):
        raise ApiError(400, "Ro'yxatdan printerni tanlang" if data["kind"] == "system" else "Printer IP manzilini kiriting")
    width = to_int(data.get("width", 80), "width")
    return (
        data["name"].strip(),
        data["kind"],
        data["address"].strip(),
        to_int(data.get("port") or 9100, "port", 1),
        80 if width >= 80 else 58,
    )


@route("GET", "/api/printers")
def list_printers(conn, user, params, data, query):
    return rows(conn.execute("SELECT * FROM printers WHERE active = 1 ORDER BY id"))


@route("POST", "/api/printers", ("admin",))
def create_printer(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO printers (name, kind, address, port, width) VALUES (?,?,?,?,?)",
        printer_values(data),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/printers/(\d+)", ("admin",))
def update_printer(conn, user, params, data, query):
    conn.execute(
        "UPDATE printers SET name = ?, kind = ?, address = ?, port = ?, width = ? WHERE id = ?",
        (*printer_values(data), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/printers/(\d+)", ("admin",))
def delete_printer(conn, user, params, data, query):
    conn.execute("UPDATE printers SET active = 0 WHERE id = ?", (params[0],))
    conn.execute("UPDATE products SET printer_id = NULL WHERE printer_id = ?", (params[0],))
    return {"ok": True}


@route("GET", "/api/printers/system", ("admin",))
def system_printers(conn, user, params, data, query):
    # Printerlarni qidirish bazaga tegmaydi - qulfdan tashqarida bajariladi
    def run():
        try:
            return printing.list_system_printers()
        except (PrintError, OSError) as e:
            raise ApiError(500, f"Printerlar ro'yxatini olib bo'lmadi: {e}")

    return Deferred(run)


@route("GET", "/api/printers/scan", ("admin",))
def scan_printers(conn, user, params, data, query):
    return Deferred(printing.scan_network)


def get_printer(conn, printer_id):
    printer = conn.execute(
        "SELECT * FROM printers WHERE id = ? AND active = 1", (printer_id,)
    ).fetchone()
    if not printer:
        raise ApiError(404, "Printer topilmadi")
    return dict(printer)


@route("POST", r"/api/printers/(\d+)/test", ("admin",))
def test_printer(conn, user, params, data, query):
    printer = get_printer(conn, params[0])
    fake_order = {"id": 0, "type": "takeaway", "table_name": "", "waiter_name": user["full_name"]}
    send_to_printer(printer, kitchen_ticket_bytes(printer, fake_order, [{"name": "TEST", "qty": 1}]))
    return {"ok": True}


# --- oshxonaga yuborish


def pending_lines(items, sent_field, only_with_printer):
    """Oshxonaga hali yuborilmagan o'zgarishlar, printer bo'yicha guruhlangan."""
    groups = {}
    for i in items:
        delta = i["qty"] - i[sent_field]
        if delta == 0 or (only_with_printer and not i["printer_id"]):
            continue
        groups.setdefault(i["printer_id"], []).append(
            {"item_id": i["id"], "name": i["name"], "qty": delta, "sent": i["qty"]}
        )
    return groups


@route("POST", r"/api/orders/(\d+)/kitchen-print")
def kitchen_print(conn, user, params, data, query):
    order = open_order(conn, params[0])
    items = order_items_with_printer(conn, params[0])
    groups = pending_lines(items, "printed_qty", only_with_printer=True)
    if not groups:
        if any(i["qty"] != i["printed_qty"] for i in items):
            raise ApiError(400, "Bu taomlarga printer tanlanmagan. Menyu bo'limida printer biriktiring")
        raise ApiError(400, "Oshxonaga yuboriladigan yangi taom yo'q")
    printed, errors = [], []
    for printer_id, lines in groups.items():
        printer = get_printer(conn, printer_id)
        try:
            send_to_printer(printer, kitchen_ticket_bytes(printer, order, lines))
        except ApiError as e:
            errors.append(e.message)
            continue
        for line in lines:
            conn.execute(
                "UPDATE order_items SET printed_qty = ? WHERE id = ?", (line["sent"], line["item_id"])
            )
        printed.append(printer["name"])
    if not printed:
        raise ApiError(502, "; ".join(errors))
    result = order_detail(conn, params[0])
    result["printed"], result["errors"] = printed, errors
    return result


@route("POST", r"/api/orders/(\d+)/kitchen-send")
def kitchen_send(conn, user, params, data, query):
    open_order(conn, params[0])
    groups = pending_lines(order_items_with_printer(conn, params[0]), "kds_qty", only_with_printer=False)
    if not groups:
        raise ApiError(400, "Oshxonaga yuboriladigan yangi taom yo'q")
    for printer_id, lines in groups.items():
        conn.execute(
            "INSERT INTO kitchen_tickets (order_id, printer_id, lines, created_at) VALUES (?,?,?,?)",
            (params[0], printer_id,
             json.dumps([{"name": l["name"], "qty": l["qty"]} for l in lines], ensure_ascii=False), now()),
        )
        for line in lines:
            conn.execute("UPDATE order_items SET kds_qty = ? WHERE id = ?", (line["sent"], line["item_id"]))
    return order_detail(conn, params[0])


@route("GET", "/api/kitchen", ("admin", "cashier", "cook"))
def kitchen_tickets(conn, user, params, data, query):
    sql = """SELECT k.*, o.type, t.name AS table_name, h.name AS hall_name,
                    u.full_name AS waiter_name, pr.name AS printer_name
             FROM kitchen_tickets k
             JOIN orders o ON o.id = k.order_id
             LEFT JOIN tables t ON t.id = o.table_id
             LEFT JOIN halls h ON h.id = t.hall_id
             LEFT JOIN users u ON u.id = o.waiter_id
             LEFT JOIN printers pr ON pr.id = k.printer_id
             WHERE k.status = 'new'"""
    args = []
    station = query.get("printer_id", [""])[0]
    if station == "none":
        sql += " AND k.printer_id IS NULL"
    elif station:
        sql += " AND k.printer_id = ?"
        args.append(to_int(station, "printer_id"))
    tickets = rows(conn.execute(sql + " ORDER BY k.id", args))
    for t in tickets:
        t["lines"] = json.loads(t["lines"])
    return tickets


@route("POST", r"/api/kitchen/(\d+)/ready", ("admin", "cashier", "cook"))
def kitchen_ready(conn, user, params, data, query):
    conn.execute(
        "UPDATE kitchen_tickets SET status = 'ready', ready_at = ? WHERE id = ?", (now(), params[0])
    )
    return {"ok": True}


# --- hisobot


@route("GET", "/api/reports", ("admin", "cashier"))
def report(conn, user, params, data, query):
    today = datetime.now().strftime("%Y-%m-%d")
    date_from = query.get("from", [today])[0]
    date_to = query.get("to", [today])[0]
    for d in (date_from, date_to):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ApiError(400, "Sana formati: YYYY-MM-DD")
    rng = (date_from + " 00:00:00", date_to + " 23:59:59")
    where = "o.status = 'paid' AND o.closed_at BETWEEN ? AND ?"
    summary = dict(
        conn.execute(
            f"""SELECT COUNT(*) AS orders, COALESCE(SUM(total), 0) AS revenue,
                       COALESCE(SUM(discount), 0) AS discount
                FROM orders o WHERE {where}""",
            rng,
        ).fetchone()
    )
    summary["average"] = summary["revenue"] // summary["orders"] if summary["orders"] else 0
    summary["cost"] = conn.execute(
        f"""SELECT COALESCE(SUM(i.cost * i.qty), 0) FROM order_items i
            JOIN orders o ON o.id = i.order_id WHERE {where}""",
        rng,
    ).fetchone()[0]
    summary["profit"] = summary["revenue"] - summary["cost"]
    return {
        "from": date_from,
        "to": date_to,
        "summary": summary,
        "by_method": rows(
            conn.execute(
                f"""SELECT payment_method AS method, COUNT(*) AS orders, SUM(total) AS revenue
                    FROM orders o WHERE {where} GROUP BY payment_method ORDER BY revenue DESC""",
                rng,
            )
        ),
        "top_products": rows(
            conn.execute(
                f"""SELECT i.name, SUM(i.qty) AS qty, SUM(i.qty * i.price) AS revenue,
                           SUM(i.qty * (i.price - i.cost)) AS profit
                    FROM order_items i JOIN orders o ON o.id = i.order_id
                    WHERE {where} AND i.qty > 0 GROUP BY i.name ORDER BY qty DESC LIMIT 10""",
                rng,
            )
        ),
        "by_waiter": rows(
            conn.execute(
                f"""SELECT COALESCE(u.full_name, '-') AS name, COUNT(*) AS orders, SUM(o.total) AS revenue
                    FROM orders o LEFT JOIN users u ON u.id = o.waiter_id
                    WHERE {where} GROUP BY o.waiter_id ORDER BY revenue DESC""",
                rng,
            )
        ),
        "orders": rows(
            conn.execute(
                f"""SELECT o.id, o.type, o.total, o.discount, o.payment_method, o.closed_at,
                           t.name AS table_name, h.name AS hall_name, u.full_name AS waiter_name
                    FROM orders o
                    LEFT JOIN tables t ON t.id = o.table_id
                    LEFT JOIN halls h ON h.id = t.hall_id
                    LEFT JOIN users u ON u.id = o.waiter_id
                    WHERE {where} ORDER BY o.closed_at DESC""",
                rng,
            )
        ),
    }


# --- xodimlar


@route("GET", "/api/users", ("admin",))
def list_users(conn, user, params, data, query):
    return rows(
        conn.execute("SELECT id, username, full_name, role, active FROM users ORDER BY id")
    )


def check_role(role):
    if role not in ROLES:
        raise ApiError(400, "Noto'g'ri rol")


@route("POST", "/api/users", ("admin",))
def create_user(conn, user, params, data, query):
    require(data, "username", "full_name", "role", "password")
    check_role(data["role"])
    if len(data["password"]) < 4:
        raise ApiError(400, "Parol kamida 4 belgi bo'lishi kerak")
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (data["username"].strip(),)).fetchone():
        raise ApiError(409, "Bu login band")
    pw, salt = hash_password(data["password"])
    cur = conn.execute(
        "INSERT INTO users (username, full_name, role, password_hash, salt) VALUES (?,?,?,?,?)",
        (data["username"].strip(), data["full_name"].strip(), data["role"], pw, salt),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/users/(\d+)", ("admin",))
def update_user(conn, user, params, data, query):
    require(data, "full_name", "role")
    check_role(data["role"])
    target = int(params[0])
    active = 1 if data.get("active", True) else 0
    if target == user["id"] and (data["role"] != "admin" or not active):
        raise ApiError(400, "O'zingizning admin huquqingizni o'chira olmaysiz")
    conn.execute(
        "UPDATE users SET full_name = ?, role = ?, active = ? WHERE id = ?",
        (data["full_name"].strip(), data["role"], active, target),
    )
    if data.get("password"):
        if len(data["password"]) < 4:
            raise ApiError(400, "Parol kamida 4 belgi bo'lishi kerak")
        pw, salt = hash_password(data["password"])
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (pw, salt, target)
        )
    if not active or data.get("password"):
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target,))
    return {"ok": True}


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "CafePOS/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (now(), fmt % args))

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    # --- javob yuborish

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path):
        if path.startswith("/uploads/"):
            full = os.path.join(os.path.realpath(UPLOAD_DIR), os.path.basename(path))
            if not os.path.isfile(full):
                return self.send_json(404, {"error": "Topilmadi"})
        else:
            if path == "/":
                path = "/index.html"
            full = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip("/")))
            if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
                full = os.path.join(STATIC_DIR, "index.html")
        with open(full, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # --- sessiya

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["sid"].value if "sid" in cookie else None

    def current_user(self, conn):
        token = self.session_token()
        if not token:
            return None
        row = conn.execute(
            """SELECT u.id, u.username, u.full_name, u.role FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > ? AND u.active = 1""",
            (token, now()),
        ).fetchone()
        return dict(row) if row else None

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > MAX_BODY:
            raise ApiError(413, "So'rov juda katta")
        try:
            data = json.loads(self.rfile.read(length).decode())
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Noto'g'ri JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "Noto'g'ri JSON")
        return data

    # --- asosiy

    def dispatch(self, method):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if method == "GET":
                return self.send_static(url.path)
            return self.send_json(404, {"error": "Topilmadi"})
        try:
            data = self.read_json() if method in ("POST", "PUT") else {}
            with db_lock:
                conn = self.server.conn
                try:
                    result = self.handle_api(conn, method, url.path, data, parse_qs(url.query))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            status, payload, headers = result
            if isinstance(payload, Deferred):
                payload = payload.fn()
            self.send_json(status, payload, headers)
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # kutilmagan xato - serverni yiqitmaymiz
            self.log_message("XATO: %r", e)
            self.send_json(500, {"error": "Serverda xato: " + str(e)})

    def handle_api(self, conn, method, path, data, query):
        if method == "POST" and path == "/api/login":
            return self.login(conn, data)
        if method == "POST" and path == "/api/logout":
            token = self.session_token()
            if token:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return 200, {"ok": True}, {"Set-Cookie": "sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"}

        user = self.current_user(conn)
        if not user:
            raise ApiError(401, "Tizimga kiring")
        path_matched = False
        for r_method, pattern, roles, fn in ROUTES:
            m = pattern.match(path)
            if not m:
                continue
            path_matched = True
            if r_method != method:
                continue
            if roles and user["role"] not in roles:
                raise ApiError(403, "Bu amal uchun ruxsatingiz yo'q")
            return 200, fn(conn, user, m.groups(), data, query), None
        raise ApiError(405 if path_matched else 404, "Topilmadi")

    def login(self, conn, data):
        require(data, "username", "password")
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1", (data["username"].strip(),)
        ).fetchone()
        if not row or not secrets.compare_digest(
            hash_password(data["password"], row["salt"])[0], row["password_hash"]
        ):
            raise ApiError(401, "Login yoki parol noto'g'ri")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)", (token, row["id"], expires)
        )
        cookie = f"sid={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Strict"
        user = {k: row[k] for k in ("id", "username", "full_name", "role")}
        return 200, user, {"Set-Cookie": cookie}


def make_server(port=PORT, host="0.0.0.0"):
    conn = connect()
    init_db(conn)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.conn = conn
    return server


def main():
    try:
        server = make_server()
    except OSError:
        print(f"XATO: {PORT}-port band. CafePOS allaqachon ishlayotgan bo'lishi mumkin.")
        print(f"Brauzerda oching: http://localhost:{PORT}")
        sys.exit(1)
    url = f"http://localhost:{PORT}"
    print("=" * 50)
    print(f"  CafePOS ishga tushdi: {url}")
    print("  Login: admin   Parol: admin123")
    print("  To'xtatish uchun shu oynani yoping (yoki Ctrl+C)")
    print("=" * 50)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTo'xtatildi.")


if __name__ == "__main__":
    main()
