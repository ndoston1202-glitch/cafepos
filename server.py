"""CafePOS - kafe uchun yengil POS tizimi.

Faqat Python standart kutubxonasi ishlatiladi (pip install shart emas).
Ishga tushirish:  python server.py   ->  http://localhost:8000
"""

import atexit
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
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/javascript", ".js")  # Windows reestri boshqacha bo'lishi mumkin
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.environ.get("CAFEPOS_UPLOADS", os.path.join(BASE_DIR, "uploads"))
MAX_BODY = 8 * 1024 * 1024
MAX_IMAGE = 3 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DB_PATH = os.environ.get("CAFEPOS_DB", os.path.join(BASE_DIR, "cafepos.db"))
PORT = int(os.environ.get("CAFEPOS_PORT", "8000"))
SESSION_DAYS = 7

ROLES = ("admin", "cashier", "waiter", "cook")
# Bo'limlarga kirish ruxsatlari
PERMISSIONS = ("tables", "cashier", "kitchen", "reports", "menu", "crm", "finance", "halls", "printers", "users", "settings")
ROLE_DEFAULTS = {
    "admin": PERMISSIONS,
    "cashier": ("tables", "cashier", "kitchen", "reports", "crm"),
    "waiter": ("tables",),
    "cook": ("kitchen",),
}
# Buyurtma to'lov usullari ("debt" = qarzga - pul keyin CRM > Qarzlar orqali tushadi)
PAYMENT_METHODS = ("cash", "card", "payme", "click", "debt")
# Moliya hisoblari (kassa balansi)
FINANCE_ACCOUNTS = ("cash", "card", "payme", "click", "bank")
# Qarz to'lash usullari -> qaysi hisobga tushadi
DEBT_PAY_METHODS = {"cash": "cash", "click": "card", "terminal": "bank", "transfer": "bank"}
DUE_SOON_DAYS = 3
SYSTEM_FINANCE_TYPES = (("Mijoz balansini to'ldirish", "in"), ("Ta'minotchiga pul berish", "out"))
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
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS finance_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    direction TEXT NOT NULL,           -- 'in' = kirim, 'out' = chiqim
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS finance_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id INTEGER NOT NULL REFERENCES finance_types(id),
    direction TEXT NOT NULL,
    account TEXT NOT NULL,             -- cash / card / payme / click
    amount INTEGER NOT NULL,
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'done',   -- done / cancelled (o'chirilmaydi)
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    cancelled_at TEXT,
    cancelled_by INTEGER REFERENCES users(id),
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_finance_created ON finance_entries(created_at);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    gender TEXT NOT NULL,              -- 'm' erkak, 'f' ayol
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_id INTEGER REFERENCES orders(id),
    amount INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'open',   -- open / closed / cancelled
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS debt_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debt_id INTEGER NOT NULL REFERENCES debts(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount INTEGER NOT NULL,
    method TEXT NOT NULL,              -- cash / click / terminal / transfer
    account TEXT NOT NULL,             -- qaysi hisobga tushdi: cash / card / bank
    status TEXT NOT NULL DEFAULT 'done',
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    cancelled_at TEXT,
    cancelled_by INTEGER REFERENCES users(id),
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_debts_customer ON debts(customer_id);
"""

# Eski bazalarga yangi ustunlar qo'shiladi (ma'lumot o'chmaydi)
MIGRATIONS = [
    ("tables", "hall_id", "INTEGER REFERENCES halls(id)"),
    # Zal uchun alohida xizmat haqi foizi (NULL = umumiy sozlama)
    ("halls", "service_percent", "REAL"),
    # Yopilgan buyurtmaning xizmat haqi (to'lov paytida muzlatiladi)
    ("orders", "service_percent", "REAL NOT NULL DEFAULT 0"),
    ("orders", "service", "INTEGER NOT NULL DEFAULT 0"),
    # Savdo bekor qilinsa (pul qaytarildi): status = 'refunded'
    ("orders", "refunded_at", "TEXT"),
    ("orders", "refunded_by", "INTEGER REFERENCES users(id)"),
    ("orders", "refund_reason", "TEXT"),
    ("users", "first_name", "TEXT"),
    ("users", "last_name", "TEXT"),
    ("users", "phone", "TEXT"),
    # JSON ro'yxat; NULL = rol bo'yicha standart ruxsatlar
    ("users", "permissions", "TEXT"),
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
    # Bazaviy tranzaksiya turlari doim bo'ladi
    for name, direction in SYSTEM_FINANCE_TYPES:
        conn.execute(
            """INSERT OR IGNORE INTO finance_types (name, direction, is_system, created_at)
               VALUES (?, ?, 1, ?)""",
            (name, direction, now()),
        )
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


DEFAULT_SETTINGS = {"cafe_name": "CafePOS", "service_percent": "0"}


def get_settings(conn):
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")})
    settings["service_percent"] = float(settings["service_percent"] or 0)
    return settings


def to_percent(value, field):
    try:
        n = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ApiError(400, f"'{field}' son bo'lishi kerak")
    if not 0 <= n <= 100:
        raise ApiError(400, "Foiz 0 dan 100 gacha bo'lishi kerak")
    return round(n, 2)


def service_percent(conn, order_type, hall_id):
    """Stolda o'tirganlar uchun xizmat haqi foizi: zalniki, bo'lmasa umumiy sozlama."""
    if order_type != "dine_in":
        return 0
    if hall_id:
        row = conn.execute("SELECT service_percent FROM halls WHERE id = ?", (hall_id,)).fetchone()
        if row and row["service_percent"] is not None:
            return row["service_percent"]
    return get_settings(conn)["service_percent"]


def apply_totals(order, percent):
    """order["subtotal"] va order["discount"] asosida xizmat haqi va jami summani hisoblaydi."""
    order["service_percent"] = percent
    order["service"] = round(order["subtotal"] * percent / 100)
    order["total"] = max(order["subtotal"] + order["service"] - order["discount"], 0)
    return order


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
        """SELECT o.*, t.name AS table_name, t.hall_id, h.name AS hall_name, w.full_name AS waiter_name
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
        apply_totals(order, service_percent(conn, order["type"], order["hall_id"]))
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


def route(method, pattern, perms=None):
    """perms - shu ruxsatlardan birortasi bo'lsa kirish mumkin (None = tizimga kirgan hamma)."""
    def wrap(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), perms, fn))
        return fn

    return wrap


@route("GET", "/api/me")
def me(conn, user, params, data, query):
    return user


# --- kategoriyalar


@route("GET", "/api/categories")
def list_categories(conn, user, params, data, query):
    return rows(conn.execute("SELECT * FROM categories ORDER BY sort, name"))


@route("POST", "/api/categories", ("menu",))
def create_category(conn, user, params, data, query):
    require(data, "name")
    cur = conn.execute(
        "INSERT INTO categories (name, sort) VALUES (?, ?)",
        (data["name"].strip(), to_int(data.get("sort", 0), "sort")),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/categories/(\d+)", ("menu",))
def update_category(conn, user, params, data, query):
    require(data, "name")
    conn.execute(
        "UPDATE categories SET name = ?, sort = ? WHERE id = ?",
        (data["name"].strip(), to_int(data.get("sort", 0), "sort"), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/categories/(\d+)", ("menu",))
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


@route("POST", "/api/products", ("menu",))
def create_product(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO products (category_id, name, price, cost, printer_id) VALUES (?,?,?,?,?)",
        product_values(data),
    )
    save_product_image(conn, cur.lastrowid, data)
    return {"id": cur.lastrowid}


@route("PUT", r"/api/products/(\d+)", ("menu",))
def update_product(conn, user, params, data, query):
    conn.execute(
        "UPDATE products SET category_id = ?, name = ?, price = ?, cost = ?, printer_id = ? WHERE id = ?",
        (*product_values(data), params[0]),
    )
    save_product_image(conn, params[0], data)
    return {"ok": True}


@route("DELETE", r"/api/products/(\d+)", ("menu",))
def delete_product(conn, user, params, data, query):
    # Eski buyurtmalar tarixi saqlanishi uchun o'chirmaymiz, faqat yashiramiz
    conn.execute("UPDATE products SET active = 0 WHERE id = ?", (params[0],))
    return {"ok": True}


# --- sozlamalar


@route("GET", "/api/settings")
def read_settings(conn, user, params, data, query):
    return get_settings(conn)


@route("PUT", "/api/settings", ("settings",))
def save_settings(conn, user, params, data, query):
    values = {}
    if "cafe_name" in data:
        values["cafe_name"] = (data["cafe_name"] or "").strip() or DEFAULT_SETTINGS["cafe_name"]
    if "service_percent" in data:
        values["service_percent"] = str(to_percent(data["service_percent"] or 0, "service_percent"))
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    return get_settings(conn)


# --- zallar


@route("GET", "/api/halls")
def list_halls(conn, user, params, data, query):
    return rows(
        conn.execute(
            """SELECT h.*, (SELECT COUNT(*) FROM tables t WHERE t.hall_id = h.id AND t.active = 1) AS tables
               FROM halls h ORDER BY h.sort, h.id"""
        )
    )


def hall_values(data):
    require(data, "name")
    percent = data.get("service_percent")
    percent = None if percent in (None, "") else to_percent(percent, "service_percent")
    return data["name"].strip(), to_int(data.get("sort") or 0, "sort"), percent


@route("POST", "/api/halls", ("halls",))
def create_hall(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO halls (name, sort, service_percent) VALUES (?, ?, ?)", hall_values(data)
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/halls/(\d+)", ("halls",))
def update_hall(conn, user, params, data, query):
    conn.execute(
        "UPDATE halls SET name = ?, sort = ?, service_percent = ? WHERE id = ?",
        (*hall_values(data), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/halls/(\d+)", ("halls",))
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
            """SELECT o.id, o.type, o.table_id, o.created_at, o.discount, u.full_name AS waiter_name,
                      COALESCE(SUM(i.price * i.qty), 0) AS subtotal
               FROM orders o
               LEFT JOIN order_items i ON i.order_id = o.id
               LEFT JOIN users u ON u.id = o.waiter_id
               WHERE o.status = 'open' AND o.table_id IS NOT NULL
               GROUP BY o.id
               HAVING COALESCE(SUM(i.qty), 0) > 0"""  # taom qo'shilmagan stol band emas
        )
    }
    for t in tables:
        t["order"] = open_orders.get(t["id"])
        if t["order"]:
            apply_totals(t["order"], service_percent(conn, "dine_in", t["hall_id"]))
    return tables


@route("POST", "/api/tables", ("halls",))
def create_table(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO tables (name, seats, hall_id) VALUES (?, ?, ?)",
        table_values(conn, data),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/tables/(\d+)", ("halls",))
def update_table(conn, user, params, data, query):
    conn.execute(
        "UPDATE tables SET name = ?, seats = ?, hall_id = ? WHERE id = ?",
        (*table_values(conn, data), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/tables/(\d+)", ("halls",))
def delete_table(conn, user, params, data, query):
    busy = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE table_id = ? AND status = 'open'", (params[0],)
    ).fetchone()[0]
    if busy:
        raise ApiError(409, "Stolda ochiq buyurtma bor")
    conn.execute("UPDATE tables SET active = 0 WHERE id = ?", (params[0],))
    return {"ok": True}


# --- buyurtmalar


@route("GET", "/api/orders", ("tables", "cashier"))
def list_orders(conn, user, params, data, query):
    status = query.get("status", ["open"])[0]
    orders = rows(
        conn.execute(
            """SELECT o.*, t.name AS table_name, t.hall_id, h.name AS hall_name, u.full_name AS waiter_name,
                      COALESCE((SELECT SUM(price * qty) FROM order_items WHERE order_id = o.id), 0) AS subtotal
               FROM orders o
               LEFT JOIN tables t ON t.id = o.table_id
               LEFT JOIN halls h ON h.id = t.hall_id
               LEFT JOIN users u ON u.id = o.waiter_id
               WHERE o.status = ? ORDER BY o.id DESC LIMIT 200""",
            (status,),
        )
    )
    if status == "open":  # bo'sh (taom qo'shilmagan) buyurtmalar kassada ko'rinmaydi
        orders = [o for o in orders if conn.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = ?", (o["id"],)).fetchone()[0] > 0]
    for o in orders:
        if o["status"] == "open":
            apply_totals(o, service_percent(conn, o["type"], o["hall_id"]))
    return orders


@route("POST", "/api/orders", ("tables",))
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


@route("GET", r"/api/orders/(\d+)", ("tables", "cashier", "reports"))
def get_order(conn, user, params, data, query):
    return order_detail(conn, params[0])


@route("POST", r"/api/orders/(\d+)/items", ("tables",))
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


@route("PUT", r"/api/orders/(\d+)/items/(\d+)", ("tables",))
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


@route("POST", r"/api/orders/(\d+)/pay", ("cashier",))
def pay_order(conn, user, params, data, query):
    order = open_order(conn, params[0])
    if not order["items"]:
        raise ApiError(400, "Bo'sh buyurtmani yopib bo'lmaydi")
    method = data.get("method")
    if method not in PAYMENT_METHODS:
        raise ApiError(400, "To'lov turini tanlang")
    discount = to_int(data.get("discount", 0), "discount", 0)
    if discount > order["subtotal"] + order["service"]:
        raise ApiError(400, "Chegirma summadan katta bo'lishi mumkin emas")
    order["discount"] = discount
    apply_totals(order, order["service_percent"])
    if method == "debt":  # qarzga: mijoz va to'lov muddati shart
        customer = get_customer(conn, data.get("customer_id"))
        due = parse_due_date(data.get("due_date"))
        if order["total"] <= 0:
            raise ApiError(400, "Qarzga yoziladigan summa yo'q")
        conn.execute(
            """INSERT INTO debts (customer_id, order_id, amount, due_date, comment, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (customer["id"], order["id"], order["total"], due, f"Buyurtma #{order['id']}", now(), user["id"]),
        )
    conn.execute(
        """UPDATE orders SET status = 'paid', discount = ?, service_percent = ?, service = ?, total = ?,
                  payment_method = ?, cashier_id = ?, closed_at = ? WHERE id = ?""",
        (discount, order["service_percent"], order["service"], order["total"], method, user["id"], now(), params[0]),
    )
    return order_detail(conn, params[0])


@route("POST", r"/api/orders/(\d+)/cancel", ("cashier",))
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


@route("POST", r"/api/orders/(\d+)/discard", ("tables",))
def discard_order(conn, user, params, data, query):
    """Ofitsiant uchun: oshxonaga yuborilmagan buyurtmadan voz kechish.

    Taom qo'shilmagan bo'lsa - buyurtma butunlay o'chadi (stol bo'shaydi, tarixda iz qolmaydi).
    Taom bor, lekin oshxonaga yuborilmagan bo'lsa - "bekor qilingan" bo'ladi.
    Oshxonaga yuborilgan bo'lsa - faqat kassa ruxsati bilan (/cancel).
    """
    open_order(conn, params[0])
    items = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(printed_qty + kds_qty), 0) FROM order_items WHERE order_id = ?",
        (params[0],),
    ).fetchone()
    if items[0] == 0:
        conn.execute("DELETE FROM kitchen_tickets WHERE order_id = ?", (params[0],))
        conn.execute("DELETE FROM orders WHERE id = ?", (params[0],))
        return {"ok": True, "deleted": True}
    if items[1] > 0 and "cashier" not in user["permissions"]:
        raise ApiError(403, "Oshxonaga yuborilgan buyurtmani faqat kassir bekor qila oladi")
    return dict(cancel_order(conn, user, params, data, query), deleted=False)


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


@route("POST", "/api/printers", ("printers",))
def create_printer(conn, user, params, data, query):
    cur = conn.execute(
        "INSERT INTO printers (name, kind, address, port, width) VALUES (?,?,?,?,?)",
        printer_values(data),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/printers/(\d+)", ("printers",))
def update_printer(conn, user, params, data, query):
    conn.execute(
        "UPDATE printers SET name = ?, kind = ?, address = ?, port = ?, width = ? WHERE id = ?",
        (*printer_values(data), params[0]),
    )
    return {"ok": True}


@route("DELETE", r"/api/printers/(\d+)", ("printers",))
def delete_printer(conn, user, params, data, query):
    conn.execute("UPDATE printers SET active = 0 WHERE id = ?", (params[0],))
    conn.execute("UPDATE products SET printer_id = NULL WHERE printer_id = ?", (params[0],))
    return {"ok": True}


@route("GET", "/api/printers/system", ("printers",))
def system_printers(conn, user, params, data, query):
    # Printerlarni qidirish bazaga tegmaydi - qulfdan tashqarida bajariladi
    def run():
        try:
            return printing.list_system_printers()
        except (PrintError, OSError) as e:
            raise ApiError(500, f"Printerlar ro'yxatini olib bo'lmadi: {e}")

    return Deferred(run)


@route("GET", "/api/printers/scan", ("printers",))
def scan_printers(conn, user, params, data, query):
    return Deferred(printing.scan_network)


def get_printer(conn, printer_id):
    printer = conn.execute(
        "SELECT * FROM printers WHERE id = ? AND active = 1", (printer_id,)
    ).fetchone()
    if not printer:
        raise ApiError(404, "Printer topilmadi")
    return dict(printer)


@route("POST", r"/api/printers/(\d+)/test", ("printers",))
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


@route("POST", r"/api/orders/(\d+)/kitchen-print", ("tables",))
def kitchen_print(conn, user, params, data, query):
    order = open_order(conn, params[0])
    items = order_items_with_printer(conn, params[0])
    groups = pending_lines(items, "printed_qty", only_with_printer=True)
    printed, errors = [], []
    if not groups:
        # Printer biriktirilmagan yoki yangi taom yo'q - xato emas, hech narsa chop etilmaydi
        result = order_detail(conn, params[0])
        result["printed"], result["errors"] = printed, errors
        return result
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


@route("POST", r"/api/orders/(\d+)/kitchen-send", ("tables",))
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


@route("GET", "/api/kitchen", ("kitchen",))
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


@route("POST", r"/api/kitchen/(\d+)/ready", ("kitchen",))
def kitchen_ready(conn, user, params, data, query):
    conn.execute(
        "UPDATE kitchen_tickets SET status = 'ready', ready_at = ? WHERE id = ?", (now(), params[0])
    )
    return {"ok": True}


# --- CRM: mijozlar va qarzlar


def normalize_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 9:  # 901234567 -> 998901234567
        digits = "998" + digits
    if len(digits) < 9 or len(digits) > 15:
        raise ApiError(400, "Telefon raqamini to'g'ri kiriting (masalan +998 90 123 45 67)")
    return "+" + digits


def get_customer(conn, customer_id):
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id or 0,)).fetchone()
    if not row:
        raise ApiError(404 if customer_id else 400, "Mijozni tanlang" if not customer_id else "Mijoz topilmadi")
    return dict(row)


def parse_due_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        raise ApiError(400, "To'lov muddatini tanlang")


def debt_paid(conn, debt_id):
    return conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM debt_payments WHERE debt_id = ? AND status = 'done'", (debt_id,)
    ).fetchone()[0]


def debt_bucket(due_date, today):
    due = datetime.strptime(due_date, "%Y-%m-%d").date()
    days = (due - today).days
    if days < 0:
        return "overdue", days
    return ("due" if days <= DUE_SOON_DAYS else "later"), days


def debts_query(conn, where="", args=()):
    today = datetime.now().date()
    result = []
    for r in conn.execute(
        f"""SELECT d.*, c.name AS customer_name, c.phone AS customer_phone,
                   COALESCE((SELECT SUM(amount) FROM debt_payments p WHERE p.debt_id = d.id AND p.status = 'done'), 0) AS paid
            FROM debts d JOIN customers c ON c.id = d.customer_id
            WHERE d.status != 'cancelled' {where} ORDER BY d.due_date, d.id""", args
    ):
        d = dict(r)
        d["remaining"] = d["amount"] - d["paid"]
        d["bucket"], d["days"] = debt_bucket(d["due_date"], today)
        if d["remaining"] <= 0:
            d["bucket"] = "closed"
        result.append(d)
    return result


def customer_values(data):
    name = clean_name(data.get("name"))
    if len(name) < 2:
        raise ApiError(400, "Mijoz ismini kiriting")
    if data.get("gender") not in ("m", "f"):
        raise ApiError(400, "Jinsini tanlang")
    return name, normalize_phone(data.get("phone")), data["gender"]


@route("GET", "/api/customers", ("crm", "cashier"))
def list_customers(conn, user, params, data, query):
    q = (query.get("q", [""])[0] or "").strip()
    sql = """SELECT c.*,
                    COALESCE((SELECT SUM(d.amount) FROM debts d WHERE d.customer_id = c.id AND d.status != 'cancelled'), 0)
                  - COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.customer_id = c.id AND p.status = 'done'), 0)
                    AS debt
             FROM customers c"""
    args = []
    if q:
        sql += " WHERE c.name LIKE ? OR c.phone LIKE ?"
        digits = re.sub(r"\D", "", q)
        args = [f"%{q}%", f"%{digits or q}%"]
    return rows(conn.execute(sql + " ORDER BY c.name LIMIT 500", args))


@route("POST", "/api/customers", ("crm", "cashier"))
def create_customer(conn, user, params, data, query):
    name, phone, gender = customer_values(data)
    if conn.execute("SELECT 1 FROM customers WHERE phone = ?", (phone,)).fetchone():
        raise ApiError(409, f"{phone} raqamli mijoz allaqachon bor")
    cur = conn.execute(
        "INSERT INTO customers (name, phone, gender, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
        (name, phone, gender, now(), user["id"]),
    )
    return get_customer(conn, cur.lastrowid)


@route("PUT", r"/api/customers/(\d+)", ("crm",))
def update_customer(conn, user, params, data, query):
    get_customer(conn, params[0])
    name, phone, gender = customer_values(data)
    if conn.execute("SELECT 1 FROM customers WHERE phone = ? AND id != ?", (phone, params[0])).fetchone():
        raise ApiError(409, f"{phone} raqamli boshqa mijoz bor")
    conn.execute("UPDATE customers SET name = ?, phone = ?, gender = ? WHERE id = ?", (name, phone, gender, params[0]))
    return get_customer(conn, params[0])


@route("GET", r"/api/customers/(\d+)", ("crm",))
def customer_detail(conn, user, params, data, query):
    customer = get_customer(conn, params[0])
    customer["debts"] = debts_query(conn, "AND d.customer_id = ?", (params[0],))
    customer["payments"] = rows(conn.execute(
        """SELECT p.*, u.full_name AS user_name FROM debt_payments p LEFT JOIN users u ON u.id = p.created_by
           WHERE p.customer_id = ? ORDER BY p.id DESC""", (params[0],)))
    customer["total_debt"] = sum(d["amount"] for d in customer["debts"])
    customer["total_paid"] = sum(d["paid"] for d in customer["debts"])
    customer["remaining"] = customer["total_debt"] - customer["total_paid"]
    return customer


@route("POST", r"/api/customers/(\d+)/debts", ("crm",))
def add_debt(conn, user, params, data, query):
    customer = get_customer(conn, params[0])
    amount = to_int(data.get("amount"), "amount", 1)
    cur = conn.execute(
        """INSERT INTO debts (customer_id, amount, due_date, comment, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (customer["id"], amount, parse_due_date(data.get("due_date")),
         (data.get("comment") or "").strip() or None, now(), user["id"]),
    )
    return {"id": cur.lastrowid}


@route("GET", "/api/debts", ("crm",))
def list_debts(conn, user, params, data, query):
    debts = [d for d in debts_query(conn) if d["bucket"] != "closed"]
    return {
        "debts": debts,
        "due_soon_days": DUE_SOON_DAYS,
        "totals": {b: sum(d["remaining"] for d in debts if d["bucket"] == b) for b in ("overdue", "due", "later")},
    }


@route("POST", r"/api/debts/(\d+)/pay", ("crm",))
def pay_debt(conn, user, params, data, query):
    debt = conn.execute("SELECT * FROM debts WHERE id = ? AND status != 'cancelled'", (params[0],)).fetchone()
    if not debt:
        raise ApiError(404, "Qarz topilmadi")
    method = data.get("method")
    if method not in DEBT_PAY_METHODS:
        raise ApiError(400, "To'lov usulini tanlang")
    remaining = debt["amount"] - debt_paid(conn, debt["id"])
    if remaining <= 0:
        raise ApiError(409, "Bu qarz to'liq to'langan")
    amount = to_int(data.get("amount") or remaining, "amount", 1)
    if amount > remaining:
        raise ApiError(400, f"Qarz qoldig'i {remaining:,} so'm - undan ko'p to'lab bo'lmaydi".replace(",", " "))
    conn.execute(
        """INSERT INTO debt_payments (debt_id, customer_id, amount, method, account, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (debt["id"], debt["customer_id"], amount, method, DEBT_PAY_METHODS[method], now(), user["id"]),
    )
    if amount == remaining:
        conn.execute("UPDATE debts SET status = 'closed' WHERE id = ?", (debt["id"],))
    return {"ok": True, "remaining": remaining - amount, "account": DEBT_PAY_METHODS[method]}


@route("POST", r"/api/debt-payments/(\d+)/cancel", ("crm", "finance"))
def cancel_debt_payment(conn, user, params, data, query):
    payment = conn.execute("SELECT * FROM debt_payments WHERE id = ?", (params[0],)).fetchone()
    if not payment:
        raise ApiError(404, "To'lov topilmadi")
    if payment["status"] != "done":
        raise ApiError(409, "To'lov allaqachon bekor qilingan")
    conn.execute(
        """UPDATE debt_payments SET status = 'cancelled', cancelled_at = ?, cancelled_by = ?, cancel_reason = ?
           WHERE id = ?""",
        (now(), user["id"], (data.get("reason") or "").strip() or None, params[0]),
    )
    # to'lov bekor bo'ldi - qarz yana ochiq
    conn.execute("UPDATE debts SET status = 'open' WHERE id = ? AND status = 'closed'", (payment["debt_id"],))
    return {"ok": True}


# --- moliya: kassa balansi, kirim/chiqim, tranzaksiya turlari


def clean_name(text):
    return " ".join((text or "").split())


@route("GET", "/api/finance/types", ("finance",))
def list_finance_types(conn, user, params, data, query):
    return rows(conn.execute(
        """SELECT t.*, u.full_name AS created_by_name,
                  (SELECT COUNT(*) FROM finance_entries e WHERE e.type_id = t.id AND e.status = 'done') AS used
           FROM finance_types t LEFT JOIN users u ON u.id = t.created_by
           ORDER BY t.is_system DESC, t.direction, t.name"""
    ))


@route("POST", "/api/finance/types", ("finance",))
def create_finance_type(conn, user, params, data, query):
    name = clean_name(data.get("name"))
    if len(name) < 2:
        raise ApiError(400, "Tranzaksiya nomini kiriting")
    if data.get("direction") not in ("in", "out"):
        raise ApiError(400, "Kirim yoki chiqimni tanlang")
    if conn.execute("SELECT 1 FROM finance_types WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise ApiError(409, f"\"{name}\" nomli tranzaksiya allaqachon bor")
    cur = conn.execute(
        "INSERT INTO finance_types (name, direction, created_at, created_by) VALUES (?, ?, ?, ?)",
        (name, data["direction"], now(), user["id"]),
    )
    return {"id": cur.lastrowid}
# Yaratilgan tur o'zgartirilmaydi va o'chirilmaydi - PUT/DELETE yo'q


def finance_balance(conn):
    accounts = {m: {"account": m, "sales": 0, "debt": 0, "in": 0, "out": 0} for m in FINANCE_ACCOUNTS}
    for method, total in conn.execute(
        "SELECT payment_method, COALESCE(SUM(total), 0) FROM orders WHERE status = 'paid' GROUP BY payment_method"
    ):
        if method in accounts:
            accounts[method]["sales"] = total
    for account, direction, total in conn.execute(
        """SELECT account, direction, COALESCE(SUM(amount), 0) FROM finance_entries
           WHERE status = 'done' GROUP BY account, direction"""
    ):
        if account in accounts:
            accounts[account][direction] = total
    for account, total in conn.execute(
        "SELECT account, COALESCE(SUM(amount), 0) FROM debt_payments WHERE status = 'done' GROUP BY account"
    ):
        if account in accounts:
            accounts[account]["debt"] = total
    for a in accounts.values():
        a["balance"] = a["sales"] + a["debt"] + a["in"] - a["out"]
    result = list(accounts.values())
    return {"accounts": result, "total": sum(a["balance"] for a in result)}


@route("GET", "/api/finance/balance", ("finance",))
def get_finance_balance(conn, user, params, data, query):
    return finance_balance(conn)


@route("POST", "/api/finance/entries", ("finance",))
def create_finance_entry(conn, user, params, data, query):
    require(data, "type_id", "account", "amount")
    ftype = conn.execute("SELECT * FROM finance_types WHERE id = ?", (data["type_id"],)).fetchone()
    if not ftype:
        raise ApiError(404, "Tranzaksiya turi topilmadi")
    if data["account"] not in FINANCE_ACCOUNTS:
        raise ApiError(400, "Hisobni tanlang (naqd, karta...)")
    amount = to_int(data["amount"], "amount", 1)
    cur = conn.execute(
        """INSERT INTO finance_entries (type_id, direction, account, amount, comment, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ftype["id"], ftype["direction"], data["account"], amount,
         (data.get("comment") or "").strip() or None, now(), user["id"]),
    )
    return {"id": cur.lastrowid, "balance": finance_balance(conn)}


@route("POST", r"/api/finance/entries/(\d+)/cancel", ("finance",))
def cancel_finance_entry(conn, user, params, data, query):
    entry = conn.execute("SELECT * FROM finance_entries WHERE id = ?", (params[0],)).fetchone()
    if not entry:
        raise ApiError(404, "Tranzaksiya topilmadi")
    if entry["status"] != "done":
        raise ApiError(409, "Tranzaksiya allaqachon bekor qilingan")
    conn.execute(
        """UPDATE finance_entries SET status = 'cancelled', cancelled_at = ?, cancelled_by = ?, cancel_reason = ?
           WHERE id = ?""",
        (now(), user["id"], (data.get("reason") or "").strip() or None, params[0]),
    )
    return {"ok": True}


@route("POST", r"/api/finance/sales/(\d+)/cancel", ("finance",))
def cancel_sale(conn, user, params, data, query):
    """Savdoni bekor qilish (pulni qaytarish): buyurtma 'refunded' bo'ladi - tushum va balansdan chiqadi.
    Buyurtma o'chirilmaydi, tarixda qoladi."""
    order = conn.execute("SELECT status FROM orders WHERE id = ?", (params[0],)).fetchone()
    if not order:
        raise ApiError(404, "Buyurtma topilmadi")
    if order["status"] != "paid":
        raise ApiError(409, "Bu savdo allaqachon bekor qilingan")
    debt = conn.execute("SELECT * FROM debts WHERE order_id = ? AND status != 'cancelled'", (params[0],)).fetchone()
    if debt:  # qarzga sotilgan bo'lsa - qarz ham bekor bo'ladi
        if debt_paid(conn, debt["id"]) > 0:
            raise ApiError(409, "Bu qarz bo'yicha to'lovlar bor. Avval ularni bekor qiling")
        conn.execute("UPDATE debts SET status = 'cancelled' WHERE id = ?", (debt["id"],))
    conn.execute(
        "UPDATE orders SET status = 'refunded', refunded_at = ?, refunded_by = ?, refund_reason = ? WHERE id = ?",
        (now(), user["id"], (data.get("reason") or "").strip() or None, params[0]),
    )
    return {"ok": True}


@route("GET", "/api/finance/entries", ("finance",))
def list_finance_entries(conn, user, params, data, query):
    today = datetime.now().date()
    date_from = query.get("from", [str(today.replace(day=1))])[0]
    date_to = query.get("to", [str(today)])[0]
    for d in (date_from, date_to):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ApiError(400, "Sana formati: YYYY-MM-DD")
    rng = (date_from + " 00:00:00", date_to + " 23:59:59")
    source = query.get("source", ["all"])[0]
    direction = query.get("direction", [""])[0]
    account = query.get("account", [""])[0]

    entries = []
    if source in ("all", "manual"):
        entries += [dict(r, source="manual") for r in conn.execute(
            """SELECT e.id, e.direction, e.account, e.amount, e.comment, e.status, e.created_at,
                      e.cancelled_at, e.cancel_reason, t.name AS type_name,
                      u.full_name AS user_name, cu.full_name AS cancelled_by_name
               FROM finance_entries e
               JOIN finance_types t ON t.id = e.type_id
               LEFT JOIN users u ON u.id = e.created_by
               LEFT JOIN users cu ON cu.id = e.cancelled_by
               WHERE e.created_at BETWEEN ? AND ?""", rng)]
    if source in ("all", "sales") and direction in ("", "in"):
        # Savdo tushumlari - avtomatik kirim (buyurtmani bekor qilish Savdo bo'limida)
        entries += [dict(r, source="sale", direction="in", type_name="Savdo") for r in conn.execute(
            """SELECT o.id, o.payment_method AS account, o.total AS amount, o.closed_at AS created_at,
                      'Buyurtma #' || o.id AS comment, u.full_name AS user_name,
                      CASE o.status WHEN 'paid' THEN 'done' ELSE 'cancelled' END AS status,
                      o.refunded_at AS cancelled_at, o.refund_reason AS cancel_reason,
                      ru.full_name AS cancelled_by_name
               FROM orders o
               LEFT JOIN users u ON u.id = o.cashier_id
               LEFT JOIN users ru ON ru.id = o.refunded_by
               WHERE o.status IN ('paid', 'refunded') AND o.payment_method != 'debt'
                 AND o.closed_at BETWEEN ? AND ?""", rng)]
    if source in ("all", "debts") and direction in ("", "in"):
        entries += [dict(r, source="debt", direction="in", type_name="Qarz to'lovi") for r in conn.execute(
            """SELECT p.id, p.account, p.amount, p.created_at, p.status, p.cancelled_at, p.cancel_reason,
                      c.name || ' · ' || c.phone AS comment, u.full_name AS user_name,
                      cu.full_name AS cancelled_by_name, p.method
               FROM debt_payments p
               JOIN customers c ON c.id = p.customer_id
               LEFT JOIN users u ON u.id = p.created_by
               LEFT JOIN users cu ON cu.id = p.cancelled_by
               WHERE p.created_at BETWEEN ? AND ?""", rng)]
    if direction:
        entries = [e for e in entries if e["direction"] == direction]
    if account:
        entries = [e for e in entries if e["account"] == account]
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    done = [e for e in entries if e["status"] == "done"]
    return {
        "from": date_from, "to": date_to,
        "entries": entries[:1000],
        "total_in": sum(e["amount"] for e in done if e["direction"] == "in"),
        "total_out": sum(e["amount"] for e in done if e["direction"] == "out"),
    }


# --- bosh sahifa (savdo ko'rsatkichlari)

DASHBOARD_PERIODS = ("today", "week", "month", "year")
MONTHS = ("Yan", "Fev", "Mar", "Apr", "May", "Iyun", "Iyul", "Avg", "Sen", "Okt", "Noy", "Dek")
WEEKDAYS = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")


def period_range(period, today):
    if period == "today":
        start = today
    elif period == "week":
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    else:
        start = today.replace(month=1, day=1)
    return start, today


def paid_between(conn, start, end, extra="", args=()):
    return conn.execute(
        f"""SELECT {extra or "COUNT(*) AS orders, COALESCE(SUM(total), 0) AS revenue"}
            FROM orders o WHERE o.status = 'paid' AND o.closed_at BETWEEN ? AND ?""",
        (f"{start} 00:00:00", f"{end} 23:59:59", *args),
    )


@route("GET", "/api/dashboard", ("reports",))
def dashboard(conn, user, params, data, query):
    period = query.get("period", ["month"])[0]
    if period not in DASHBOARD_PERIODS:
        raise ApiError(400, "Noto'g'ri davr")
    today = datetime.now().date()
    start, end = period_range(period, today)
    rng = (f"{start} 00:00:00", f"{end} 23:59:59")
    where = "o.status = 'paid' AND o.closed_at BETWEEN ? AND ?"

    today_row = dict(paid_between(conn, today, today).fetchone())
    month_row = dict(paid_between(conn, today.replace(day=1), today).fetchone())
    summary = dict(paid_between(conn, start, end).fetchone())
    summary["average"] = summary["revenue"] // summary["orders"] if summary["orders"] else 0
    summary["items"] = conn.execute(
        f"""SELECT COALESCE(SUM(i.qty), 0) FROM order_items i JOIN orders o ON o.id = i.order_id
            WHERE {where}""", rng,
    ).fetchone()[0]
    summary["cancelled"] = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'cancelled' AND closed_at BETWEEN ? AND ?", rng
    ).fetchone()[0]
    summary["open"] = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'open'").fetchone()[0]

    by_method = {m: 0 for m in PAYMENT_METHODS}
    for r in conn.execute(
        f"SELECT payment_method, SUM(total) FROM orders o WHERE {where} GROUP BY payment_method", rng
    ):
        by_method[r[0]] = r[1]

    # Grafik: bugun - soatlar, hafta/oy - kunlar, yil - oylar
    if period == "today":
        bucket, keys = "CAST(strftime('%H', o.closed_at) AS INTEGER)", list(range(24))
        labels = [f"{h:02d}" for h in keys]
    elif period == "year":
        bucket, keys = "CAST(strftime('%m', o.closed_at) AS INTEGER)", list(range(1, 13))
        labels = list(MONTHS)
    else:
        bucket = "date(o.closed_at)"
        days = (end - start).days + 1 if period == "month" else 7
        dates = [start + timedelta(days=i) for i in range(days)]
        keys = [d.isoformat() for d in dates]
        labels = [WEEKDAYS[d.weekday()] if period == "week" else str(d.day) for d in dates]
    values = {r[0]: r[1] for r in conn.execute(
        f"SELECT {bucket} AS k, SUM(total) FROM orders o WHERE {where} GROUP BY k", rng
    )}
    series = [{"label": lab, "value": values.get(k, 0) or 0} for k, lab in zip(keys, labels)]

    return {
        "period": period,
        "from": str(start),
        "to": str(end),
        "today": today_row,
        "month": month_row,
        "summary": summary,
        "by_method": [{"method": m, "revenue": v} for m, v in by_method.items()],
        "series": series,
    }


# --- hisobot


@route("GET", "/api/reports", ("reports",))
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
                       COALESCE(SUM(discount), 0) AS discount, COALESCE(SUM(service), 0) AS service
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
                f"""SELECT o.id, o.type, o.total, o.discount, o.service, o.payment_method, o.closed_at,
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


def effective_permissions(role, stored):
    if role == "admin":
        return list(PERMISSIONS)
    if stored is None:
        return list(ROLE_DEFAULTS.get(role, ()))
    try:
        return [p for p in json.loads(stored) if p in PERMISSIONS]
    except (ValueError, TypeError):
        return list(ROLE_DEFAULTS.get(role, ()))


def public_user(row):
    user = {k: row[k] for k in ("id", "username", "full_name", "role")}
    user["permissions"] = effective_permissions(row["role"], row["permissions"])
    return user


@route("GET", "/api/users", ("users",))
def list_users(conn, user, params, data, query):
    users = rows(
        conn.execute(
            """SELECT id, username, full_name, first_name, last_name, phone, role, permissions, active
               FROM users ORDER BY active DESC, id"""
        )
    )
    for u in users:
        if u["first_name"] is None:  # eski versiyada yaratilgan xodim
            u["first_name"], _, u["last_name"] = u["full_name"].partition(" ")
        u["permissions"] = effective_permissions(u["role"], u["permissions"])
    return users


def user_values(conn, user, data, target=None):
    """Ism, familiya, telefon, rol va ruxsatlarni tekshiradi."""
    if not data.get("first_name") and data.get("full_name"):
        data["first_name"], _, data["last_name"] = data["full_name"].strip().partition(" ")
    require(data, "first_name", "role")
    if data["role"] not in ROLES:
        raise ApiError(400, "Noto'g'ri rol")
    # Administratorni faqat administrator yaratadi/o'zgartiradi
    target_role = target["role"] if target else None
    if user["role"] != "admin" and "admin" in (data["role"], target_role):
        raise ApiError(403, "Administratorni faqat administrator boshqaradi")
    first = data["first_name"].strip()
    last = (data.get("last_name") or "").strip()
    phone = (data.get("phone") or "").strip() or None
    if data["role"] == "admin":
        perms = None
    elif "permissions" in data:
        if not isinstance(data["permissions"], list):
            raise ApiError(400, "Ruxsatlar ro'yxat bo'lishi kerak")
        perms = json.dumps([p for p in PERMISSIONS if p in data["permissions"]])
    else:
        perms = target["permissions"] if target and target["role"] == data["role"] else None
    return first, last, f"{first} {last}".strip(), phone, data["role"], perms


def check_password(password):
    if len(password or "") < 4:
        raise ApiError(400, "Parol kamida 4 belgi bo'lishi kerak")


@route("POST", "/api/users", ("users",))
def create_user(conn, user, params, data, query):
    require(data, "username", "password")
    check_password(data["password"])
    username = data["username"].strip()
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise ApiError(409, "Bu username band")
    pw, salt = hash_password(data["password"])
    cur = conn.execute(
        """INSERT INTO users (first_name, last_name, full_name, phone, role, permissions,
                              username, password_hash, salt) VALUES (?,?,?,?,?,?,?,?,?)""",
        (*user_values(conn, user, data), username, pw, salt),
    )
    return {"id": cur.lastrowid}


def get_user_row(conn, user_id):
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise ApiError(404, "Xodim topilmadi")
    return target


@route("PUT", r"/api/users/(\d+)", ("users",))
def update_user(conn, user, params, data, query):
    target_id = int(params[0])
    target = get_user_row(conn, target_id)
    values = user_values(conn, user, data, target)
    active = 1 if data.get("active", True) else 0
    if target_id == user["id"]:
        if not active or values[4] != user["role"]:
            raise ApiError(400, "O'zingizning rolingizni o'zgartira yoki o'chira olmaysiz")
        if "users" not in effective_permissions(values[4], values[5]):
            raise ApiError(400, "O'zingizdan \"Xodimlar\" ruxsatini olib tashlay olmaysiz")
    conn.execute(
        """UPDATE users SET first_name = ?, last_name = ?, full_name = ?, phone = ?, role = ?,
                  permissions = ?, active = ? WHERE id = ?""",
        (*values, active, target_id),
    )
    if data.get("password"):
        check_password(data["password"])
        pw, salt = hash_password(data["password"])
        conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (pw, salt, target_id))
    if not active or data.get("password"):
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    return {"ok": True}


@route("DELETE", r"/api/users/(\d+)", ("users",))
def delete_user(conn, user, params, data, query):
    target_id = int(params[0])
    target = get_user_row(conn, target_id)
    if target_id == user["id"]:
        raise ApiError(400, "O'zingizni o'chira olmaysiz")
    if target["role"] == "admin" and user["role"] != "admin":
        raise ApiError(403, "Administratorni faqat administrator boshqaradi")
    # Buyurtmalar tarixi saqlanishi uchun o'chirilmaydi, faqat bloklanadi
    conn.execute("UPDATE users SET active = 0 WHERE id = ?", (target_id,))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
    return {"ok": True}


# --- tarmoq


def lan_urls():
    """Asosiy (Wi-Fi/LAN) manzil birinchi; qolganlari - VPN, VirtualBox kabi virtual adapterlar."""
    main = printing.primary_ip()
    _, ips = printing.local_networks()
    others = sorted(ip for ip in ips if ip != main and not ip.startswith(("127.", "169.254.")))
    return [f"http://{ip}:{PORT}" for ip in ([main] if main else []) + others]


@route("GET", "/api/network")
def network_info(conn, user, params, data, query):
    urls = lan_urls()
    return {"port": PORT, "main": urls[0] if urls else None, "others": urls[1:]}


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
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/manifest+json"):
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
            """SELECT u.id, u.username, u.full_name, u.role, u.permissions FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > ? AND u.active = 1""",
            (token, now()),
        ).fetchone()
        return public_user(row) if row else None

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
        for r_method, pattern, perms, fn in ROUTES:
            m = pattern.match(path)
            if not m:
                continue
            path_matched = True
            if r_method != method:
                continue
            if perms and not set(perms) & set(user["permissions"]):
                raise ApiError(403, "Bu bo'limga ruxsatingiz yo'q")
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
        return 200, public_user(row), {"Set-Cookie": cookie}


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
    # TOXTATISH.bat serverni shu raqam orqali to'xtatadi
    pid_path = os.path.join(BASE_DIR, "cafepos.pid")
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(pid_path) and os.remove(pid_path))
    print("=" * 50)
    print(f"  CafePOS ishga tushdi: {url}")
    lans = lan_urls()
    if lans:
        print(f"  Telefon/planshetdan (shu Wi-Fi): {lans[0]}")
    for lan in lans[1:]:
        print(f"    boshqa adapter (odatda kerak emas): {lan}")
    print("  Login: admin   Parol: admin123")
    print("  To'xtatish: TOXTATISH.bat (yoki shu oynada Ctrl+C)")
    print("=" * 50)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTo'xtatildi.")


if __name__ == "__main__":
    main()
