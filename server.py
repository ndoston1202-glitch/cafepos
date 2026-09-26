"""CafePOS - kafe uchun yengil POS tizimi.

Faqat Python standart kutubxonasi ishlatiladi (pip install shart emas).
Ishga tushirish:  python server.py   ->  http://localhost:8000
"""

import atexit
import base64
import hashlib
import hmac
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
from xml.etree import ElementTree

import printing
import telegram
import xlsx
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

ROLES = ("admin", "cashier", "waiter", "cook", "staff")  # staff = ruxsatlari qo'lda belgilangan xodim
# Bo'limlarga kirish ruxsatlari
PERMISSIONS = ("tables", "cashier", "kitchen", "reports", "menu", "crm", "finance", "halls", "printers", "users",
               "settings", "journal", "integrations")
ROLE_DEFAULTS = {
    "admin": PERMISSIONS,
    "cashier": ("tables", "cashier", "kitchen", "reports", "crm"),
    "waiter": ("tables",),
    "cook": ("kitchen",),
    "staff": (),
}
# Buyurtma to'lov usullari ("debt" = qarzga - pul keyin CRM > Qarzlar orqali tushadi)
PAYMENT_METHODS = ("cash", "card", "payme", "click", "debt")
# Moliya hisoblari (kassa balansi)
FINANCE_ACCOUNTS = ("cash", "card", "payme", "click", "bank")
# Qarz to'lash usullari -> qaysi hisobga tushadi
DEBT_PAY_METHODS = {"cash": "cash", "click": "card", "terminal": "bank", "transfer": "bank"}
DUE_SOON_DAYS = 3
SYSTEM_FINANCE_TYPES = (("Mijoz balansini to'ldirish", "in"), ("Ta'minotchiga pul berish", "out"))
ADJUST_TYPES = {"in": "Kassa balansini tuzatish (+)", "out": "Kassa balansini tuzatish (-)"}
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
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
-- Balansni o'rnatish tarixi (kassa hisobi / mijoz / ta'minotchi). O'chirilmaydi.
CREATE TABLE IF NOT EXISTS balance_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,              -- account / customer / supplier
    target_id INTEGER,
    account TEXT,
    old_balance INTEGER NOT NULL,
    new_balance INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
-- Savdodan qisman qaytarish (mijoz taomni qaytardi - pul qaytarildi). O'chirilmaydi.
CREATE TABLE IF NOT EXISTS order_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount INTEGER NOT NULL,
    items TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
-- Jurnal: barcha amallar (kim, qachon, nima). O'chirilmaydi.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    user_name TEXT,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    details TEXT,
    entity TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
-- Integratsiyalar (Telegram bot va boshqalar): sozlamalar JSON ko'rinishida
CREATE TABLE IF NOT EXISTS integrations (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    config TEXT,
    updated_at TEXT
);
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
    ("finance_types", "is_adjust", "INTEGER NOT NULL DEFAULT 0"),  # balans tuzatish turlari
    ("finance_entries", "supplier_id", "INTEGER REFERENCES suppliers(id)"),
    # Qaytarilgan summa (qisman qaytarish) va qaytarilgan miqdor
    ("orders", "returned", "INTEGER NOT NULL DEFAULT 0"),
    ("order_items", "returned_qty", "INTEGER NOT NULL DEFAULT 0"),
    # Savdoda tanlangan mijoz (chek mijozlar botiga boradi)
    ("orders", "customer_id", "INTEGER REFERENCES customers(id)"),
    # Mijozlar boti: mijoz telefon raqamini yuborib ulanadi
    ("customers", "telegram_chat_id", "TEXT"),
    ("customers", "telegram_linked_at", "TEXT"),
    ("users", "first_name", "TEXT"),
    ("users", "last_name", "TEXT"),
    ("users", "phone", "TEXT"),
    # JSON ro'yxat; NULL = rol bo'yicha standart ruxsatlar
    ("users", "permissions", "TEXT"),
    # PIN kod bilan kirish: HMAC(sir, pin) - qidirish uchun, bir xil PIN ikki xodimda bo'lmaydi
    ("users", "pin_lookup", "TEXT"),
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


def pin_hash(conn, pin):
    secret = conn.execute("SELECT value FROM settings WHERE key = '_pin_secret'").fetchone()[0]
    return hmac.new(secret.encode(), str(pin).encode(), hashlib.sha256).hexdigest()


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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_pin ON users(pin_lookup)")
    if not conn.execute("SELECT 1 FROM settings WHERE key = '_pin_secret'").fetchone():
        conn.execute("INSERT INTO settings (key, value) VALUES ('_pin_secret', ?)", (secrets.token_hex(32),))
    if not conn.execute("SELECT 1 FROM users WHERE pin_lookup IS NOT NULL").fetchone():
        # PIN hali hech kimda yo'q - administratorga standart PIN 1234 (keyin Xodimlar bo'limida o'zgartiriladi)
        conn.execute("UPDATE users SET pin_lookup = ? WHERE id = (SELECT MIN(id) FROM users WHERE role = 'admin')",
                     (pin_hash(conn, DEFAULT_PIN),))
    # Bazaviy tranzaksiya turlari doim bo'ladi
    for name, direction in SYSTEM_FINANCE_TYPES + tuple((n, d) for d, n in ADJUST_TYPES.items()):
        conn.execute(
            """INSERT OR IGNORE INTO finance_types (name, direction, is_system, created_at)
               VALUES (?, ?, 1, ?)""",
            (name, direction, now()),
        )
    conn.executemany("UPDATE finance_types SET is_adjust = 1 WHERE name = ?", [(n,) for n in ADJUST_TYPES.values()])
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


class FileResponse:
    """Fayl yuklab berish (masalan, import shabloni)."""

    def __init__(self, content, filename, ctype):
        self.content, self.filename, self.ctype = content, filename, ctype


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


def ensure_unique(conn, table, name, label, exclude_id=None, where="", args=()):
    """Bir xil nomli yozuv ikkinchi marta yaratilmasin (katta-kichik harf va ortiqcha bo'shliqlar hisobga olinmaydi)."""
    sql = f"SELECT 1 FROM {table} WHERE lower(trim(name)) = lower(?) {where}"
    params = [name, *args]
    if exclude_id:
        sql += " AND id != ?"
        params.append(exclude_id)
    if conn.execute(sql, params).fetchone():
        raise ApiError(409, f"\"{name}\" nomli {label} allaqachon bor")


def to_int(value, field, minimum=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{field}' butun son bo'lishi kerak")
    if minimum is not None and n < minimum:
        raise ApiError(400, f"'{field}' kamida {minimum} bo'lishi kerak")
    return n


DEFAULT_SETTINGS = {"cafe_name": "CafePOS", "service_percent": "0"}
# Chekda nimalar chiqadi (Sozlamalar > Chek)
RECEIPT_DEFAULTS = {
    "show_logo": False, "show_cafe_name": True, "header_text": "",
    "show_order_number": True, "show_date": True, "show_place": True, "show_waiter": True,
    "show_cashier": False, "show_customer": True, "show_item_price": True,
    "show_service": True, "show_discount": True, "show_payment": True,
    "footer_text": "Xaridingiz uchun rahmat!", "paper_width": 80,
}
DEFAULT_PIN = "1234"
PIN_LENGTH = 4


def get_settings(conn):
    settings = dict(DEFAULT_SETTINGS)
    # "_" bilan boshlanadigan kalitlar - ichki sirlar, tashqariga berilmaydi
    settings.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings WHERE key NOT LIKE '\\_%' ESCAPE '\\'")})
    settings["service_percent"] = float(settings["service_percent"] or 0)
    receipt = dict(RECEIPT_DEFAULTS)
    try:
        receipt.update(json.loads(settings.get("receipt") or "{}"))
    except ValueError:
        pass
    settings["receipt"] = receipt
    return settings


def receipt_values(data):
    if not isinstance(data, dict):
        raise ApiError(400, "Chek sozlamalari noto'g'ri")
    out = {}
    for key, default in RECEIPT_DEFAULTS.items():
        value = data.get(key, default)
        if isinstance(default, bool):
            out[key] = bool(value)
        elif key == "paper_width":
            out[key] = 58 if str(value) == "58" else 80
        else:
            text = str(value or "").strip()
            if len(text) > 300:
                raise ApiError(400, "Chek matni juda uzun (300 belgigacha)")
            out[key] = text
    return out


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
        """SELECT o.*, t.name AS table_name, t.hall_id, h.name AS hall_name, w.full_name AS waiter_name,
                  k.full_name AS cashier_name, c.name AS customer_name
           FROM orders o
           LEFT JOIN tables t ON t.id = o.table_id
           LEFT JOIN halls h ON h.id = t.hall_id
           LEFT JOIN users w ON w.id = o.waiter_id
           LEFT JOIN users k ON k.id = o.cashier_id
           LEFT JOIN customers c ON c.id = o.customer_id
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
    name = clean_name(data["name"])
    ensure_unique(conn, "categories", name, "kategoriya")
    cur = conn.execute(
        "INSERT INTO categories (name, sort) VALUES (?, ?)",
        (name, to_int(data.get("sort", 0), "sort")),
    )
    return {"id": cur.lastrowid}


@route("PUT", r"/api/categories/(\d+)", ("menu",))
def update_category(conn, user, params, data, query):
    require(data, "name")
    name = clean_name(data["name"])
    ensure_unique(conn, "categories", name, "kategoriya", params[0])
    conn.execute(
        "UPDATE categories SET name = ?, sort = ? WHERE id = ?",
        (name, to_int(data.get("sort", 0), "sort"), params[0]),
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
        clean_name(data["name"]),
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
    ensure_unique(conn, "products", clean_name(data.get("name")), "mahsulot", where="AND active = 1")
    cur = conn.execute(
        "INSERT INTO products (category_id, name, price, cost, printer_id) VALUES (?,?,?,?,?)",
        product_values(data),
    )
    save_product_image(conn, cur.lastrowid, data)
    return {"id": cur.lastrowid}


@route("PUT", r"/api/products/(\d+)", ("menu",))
def update_product(conn, user, params, data, query):
    ensure_unique(conn, "products", clean_name(data.get("name")), "mahsulot", params[0], "AND active = 1")
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
    if "receipt" in data:
        values["receipt"] = json.dumps(receipt_values(data["receipt"]), ensure_ascii=False)
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
    return clean_name(data["name"]), to_int(data.get("sort") or 0, "sort"), percent


@route("POST", "/api/halls", ("halls",))
def create_hall(conn, user, params, data, query):
    values = hall_values(data)
    ensure_unique(conn, "halls", values[0], "zal")
    cur = conn.execute("INSERT INTO halls (name, sort, service_percent) VALUES (?, ?, ?)", values)
    return {"id": cur.lastrowid}


@route("PUT", r"/api/halls/(\d+)", ("halls",))
def update_hall(conn, user, params, data, query):
    values = hall_values(data)
    ensure_unique(conn, "halls", values[0], "zal", params[0])
    conn.execute(
        "UPDATE halls SET name = ?, sort = ?, service_percent = ? WHERE id = ?",
        (*values, params[0]),
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
    return clean_name(data["name"]), to_int(data.get("seats") or 4, "seats", 1), hall_id


def ensure_unique_table(conn, values, exclude_id=None):
    ensure_unique(conn, "tables", values[0], "stol (shu zalda)", exclude_id,
                  "AND active = 1 AND COALESCE(hall_id, 0) = ?", (values[2] or 0,))


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
    values = table_values(conn, data)
    ensure_unique_table(conn, values)
    cur = conn.execute("INSERT INTO tables (name, seats, hall_id) VALUES (?, ?, ?)", values)
    return {"id": cur.lastrowid}


@route("PUT", r"/api/tables/(\d+)", ("halls",))
def update_table(conn, user, params, data, query):
    values = table_values(conn, data)
    ensure_unique_table(conn, values, params[0])
    conn.execute(
        "UPDATE tables SET name = ?, seats = ?, hall_id = ? WHERE id = ?",
        (*values, params[0]),
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
    # Mijoz ixtiyoriy (qarzga sotishda shart) - tanlansa chek mijozlar botiga boradi
    customer = get_customer(conn, data.get("customer_id")) if data.get("customer_id") or method == "debt" else None
    if method == "debt":  # qarzga: mijoz va to'lov muddati shart
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
                  payment_method = ?, cashier_id = ?, closed_at = ?, customer_id = ? WHERE id = ?""",
        (discount, order["service_percent"], order["service"], order["total"], method, user["id"], now(),
         customer["id"] if customer else None, params[0]),
    )
    paid = order_detail(conn, params[0])
    if customer:
        paid["customer_name"] = customer["name"]
        paid["customer_notified"] = notify_customer(conn, customer, receipt_text(conn, paid, customer))
    return paid


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
        clean_name(data["name"]),
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
    values = printer_values(data)
    ensure_unique(conn, "printers", values[0], "printer", where="AND active = 1")
    cur = conn.execute("INSERT INTO printers (name, kind, address, port, width) VALUES (?,?,?,?,?)", values)
    return {"id": cur.lastrowid}


@route("PUT", r"/api/printers/(\d+)", ("printers",))
def update_printer(conn, user, params, data, query):
    values = printer_values(data)
    ensure_unique(conn, "printers", values[0], "printer", params[0], "AND active = 1")
    conn.execute(
        "UPDATE printers SET name = ?, kind = ?, address = ?, port = ?, width = ? WHERE id = ?",
        (*values, params[0]),
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
    where = []
    if q:
        where.append("(c.name LIKE ? OR c.phone LIKE ?)")
        digits = re.sub(r"\D", "", q)
        args = [f"%{q}%", f"%{digits or q}%"]
    if query.get("telegram", [""])[0] == "1":  # faqat mijozlar botiga ulanganlar
        where.append("c.telegram_chat_id IS NOT NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
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
    customer = get_customer(conn, debt["customer_id"])
    total_left = customer_remaining(conn, customer["id"])
    notify_customer(conn, customer, f"✅ <b>To'lovingiz qabul qilindi</b>\nSumma: {fmt_money(amount)}\n"
                    + (f"Qolgan qarzingiz: <b>{fmt_money(total_left)}</b>" if total_left > 0 else "Qarzingiz to'liq yopildi 🎉"))
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
        "SELECT payment_method, COALESCE(SUM(total - returned), 0) FROM orders WHERE status = 'paid' GROUP BY payment_method"
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
    supplier_id = data.get("supplier_id") or None
    if supplier_id:
        if ftype["direction"] != "out":
            raise ApiError(400, "Ta'minotchi faqat chiqimda tanlanadi")
        get_supplier(conn, supplier_id)
    cur = conn.execute(
        """INSERT INTO finance_entries (type_id, direction, account, amount, comment, created_at, created_by, supplier_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ftype["id"], ftype["direction"], data["account"], amount,
         (data.get("comment") or "").strip() or None, now(), user["id"], supplier_id),
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


@route("POST", r"/api/sales/(\d+)/return", ("finance", "cashier"))
def return_sale(conn, user, params, data, query):
    """Chekdan tanlangan taomlarni qaytarish: pul (chegirma va xizmat haqi ulushi bilan) qaytariladi."""
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (params[0],)).fetchone()
    if not order:
        raise ApiError(404, "Savdo topilmadi")
    if order["status"] != "paid":
        raise ApiError(409, "Faqat yopilgan (to'langan) savdodan qaytarish mumkin")
    items = {i["id"]: i for i in rows(conn.execute("SELECT * FROM order_items WHERE order_id = ?", (params[0],)))}
    subtotal = sum(i["price"] * i["qty"] for i in items.values())
    lines, value = [], 0
    for line in data.get("items") or []:
        item = items.get(to_int(line.get("item_id"), "item_id"))
        qty = to_int(line.get("qty"), "qty", 0)
        if not item or qty == 0:
            continue
        if qty > item["qty"] - item["returned_qty"]:
            raise ApiError(400, f"{item['name']}: {item['qty'] - item['returned_qty']} tadan ko'p qaytarib bo'lmaydi")
        lines.append({"item_id": item["id"], "name": item["name"], "qty": qty, "price": item["price"]})
        value += item["price"] * qty
    if not lines:
        raise ApiError(400, "Qaytariladigan taomni va sonini tanlang")
    left = order["total"] - order["returned"]
    all_back = all(items[l["item_id"]]["qty"] - items[l["item_id"]]["returned_qty"] == l["qty"] for l in lines) and \
        sum(l["qty"] for l in lines) == sum(i["qty"] - i["returned_qty"] for i in items.values())
    # chegirma va xizmat haqi ulushi hisobga olinadi; hammasi qaytsa - qolgan summa to'liq
    amount = left if all_back else min(left, round(value * order["total"] / subtotal) if subtotal else 0)
    if order["payment_method"] == "debt":  # qarzga sotilgan - qarz kamayadi
        debt = conn.execute("SELECT * FROM debts WHERE order_id = ? AND status != 'cancelled'", (order["id"],)).fetchone()
        if debt:
            if debt["amount"] - debt_paid(conn, debt["id"]) < amount:
                raise ApiError(409, "Qarzning to'langan qismidan ko'p qaytarib bo'lmaydi. Avval to'lovni bekor qiling")
            conn.execute("UPDATE debts SET amount = amount - ? WHERE id = ?", (amount, debt["id"]))
            conn.execute("UPDATE debts SET status = 'closed' WHERE id = ? AND amount <= ?", (debt["id"], debt_paid(conn, debt["id"])))
    for l in lines:
        conn.execute("UPDATE order_items SET returned_qty = returned_qty + ? WHERE id = ?", (l["qty"], l["item_id"]))
    conn.execute("UPDATE orders SET returned = returned + ? WHERE id = ?", (amount, order["id"]))
    reason = (data.get("reason") or "").strip() or None
    cur = conn.execute(
        "INSERT INTO order_returns (order_id, amount, items, reason, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (order["id"], amount, json.dumps(lines, ensure_ascii=False), reason, now(), user["id"]))
    if order["customer_id"]:
        notify_customer(conn, get_customer(conn, order["customer_id"]),
                        f"↩️ <b>Qaytarish</b> · Buyurtma #{order['id']}\n"
                        + "\n".join(f"• {html_escape(l['name'])} × {l['qty']}" for l in lines)
                        + f"\nQaytarilgan summa: <b>{fmt_money(amount)}</b>", "notify_sales")
    return {"id": cur.lastrowid, "amount": amount, "items": lines, "reason": reason, "order_id": order["id"]}


# --- savdolar (barcha cheklar)


def sale_filters(query):
    q = lambda k, d="": (query.get(k, [d])[0] or "").strip()
    today = datetime.now().date().isoformat()
    date_from, date_to = q("from") or today, q("to") or today
    for d in (date_from, date_to):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ApiError(400, "Sana formati: YYYY-MM-DD")
    where = ["o.status IN ('paid', 'refunded')", "o.closed_at BETWEEN ? AND ?"]
    args = [date_from + " 00:00:00", date_to + " 23:59:59"]
    status = q("status")
    if status == "paid":
        where.append("o.status = 'paid'")
    elif status == "refunded":
        where.append("o.status = 'refunded'")
    elif status == "returned":
        where.append("o.returned > 0")
    if q("method") in PAYMENT_METHODS:
        where.append("o.payment_method = ?")
        args.append(q("method"))
    if q("q"):
        text = q("q").lstrip("#")
        where.append("(CAST(o.id AS TEXT) = ? OR c.name LIKE ? OR c.phone LIKE ?)")
        args += [text, f"%{text}%", f"%{re.sub(r'[^0-9]', '', text) or text}%"]
    return date_from, date_to, " AND ".join(where), args


@route("GET", "/api/sales", ("reports", "finance"))
def list_sales(conn, user, params, data, query):
    date_from, date_to, where, args = sale_filters(query)
    sales = rows(conn.execute(
        f"""SELECT o.id, o.type, o.status, o.total, o.returned, o.discount, o.service, o.payment_method, o.closed_at,
                   t.name AS table_name, h.name AS hall_name, w.full_name AS waiter_name, k.full_name AS cashier_name,
                   c.name AS customer_name,
                   (SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = o.id) AS items
            FROM orders o
            LEFT JOIN tables t ON t.id = o.table_id LEFT JOIN halls h ON h.id = t.hall_id
            LEFT JOIN users w ON w.id = o.waiter_id LEFT JOIN users k ON k.id = o.cashier_id
            LEFT JOIN customers c ON c.id = o.customer_id
            WHERE {where} ORDER BY o.closed_at DESC, o.id DESC LIMIT 2000""", args))
    paid = [x for x in sales if x["status"] == "paid"]
    return {
        "from": date_from, "to": date_to, "sales": sales,
        "totals": {"count": len(paid), "revenue": sum(x["total"] - x["returned"] for x in paid),
                   "returned": sum(x["returned"] for x in paid),
                   "refunded": sum(x["total"] for x in sales if x["status"] == "refunded")},
    }


@route("GET", r"/api/sales/(\d+)", ("reports", "finance", "cashier"))
def sale_detail(conn, user, params, data, query):
    order = order_detail(conn, params[0])
    if order["status"] not in ("paid", "refunded"):
        raise ApiError(404, "Savdo topilmadi")
    order["items"] = rows(conn.execute(
        "SELECT id, name, price, qty, returned_qty FROM order_items WHERE order_id = ? AND qty > 0 ORDER BY id", (params[0],)))
    extra = one(conn, """SELECT k.full_name AS cashier_name, r.full_name AS refunded_by_name,
                                c.name AS customer_name, c.phone AS customer_phone
                         FROM orders o LEFT JOIN users k ON k.id = o.cashier_id LEFT JOIN users r ON r.id = o.refunded_by
                         LEFT JOIN customers c ON c.id = o.customer_id WHERE o.id = ?""", params[0])
    order.update(extra)
    order["returns"] = rows(conn.execute(
        """SELECT r.*, u.full_name AS user_name FROM order_returns r LEFT JOIN users u ON u.id = r.created_by
           WHERE r.order_id = ? ORDER BY r.id""", (params[0],)))
    for r in order["returns"]:
        r["items"] = json.loads(r["items"])
    order["due_date"] = one(conn, "SELECT due_date FROM debts WHERE order_id = ? ORDER BY id DESC", params[0]).get("due_date")
    order["can_manage"] = bool({"finance", "cashier"} & set(user["permissions"]))
    return order


@route("POST", r"/api/finance/sales/(\d+)/cancel", ("finance", "cashier"))
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
    order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (params[0],)).fetchone())
    if order["customer_id"]:
        notify_customer(conn, get_customer(conn, order["customer_id"]),
                        f"↩️ <b>Xarid bekor qilindi</b>\nBuyurtma #{order['id']} · {fmt_money(order['total'] - order['returned'])}",
                        "notify_sales")
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
            """SELECT e.id, e.direction, e.account, e.amount,
                      COALESCE(s.name || COALESCE(' · ' || e.comment, ''), e.comment) AS comment,
                      e.status, e.created_at, e.cancelled_at, e.cancel_reason, t.name AS type_name,
                      u.full_name AS user_name, cu.full_name AS cancelled_by_name
               FROM finance_entries e
               JOIN finance_types t ON t.id = e.type_id
               LEFT JOIN suppliers s ON s.id = e.supplier_id
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
    if source in ("all", "sales") and direction in ("", "out"):
        # Qisman qaytarishlar - pul kassadan chiqdi (savdo bekor qilinsa, savdo o'zi ham hisobdan chiqadi)
        entries += [dict(r, source="return", direction="out", type_name="Savdodan qaytarish") for r in conn.execute(
            """SELECT r.id, o.payment_method AS account, r.amount, r.created_at,
                      'Buyurtma #' || o.id || COALESCE(' · ' || r.reason, '') AS comment, u.full_name AS user_name,
                      CASE o.status WHEN 'paid' THEN 'done' ELSE 'cancelled' END AS status,
                      NULL AS cancelled_at, NULL AS cancel_reason, NULL AS cancelled_by_name
               FROM order_returns r JOIN orders o ON o.id = r.order_id
               LEFT JOIN users u ON u.id = r.created_by
               WHERE o.payment_method != 'debt' AND r.created_at BETWEEN ? AND ?""", rng)]
    if source in ("all", "debts") and direction in ("", "in"):
        entries += [dict(r, source="debt", direction="in", type_name="Qarz to'lovi") for r in conn.execute(
            """SELECT p.id, p.account, p.amount, p.created_at, p.status, p.cancelled_at, p.cancel_reason,
                      c.name || ' · ' || c.phone AS comment, u.full_name AS user_name,
                      cu.full_name AS cancelled_by_name, p.method
               FROM debt_payments p
               JOIN customers c ON c.id = p.customer_id
               LEFT JOIN users u ON u.id = p.created_by
               LEFT JOIN users cu ON cu.id = p.cancelled_by
               WHERE p.account != 'adjust' AND p.created_at BETWEEN ? AND ?""", rng)]
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


# --- ta'minotchilar va balansni o'rnatish


def get_supplier(conn, supplier_id):
    row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id or 0,)).fetchone()
    if not row:
        raise ApiError(404, "Ta'minotchi topilmadi")
    return dict(row)


def supplier_balances(conn):
    """Musbat balans - biz ta'minotchiga qarzdormiz."""
    return rows(conn.execute(
        """SELECT s.*,
                  COALESCE((SELECT SUM(new_balance - old_balance) FROM balance_adjustments a
                            WHERE a.target = 'supplier' AND a.target_id = s.id), 0)
                - COALESCE((SELECT SUM(amount) FROM finance_entries e
                            WHERE e.supplier_id = s.id AND e.status = 'done' AND e.direction = 'out'), 0) AS balance
           FROM suppliers s ORDER BY s.name"""
    ))


def customer_remaining(conn, customer_id):
    return sum(d["remaining"] for d in debts_query(conn, "AND d.customer_id = ?", (customer_id,)))


def record_adjustment(conn, user, target, target_id, account, old, new, comment):
    conn.execute(
        """INSERT INTO balance_adjustments (target, target_id, account, old_balance, new_balance, comment, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (target, target_id, account, old, new, comment, now(), user["id"]),
    )


def new_balance_value(data, minimum=None):
    try:
        value = int(float(str(data.get("balance", "")).replace(" ", "").replace(",", ".")))
    except ValueError:
        raise ApiError(400, "Yangi balansni son bilan kiriting")
    if minimum is not None and value < minimum:
        raise ApiError(400, "Balans manfiy bo'lishi mumkin emas")
    return value


@route("GET", "/api/suppliers", ("finance",))
def list_suppliers(conn, user, params, data, query):
    return supplier_balances(conn)


@route("POST", "/api/suppliers", ("finance",))
def create_supplier(conn, user, params, data, query):
    name = clean_name(data.get("name"))
    if len(name) < 2:
        raise ApiError(400, "Ta'minotchi nomini kiriting")
    if conn.execute("SELECT 1 FROM suppliers WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise ApiError(409, f"\"{name}\" nomli ta'minotchi allaqachon bor")
    phone = normalize_phone(data["phone"]) if (data.get("phone") or "").strip() else None
    if phone and conn.execute("SELECT 1 FROM suppliers WHERE phone = ?", (phone,)).fetchone():
        raise ApiError(409, f"{phone} raqamli ta'minotchi allaqachon bor")
    cur = conn.execute(
        "INSERT INTO suppliers (name, phone, created_at, created_by) VALUES (?, ?, ?, ?)",
        (name, phone, now(), user["id"]),
    )
    return get_supplier(conn, cur.lastrowid)


@route("GET", "/api/balances", ("finance",))
def get_balances(conn, user, params, data, query):
    customers = rows(conn.execute("SELECT id, name, phone FROM customers ORDER BY name"))
    for c in customers:
        c["balance"] = customer_remaining(conn, c["id"])
    history = rows(conn.execute(
        """SELECT a.*, u.full_name AS user_name,
                  CASE a.target WHEN 'customer' THEN (SELECT name FROM customers WHERE id = a.target_id)
                                WHEN 'supplier' THEN (SELECT name FROM suppliers WHERE id = a.target_id) END AS target_name
           FROM balance_adjustments a LEFT JOIN users u ON u.id = a.created_by
           ORDER BY a.id DESC LIMIT 100"""
    ))
    return {"accounts": finance_balance(conn), "customers": customers,
            "suppliers": supplier_balances(conn), "history": history}


@route("POST", "/api/balances/account", ("finance",))
def set_account_balance(conn, user, params, data, query):
    account = data.get("account")
    if account not in FINANCE_ACCOUNTS:
        raise ApiError(400, "Hisobni tanlang")
    new = new_balance_value(data)
    old = next(a["balance"] for a in finance_balance(conn)["accounts"] if a["account"] == account)
    if new == old:
        raise ApiError(400, "Balans o'zgarmadi")
    direction = "in" if new > old else "out"
    type_id = conn.execute("SELECT id FROM finance_types WHERE name = ?", (ADJUST_TYPES[direction],)).fetchone()[0]
    comment = (data.get("comment") or "").strip() or None
    conn.execute(
        """INSERT INTO finance_entries (type_id, direction, account, amount, comment, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (type_id, direction, account, abs(new - old), comment or "Balans o'rnatish", now(), user["id"]),
    )
    record_adjustment(conn, user, "account", None, account, old, new, comment)
    return {"old": old, "new": new}


@route("POST", "/api/balances/customer", ("finance",))
def set_customer_balance(conn, user, params, data, query):
    customer = get_customer(conn, data.get("customer_id"))
    new = new_balance_value(data, minimum=0)
    old = customer_remaining(conn, customer["id"])
    if new == old:
        raise ApiError(400, "Balans o'zgarmadi")
    comment = (data.get("comment") or "").strip() or None
    if new > old:  # qarz ko'paydi - yangi qarz yoziladi
        due = parse_due_date(data["due_date"]) if data.get("due_date") else (
            datetime.now().date() + timedelta(days=30)).isoformat()
        conn.execute(
            """INSERT INTO debts (customer_id, amount, due_date, comment, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (customer["id"], new - old, due, comment or "Balans o'rnatish", now(), user["id"]),
        )
    else:  # qarz kamaydi - eng eski qarzlardan boshlab tuzatiladi (kassaga pul tushmaydi)
        left = old - new
        for d in debts_query(conn, "AND d.customer_id = ? AND d.status = 'open'", (customer["id"],)):
            if left <= 0:
                break
            part = min(left, d["remaining"])
            if part <= 0:
                continue
            conn.execute(
                """INSERT INTO debt_payments (debt_id, customer_id, amount, method, account, created_at, created_by)
                   VALUES (?, ?, ?, 'adjust', 'adjust', ?, ?)""",
                (d["id"], customer["id"], part, now(), user["id"]),
            )
            if part == d["remaining"]:
                conn.execute("UPDATE debts SET status = 'closed' WHERE id = ?", (d["id"],))
            left -= part
    record_adjustment(conn, user, "customer", customer["id"], None, old, new, comment)
    return {"old": old, "new": new}


@route("POST", "/api/balances/supplier", ("finance",))
def set_supplier_balance(conn, user, params, data, query):
    supplier = get_supplier(conn, data.get("supplier_id"))
    new = new_balance_value(data)
    old = next(s["balance"] for s in supplier_balances(conn) if s["id"] == supplier["id"])
    if new == old:
        raise ApiError(400, "Balans o'zgarmadi")
    record_adjustment(conn, user, "supplier", supplier["id"], None, old, new, (data.get("comment") or "").strip() or None)
    return {"old": old, "new": new}


# --- import (bir nechta mahsulot / mijozni fayl orqali qo'shish)

IMPORT_TEMPLATES = {
    "products": {
        "perm": "menu",
        "file": "CafePOS_mahsulotlar_shablon.xlsx",
        "headers": ["Nomi*", "Kategoriya", "Sotish narxi*", "Tannarxi", "Printer"],
        "widths": [28, 22, 16, 14, 16],
        "rows": [["Osh", "Taomlar", 35000, 18000, "Oshxona"], ["Choy", "Issiq ichimliklar", 5000, 1000, ""]],
        "notes": ["# * - majburiy ustun. Namuna qatorlarni o'chirib, o'z mahsulotlaringizni yozing.",
                  "# Yangi kategoriya avtomatik yaratiladi. Printer nomi dasturdagi bilan bir xil bo'lsin.",
                  "# Shu nomli mahsulot bor bo'lsa - narxi va kategoriyasi yangilanadi."],
        "fields": {"name": ("nomi", "name", "mahsulot", "taom"), "category": ("kategoriya", "category"),
                   "price": ("sotish narxi", "narxi", "narx", "price"), "cost": ("tannarxi", "tannarx", "cost"),
                   "printer": ("printer",)},
    },
    "customers": {
        "perm": "crm",
        "file": "CafePOS_mijozlar_shablon.xlsx",
        "headers": ["Ismi*", "Telefon*", "Jinsi*"],
        "widths": [28, 22, 12],
        "rows": [["Ali Valiyev", "+998 90 123 45 67", "Erkak"], ["Zarina Karimova", "93 555 66 77", "Ayol"]],
        "notes": ["# * - majburiy ustun. Jinsi: Erkak yoki Ayol.",
                  "# Shu telefon raqamli mijoz bor bo'lsa - ismi va jinsi yangilanadi."],
        "fields": {"name": ("ismi", "ism", "name", "mijoz"), "phone": ("telefon", "telefon raqami", "phone"),
                   "gender": ("jinsi", "jins", "gender")},
    },
}


def import_spec(kind, user):
    spec = IMPORT_TEMPLATES.get(kind)
    if not spec:
        raise ApiError(404, "Topilmadi")
    if spec["perm"] not in user["permissions"]:
        raise ApiError(403, "Bu bo'limga ruxsatingiz yo'q")
    return spec


@route("GET", r"/api/import/(products|customers)/template")
def import_template(conn, user, params, data, query):
    spec = import_spec(params[0], user)
    content = xlsx.write_xlsx(spec["headers"], spec["rows"], spec["widths"], notes=spec["notes"])
    return FileResponse(content, spec["file"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def parse_money(value, field, required):
    text = re.sub(r"[\s'`]|so.?m|sum", "", str(value or "").lower()).replace(",", ".")
    if not text:
        if required:
            raise ApiError(400, f"{field} yozilmagan")
        return 0
    try:
        n = int(round(float(text)))
    except ValueError:
        raise ApiError(400, f"{field} son emas: {value}")
    if n < 0:
        raise ApiError(400, f"{field} manfiy bo'lishi mumkin emas")
    return n


def parse_gender(value):
    v = (value or "").strip().lower()
    if v[:1] in ("e", "m") or v.startswith(("муж", "male")):
        return "m"
    if v[:1] in ("a", "f", "w", "ж") or v.startswith("жен"):
        return "f"
    raise ApiError(400, f"Jinsi noto'g'ri: \"{value}\" (Erkak yoki Ayol yozing)")


def import_product(conn, user, rec, categories, printers):
    name = clean_name(rec.get("name"))
    if not name:
        raise ApiError(400, "Nomi yozilmagan")
    price = parse_money(rec.get("price"), "Sotish narxi", True)
    cost = parse_money(rec.get("cost"), "Tannarxi", False)
    cat_id = None
    cat_name = clean_name(rec.get("category"))
    if cat_name:
        cat_id = categories.get(cat_name.lower())
        if not cat_id:
            cat_id = conn.execute("INSERT INTO categories (name, sort) VALUES (?, ?)", (cat_name, len(categories))).lastrowid
            categories[cat_name.lower()] = cat_id
    printer_id = None
    printer_name = clean_name(rec.get("printer"))
    if printer_name:
        printer_id = printers.get(printer_name.lower())
        if not printer_id:
            raise ApiError(400, f"\"{printer_name}\" printeri topilmadi (Sozlamalar > Printerlar)")
    existing = conn.execute(
        "SELECT id FROM products WHERE name = ? COLLATE NOCASE AND active = 1", (name,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE products SET category_id = COALESCE(?, category_id), price = ?, cost = ?, "
            "printer_id = COALESCE(?, printer_id) WHERE id = ?",
            (cat_id, price, cost, printer_id, existing["id"]),
        )
        return "updated"
    conn.execute(
        "INSERT INTO products (category_id, name, price, cost, printer_id) VALUES (?,?,?,?,?)",
        (cat_id, name, price, cost, printer_id),
    )
    return "created"


def import_customer(conn, user, rec):
    name, phone, gender = clean_name(rec.get("name")), rec.get("phone"), parse_gender(rec.get("gender"))
    if len(name) < 2:
        raise ApiError(400, "Ismi yozilmagan")
    phone = normalize_phone(phone)
    existing = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
    if existing:
        conn.execute("UPDATE customers SET name = ?, gender = ? WHERE id = ?", (name, gender, existing["id"]))
        return "updated"
    conn.execute(
        "INSERT INTO customers (name, phone, gender, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
        (name, phone, gender, now(), user["id"]),
    )
    return "created"


@route("POST", r"/api/import/(products|customers)")
def import_file(conn, user, params, data, query):
    kind = params[0]
    spec = import_spec(kind, user)
    try:
        content = base64.b64decode(str(data.get("data", "")).split(",")[-1])
        headers, table = xlsx.read_table(content, data.get("file_name") or "")
    except (ValueError, xlsx.TableError, ElementTree.ParseError) as e:
        raise ApiError(400, f"Faylni o'qib bo'lmadi: {e}")
    columns = {}
    for field, aliases in spec["fields"].items():
        for i, h in enumerate(headers):
            if h in aliases:
                columns[field] = i
                break
    required = [f for f, a in spec["fields"].items() if spec["headers"][list(spec["fields"]).index(f)].endswith("*")]
    missing = [spec["headers"][list(spec["fields"]).index(f)].rstrip("*") for f in required if f not in columns]
    if missing:
        raise ApiError(400, "Faylda ustun topilmadi: " + ", ".join(missing) + ". Shablondan foydalaning")
    if not table:
        raise ApiError(400, "Faylda ma'lumot qatori yo'q")
    if len(table) > 5000:
        raise ApiError(400, "Bir martada 5000 qatordan ko'p bo'lmasin")

    categories = {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    printers = {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM printers WHERE active = 1")}
    result = {"created": 0, "updated": 0, "errors": []}
    for row_no, values in table:
        rec = {f: values[i] if i < len(values) else "" for f, i in columns.items()}
        conn.execute("SAVEPOINT import_row")
        try:
            status = (import_product(conn, user, rec, categories, printers) if kind == "products"
                      else import_customer(conn, user, rec))
            conn.execute("RELEASE import_row")
            result[status] += 1
        except ApiError as e:
            conn.execute("ROLLBACK TO import_row")
            conn.execute("RELEASE import_row")
            result["errors"].append({"row": row_no, "message": e.message})
    return result


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
        f"""SELECT {extra or "COUNT(*) AS orders, COALESCE(SUM(total - returned), 0) AS revenue"}
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
        f"""SELECT COALESCE(SUM(i.qty - i.returned_qty), 0) FROM order_items i JOIN orders o ON o.id = i.order_id
            WHERE {where}""", rng,
    ).fetchone()[0]
    summary["cancelled"] = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'cancelled' AND closed_at BETWEEN ? AND ?", rng
    ).fetchone()[0]
    summary["open"] = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'open'").fetchone()[0]

    by_method = {m: 0 for m in PAYMENT_METHODS}
    for r in conn.execute(
        f"SELECT payment_method, SUM(total - returned) FROM orders o WHERE {where} GROUP BY payment_method", rng
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
        f"SELECT {bucket} AS k, SUM(total - returned) FROM orders o WHERE {where} GROUP BY k", rng
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
            f"""SELECT COUNT(*) AS orders, COALESCE(SUM(total - returned), 0) AS revenue,
                       COALESCE(SUM(discount), 0) AS discount, COALESCE(SUM(service), 0) AS service
                FROM orders o WHERE {where}""",
            rng,
        ).fetchone()
    )
    summary["average"] = summary["revenue"] // summary["orders"] if summary["orders"] else 0
    summary["cost"] = conn.execute(
        f"""SELECT COALESCE(SUM(i.cost * (i.qty - i.returned_qty)), 0) FROM order_items i
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
                f"""SELECT payment_method AS method, COUNT(*) AS orders, SUM(total - returned) AS revenue
                    FROM orders o WHERE {where} GROUP BY payment_method ORDER BY revenue DESC""",
                rng,
            )
        ),
        "top_products": rows(
            conn.execute(
                f"""SELECT i.name, SUM(i.qty - i.returned_qty) AS qty, SUM((i.qty - i.returned_qty) * i.price) AS revenue,
                           SUM((i.qty - i.returned_qty) * (i.price - i.cost)) AS profit
                    FROM order_items i JOIN orders o ON o.id = i.order_id
                    WHERE {where} AND i.qty > 0 GROUP BY i.name ORDER BY qty DESC LIMIT 10""",
                rng,
            )
        ),
        "by_waiter": rows(
            conn.execute(
                f"""SELECT COALESCE(u.full_name, '-') AS name, COUNT(*) AS orders, SUM(o.total - o.returned) AS revenue
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
            """SELECT id, username, full_name, first_name, last_name, phone, role, permissions, active,
                      pin_lookup IS NOT NULL AS has_pin
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
    digits = re.sub(r"\D", "", phone or "")
    if digits:
        for other in conn.execute("SELECT id, phone FROM users WHERE active = 1 AND phone IS NOT NULL"):
            if re.sub(r"\D", "", other["phone"])[-9:] == digits[-9:] and (not target or other["id"] != target["id"]):
                raise ApiError(409, f"{phone} raqamli xodim allaqachon bor")
    return first, last, f"{first} {last}".strip(), phone, data["role"], perms


def check_password(password):
    if len(password or "") < 4:
        raise ApiError(400, "Parol kamida 4 belgi bo'lishi kerak")


def set_pin(conn, user_id, pin):
    pin = str(pin or "").strip()
    if not re.fullmatch(rf"\d{{{PIN_LENGTH}}}", pin):
        raise ApiError(400, f"PIN kod {PIN_LENGTH} ta raqam bo'lishi kerak")
    lookup = pin_hash(conn, pin)
    if conn.execute("SELECT 1 FROM users WHERE pin_lookup = ? AND id != ?", (lookup, user_id)).fetchone():
        raise ApiError(409, "Bu PIN kod boshqa xodimda bor - boshqasini tanlang")
    conn.execute("UPDATE users SET pin_lookup = ? WHERE id = ?", (lookup, user_id))


@route("POST", "/api/users", ("users",))
def create_user(conn, user, params, data, query):
    if not data.get("password") and not data.get("pin"):
        raise ApiError(400, "Parol (4 ta raqam) kiriting")
    if data.get("password"):
        check_password(data["password"])
    username = (data.get("username") or "").strip()
    if username:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ApiError(409, "Bu username band")
    else:  # xodim telefon raqami va paroli bilan qo'shiladi - username avtomatik
        base = re.sub(r"\D", "", data.get("phone") or "") or "xodim"
        username, n = base, 1
        while conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            n += 1
            username = f"{base}_{n}"
    # parol berilmasa - tasodifiy (xodim PIN bilan kiradi)
    pw, salt = hash_password(data.get("password") or secrets.token_urlsafe(16))
    cur = conn.execute(
        """INSERT INTO users (first_name, last_name, full_name, phone, role, permissions,
                              username, password_hash, salt) VALUES (?,?,?,?,?,?,?,?,?)""",
        (*user_values(conn, user, data), username, pw, salt),
    )
    if data.get("pin"):
        set_pin(conn, cur.lastrowid, data["pin"])
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
    if data.get("pin"):
        set_pin(conn, target_id, data["pin"])
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


# ---------------------------------------------------------------- jurnal (barcha amallar tarixi)
# Har bir o'zgartiruvchi so'rovdan keyin yoziladi: kim, qachon, nima qildi va batafsil ma'lumot.
# Yozuvlar o'chirilmaydi va o'zgartirilmaydi.

JOURNAL_CATEGORIES = {
    "sales": "Sotuv",
    "orders": "Buyurtmalar",
    "finance": "Moliya",
    "crm": "CRM",
    "menu": "Mahsulotlar",
    "users": "Xodimlar",
    "settings": "Sozlamalar",
    "auth": "Kirish",
}
# Telegramga standart yuboriladiganlar (buyurtmaga taom qo'shish kabi mayda amallarsiz)
TELEGRAM_DEFAULT_CATEGORIES = ("sales", "finance", "crm", "menu", "users", "settings")
ACCOUNT_NAMES = {"cash": "Naqd", "card": "Karta", "payme": "Payme", "click": "Click", "bank": "Hisob raqam",
                 "debt": "Qarzga", "adjust": "Tuzatish"}
DEBT_METHOD_NAMES = {"cash": "Naqd", "click": "Click", "terminal": "Terminal", "transfer": "Pul ko'chirish"}
SECRET_FIELDS = {"password", "password_hash", "salt", "token", "image", "data", "logo", "pin", "pin_lookup"}


def fmt_money(n):
    return f"{int(n or 0):,} so'm".replace(",", " ")


def one(conn, sql, *args):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else {}


def order_place(o):
    if not o:
        return ""
    if o.get("type") == "takeaway":
        return "Olib ketish"
    name = o.get("table_name") or "Stol"
    return f"{name} ({o['hall_name']})" if o.get("hall_name") else name


def order_row(conn, order_id):
    return one(conn, """SELECT o.*, t.name AS table_name, h.name AS hall_name, w.full_name AS waiter_name
                        FROM orders o LEFT JOIN tables t ON t.id = o.table_id
                        LEFT JOIN halls h ON h.id = t.hall_id LEFT JOIN users w ON w.id = o.waiter_id
                        WHERE o.id = ?""", order_id)


def change(label, old, new, fmt=str):
    """Tahrirlashda: "Narxi: 30 000 → 35 000" (o'zgarmagan bo'lsa - None)."""
    if old == new or new is None:
        return None
    return [label, f"{fmt(old) if old not in (None, '') else '—'} → {fmt(new)}"]


def entry(title, summary="", fields=(), items=None, entity=None):
    return {"title": title, "summary": summary, "fields": [f for f in fields if f and f[1] not in (None, "")],
            "items": items, "entity": entity}


# "before" - amaldan OLDINGI holat (o'chirish/tahrirlashda eski nomni ko'rsatish uchun)
AUDIT_BEFORE = {
    "update_category": lambda c, p, d: one(c, "SELECT * FROM categories WHERE id = ?", p[0]),
    "delete_category": lambda c, p, d: one(c, "SELECT * FROM categories WHERE id = ?", p[0]),
    "update_product": lambda c, p, d: one(c, """SELECT p.*, c.name AS category_name FROM products p
                                               LEFT JOIN categories c ON c.id = p.category_id WHERE p.id = ?""", p[0]),
    "delete_product": lambda c, p, d: one(c, "SELECT * FROM products WHERE id = ?", p[0]),
    "save_settings": lambda c, p, d: get_settings(c),
    "update_hall": lambda c, p, d: one(c, "SELECT * FROM halls WHERE id = ?", p[0]),
    "delete_hall": lambda c, p, d: one(c, "SELECT * FROM halls WHERE id = ?", p[0]),
    "update_table": lambda c, p, d: one(c, "SELECT * FROM tables WHERE id = ?", p[0]),
    "delete_table": lambda c, p, d: one(c, "SELECT * FROM tables WHERE id = ?", p[0]),
    "update_printer": lambda c, p, d: one(c, "SELECT * FROM printers WHERE id = ?", p[0]),
    "delete_printer": lambda c, p, d: one(c, "SELECT * FROM printers WHERE id = ?", p[0]),
    "update_item": lambda c, p, d: one(c, "SELECT * FROM order_items WHERE id = ?", p[1]),
    "cancel_order": lambda c, p, d: order_row(c, p[0]),
    "discard_order": lambda c, p, d: order_row(c, p[0]),
    "cancel_sale": lambda c, p, d: order_row(c, p[0]),
    "update_customer": lambda c, p, d: one(c, "SELECT * FROM customers WHERE id = ?", p[0]),
    "pay_debt": lambda c, p, d: one(c, """SELECT d.*, c.name AS customer_name, c.phone FROM debts d
                                         JOIN customers c ON c.id = d.customer_id WHERE d.id = ?""", p[0]),
    "cancel_debt_payment": lambda c, p, d: one(c, """SELECT p.*, c.name AS customer_name FROM debt_payments p
                                                    JOIN customers c ON c.id = p.customer_id WHERE p.id = ?""", p[0]),
    "cancel_finance_entry": lambda c, p, d: one(c, """SELECT e.*, t.name AS type_name FROM finance_entries e
                                                     JOIN finance_types t ON t.id = e.type_id WHERE e.id = ?""", p[0]),
    "update_user": lambda c, p, d: one(c, "SELECT id, username, full_name, role, phone, active FROM users WHERE id = ?", p[0]),
    "delete_user": lambda c, p, d: one(c, "SELECT id, username, full_name, role FROM users WHERE id = ?", p[0]),
    "kitchen_ready": lambda c, p, d: one(c, "SELECT * FROM kitchen_tickets WHERE id = ?", p[0]),
}


def _a_product(ctx):
    d, r = ctx.data, ctx.result
    cat = one(ctx.conn, "SELECT name FROM categories WHERE id = ?", d.get("category_id")).get("name")
    printer = one(ctx.conn, "SELECT name FROM printers WHERE id = ?", d.get("printer_id")).get("name")
    return entry("Mahsulot qo'shildi", f"{d.get('name', '').strip()} · {fmt_money(d.get('price'))}", [
        ["Nomi", d.get("name", "").strip()], ["Kategoriya", cat], ["Sotish narxi", fmt_money(d.get("price"))],
        ["Tannarxi", fmt_money(d.get("cost") or 0)], ["Printer", printer], ["Rasm", "bor" if d.get("image") else None],
    ], entity=f"product:{r.get('id')}")


def _a_product_update(ctx):
    b, d = ctx.before, ctx.data
    cat = one(ctx.conn, "SELECT name FROM categories WHERE id = ?", d.get("category_id")).get("name")
    fields = [
        change("Nomi", b.get("name"), (d.get("name") or "").strip()),
        change("Sotish narxi", b.get("price"), int(d.get("price") or 0), fmt_money),
        change("Tannarxi", b.get("cost"), int(d.get("cost") or 0), fmt_money),
        change("Kategoriya", b.get("category_name"), cat),
        ["Rasm", "yangilandi" if d.get("image") else "olib tashlandi" if d.get("remove_image") else None],
    ]
    changed = [f for f in fields if f and f[1]]
    return entry("Mahsulot o'zgartirildi", (d.get("name") or "").strip() + (
        " · " + ", ".join(f"{f[0].lower()}: {f[1]}" for f in changed) if changed else ""), changed,
        entity=f"product:{ctx.params[0]}")


def _a_pay(ctx):
    o = ctx.result
    fields = [["Buyurtma", f"#{o['id']}"], ["Joy", order_place(o)], ["Ofitsiant", o.get("waiter_name")],
              ["Taomlar summasi", fmt_money(o["subtotal"])],
              ["Xizmat haqi", f"{fmt_money(o['service'])} ({o['service_percent']:g}%)" if o.get("service") else None],
              ["Chegirma", fmt_money(o["discount"]) if o.get("discount") else None],
              ["Jami", fmt_money(o["total"])], ["To'lov usuli", ACCOUNT_NAMES.get(o["payment_method"])]]
    if o.get("customer_id"):
        c = one(ctx.conn, "SELECT name, phone FROM customers WHERE id = ?", o["customer_id"])
        fields.append(["Mijoz", f"{c.get('name', '')} {c.get('phone', '')}".strip()])
        if o.get("customer_notified"):
            fields.append(["Chek", "mijozning Telegram'iga yuborildi"])
    if o["payment_method"] == "debt":
        debt = one(ctx.conn, "SELECT due_date FROM debts WHERE order_id = ? ORDER BY id DESC", o["id"])
        fields.append(["To'lov muddati", debt.get("due_date")])
    items = [{"name": i["name"], "qty": i["qty"], "price": i["price"]} for i in o["items"]]
    return entry("Sotuv", f"Buyurtma #{o['id']} · {order_place(o)} · {fmt_money(o['total'])} · "
                 f"{ACCOUNT_NAMES.get(o['payment_method'], o['payment_method'])}", fields, items, f"order:{o['id']}")


def _a_add_item(ctx):
    o = ctx.result
    p = one(ctx.conn, "SELECT name, price FROM products WHERE id = ?", ctx.data.get("product_id"))
    qty = int(ctx.data.get("qty") or 1)
    return entry("Buyurtmaga taom qo'shildi", f"{p.get('name')} × {qty} · Buyurtma #{o['id']} · {order_place(o)}", [
        ["Taom", p.get("name")], ["Soni", qty], ["Narxi", fmt_money(p.get("price"))],
        ["Buyurtma", f"#{o['id']}"], ["Joy", order_place(o)], ["Buyurtma summasi", fmt_money(o["total"])],
    ], entity=f"order:{o['id']}")


def _a_update_item(ctx):
    b, o, qty = ctx.before, ctx.result, int(ctx.data.get("qty") or 0)
    title = "Buyurtmadan taom olib tashlandi" if qty == 0 else "Taom soni o'zgartirildi"
    return entry(title, f"{b.get('name')}: {b.get('qty')} → {qty} · Buyurtma #{o['id']} · {order_place(o)}", [
        ["Taom", b.get("name")], ["Soni", f"{b.get('qty')} → {qty}"], ["Buyurtma", f"#{o['id']}"],
        ["Joy", order_place(o)], ["Oshxonaga yuborilgan", "ha" if (b.get("printed_qty") or b.get("kds_qty")) else "yo'q"],
    ], entity=f"order:{o['id']}")


def _a_cancel_order(ctx, title="Buyurtma bekor qilindi"):
    b = ctx.before
    items = rows(ctx.conn.execute("SELECT name, qty, price FROM order_items WHERE order_id = ? AND qty > 0", (b.get("id"),)))
    total = sum(i["qty"] * i["price"] for i in items)
    return entry(title, f"Buyurtma #{b.get('id')} · {order_place(b)} · {fmt_money(total)}", [
        ["Buyurtma", f"#{b.get('id')}"], ["Joy", order_place(b)], ["Ofitsiant", b.get("waiter_name")],
        ["Summa", fmt_money(total)], ["Ochilgan", b.get("created_at")],
    ], items, f"order:{b.get('id')}")


def _a_cancel_sale(ctx):
    b = ctx.before
    items = rows(ctx.conn.execute("SELECT name, qty, price FROM order_items WHERE order_id = ? AND qty > 0", (b.get("id"),)))
    return entry("Savdo bekor qilindi (pul qaytarildi)",
                 f"Buyurtma #{b.get('id')} · {fmt_money(b.get('total'))} · {ACCOUNT_NAMES.get(b.get('payment_method'), '')}", [
        ["Buyurtma", f"#{b.get('id')}"], ["Joy", order_place(b)], ["Summa", fmt_money(b.get("total"))],
        ["To'lov usuli", ACCOUNT_NAMES.get(b.get("payment_method"))], ["Sotilgan vaqti", b.get("closed_at")],
        ["Sabab", (ctx.data.get("reason") or "").strip()],
    ], items, f"order:{b.get('id')}")


def _a_kitchen_print(ctx):
    o = ctx.result
    if not o.get("printed"):
        return None
    return entry("Oshxonaga chop etildi", f"Buyurtma #{o['id']} · {order_place(o)} · {', '.join(o['printed'])}", [
        ["Buyurtma", f"#{o['id']}"], ["Joy", order_place(o)], ["Printer", ", ".join(o["printed"])],
        ["Xatolar", "; ".join(o.get("errors") or [])],
    ], entity=f"order:{o['id']}")


def _a_kitchen_ready(ctx):
    t = ctx.before
    o = order_row(ctx.conn, t.get("order_id"))
    lines = json.loads(t.get("lines") or "[]")
    return entry("Oshxona: taom tayyor", f"Buyurtma #{o.get('id')} · {order_place(o)}", [
        ["Buyurtma", f"#{o.get('id')}"], ["Joy", order_place(o)]],
        [{"name": l["name"], "qty": l["qty"]} for l in lines], f"order:{o.get('id')}")


def _a_finance_entry(ctx):
    d = ctx.data
    t = one(ctx.conn, "SELECT name, direction FROM finance_types WHERE id = ?", d.get("type_id"))
    supplier = one(ctx.conn, "SELECT name FROM suppliers WHERE id = ?", d.get("supplier_id")).get("name")
    kind = "Kirim" if t.get("direction") == "in" else "Chiqim"
    return entry(f"{kind}: {t.get('name')}", f"{fmt_money(d.get('amount'))} · {ACCOUNT_NAMES.get(d.get('account'))}"
                 + (f" · {supplier}" if supplier else ""), [
        ["Turi", kind], ["Tranzaksiya", t.get("name")], ["Summa", fmt_money(d.get("amount"))],
        ["Hisob", ACCOUNT_NAMES.get(d.get("account"))], ["Ta'minotchi", supplier], ["Izoh", (d.get("comment") or "").strip()],
    ], entity=f"finance:{ctx.result.get('id')}")


def _a_cancel_finance(ctx):
    b = ctx.before
    kind = "Kirim" if b.get("direction") == "in" else "Chiqim"
    return entry("Tranzaksiya bekor qilindi", f"{kind}: {b.get('type_name')} · {fmt_money(b.get('amount'))}", [
        ["Tranzaksiya", f"{kind}: {b.get('type_name')}"], ["Summa", fmt_money(b.get("amount"))],
        ["Hisob", ACCOUNT_NAMES.get(b.get("account"))], ["Yaratilgan", b.get("created_at")],
        ["Sabab", (ctx.data.get("reason") or "").strip()],
    ], entity=f"finance:{b.get('id')}")


def _a_customer(ctx, title):
    c = ctx.result
    fields = [["Ismi", c.get("name")], ["Telefon", c.get("phone")], ["Jinsi", {"m": "Erkak", "f": "Ayol"}.get(c.get("gender"))]]
    if ctx.before:
        fields = [change("Ismi", ctx.before.get("name"), c.get("name")), change("Telefon", ctx.before.get("phone"), c.get("phone")),
                  change("Jinsi", {"m": "Erkak", "f": "Ayol"}.get(ctx.before.get("gender")),
                         {"m": "Erkak", "f": "Ayol"}.get(c.get("gender")))]
    return entry(title, f"{c.get('name')} · {c.get('phone')}", fields, entity=f"customer:{c.get('id')}")


def _a_add_debt(ctx):
    c = one(ctx.conn, "SELECT * FROM customers WHERE id = ?", ctx.params[0])
    d = ctx.data
    return entry("Mijozga qarz yozildi", f"{c.get('name')} · {fmt_money(d.get('amount'))} · muddati {d.get('due_date')}", [
        ["Mijoz", f"{c.get('name')} {c.get('phone')}"], ["Summa", fmt_money(d.get("amount"))],
        ["To'lov muddati", d.get("due_date")], ["Izoh", (d.get("comment") or "").strip()],
    ], entity=f"customer:{c.get('id')}")


def _a_pay_debt(ctx):
    b, r = ctx.before, ctx.result
    amount = one(ctx.conn, "SELECT amount FROM debt_payments WHERE debt_id = ? ORDER BY id DESC", b["id"]).get("amount")
    return entry("Qarz to'landi (kirim)", f"{b.get('customer_name')} · {fmt_money(amount)} · "
                 f"{DEBT_METHOD_NAMES.get(ctx.data.get('method'))}", [
        ["Mijoz", f"{b.get('customer_name')} {b.get('phone')}"], ["To'landi", fmt_money(amount)],
        ["To'lov usuli", DEBT_METHOD_NAMES.get(ctx.data.get("method"))], ["Hisobga tushdi", ACCOUNT_NAMES.get(r.get("account"))],
        ["Qarz qoldig'i", fmt_money(r.get("remaining"))],
    ], entity=f"customer:{b.get('customer_id')}")


def _a_cancel_debt_payment(ctx):
    b = ctx.before
    return entry("Qarz to'lovi bekor qilindi", f"{b.get('customer_name')} · {fmt_money(b.get('amount'))}", [
        ["Mijoz", b.get("customer_name")], ["Summa", fmt_money(b.get("amount"))],
        ["To'lov usuli", DEBT_METHOD_NAMES.get(b.get("method"))], ["To'langan vaqti", b.get("created_at")],
        ["Sabab", (ctx.data.get("reason") or "").strip()],
    ], entity=f"customer:{b.get('customer_id')}")


def _a_balance(ctx, what):
    r, d = ctx.result, ctx.data
    if what == "account":
        name, title = ACCOUNT_NAMES.get(d.get("account")), "Kassa balansi o'rnatildi"
    elif what == "customer":
        name = one(ctx.conn, "SELECT name FROM customers WHERE id = ?", d.get("customer_id")).get("name")
        title = "Mijoz balansi (qarzi) o'rnatildi"
    else:
        name = one(ctx.conn, "SELECT name FROM suppliers WHERE id = ?", d.get("supplier_id")).get("name")
        title = "Ta'minotchi balansi o'rnatildi"
    return entry(title, f"{name}: {fmt_money(r['old'])} → {fmt_money(r['new'])}", [
        ["Kimga", name], ["Eski balans", fmt_money(r["old"])], ["Yangi balans", fmt_money(r["new"])],
        ["Farq", ("+" if r["new"] > r["old"] else "−") + fmt_money(abs(r["new"] - r["old"]))],
        ["Izoh", (d.get("comment") or "").strip()],
    ])


def _a_import(ctx):
    r = ctx.result
    what = "Mahsulotlar" if ctx.params[0] == "products" else "Mijozlar"
    return dict(category="menu" if ctx.params[0] == "products" else "crm", **entry(f"{what} fayldan import qilindi",
                 f"{r['created']} ta yangi, {r['updated']} ta yangilandi" + (f", {len(r['errors'])} ta xato" if r["errors"] else ""), [
        ["Fayl", ctx.data.get("file_name")], ["Yangi qo'shildi", r["created"]], ["Yangilandi", r["updated"]],
        ["Xatolar", "; ".join(f"{e['row']}-qator: {e['message']}" for e in r["errors"][:20])],
    ]))


def _a_user(ctx, title):
    d = ctx.data
    name = f"{(d.get('first_name') or '').strip()} {(d.get('last_name') or '').strip()}".strip() or d.get("full_name")
    fields = [["Xodim", name], ["Login", d.get("username") or ctx.before.get("username")],
              ["Lavozim", {"admin": "Administrator", "cashier": "Kassir", "waiter": "Ofitsiant", "cook": "Oshpaz"}.get(d.get("role"))],
              ["Telefon", d.get("phone")]]
    if isinstance(d.get("permissions"), list) and d.get("role") != "admin":
        fields.append(["Ruxsatlar", ", ".join(PERMISSION_NAMES.get(p, p) for p in d["permissions"]) or "yo'q"])
    if ctx.before and d.get("password"):
        fields.append(["Parol", "o'zgartirildi"])
    if ctx.before and d.get("pin"):
        fields.append(["PIN kod", "o'zgartirildi"])
    if ctx.before and d.get("active") is False:
        fields.append(["Holati", "bloklandi"])
    return entry(title, f"{name} ({fields[1][1]})", fields, entity=f"user:{ctx.params[0] if ctx.params else ctx.result.get('id')}")


def _a_named(title, table_label, source="data"):
    """Oddiy nomli ob'ektlar: kategoriya, zal, stol, printer."""
    def build(ctx):
        if source == "before":
            name = ctx.before.get("name")
            return entry(title, name, [[table_label, name]])
        new = (ctx.data.get("name") or "").strip()
        old = ctx.before.get("name") if ctx.before else None
        fields = [[table_label, new]]
        if old is not None and old != new:
            fields = [[table_label, f"{old} → {new}"]]
        for key, label in (("service_percent", "Xizmat haqi, %"), ("seats", "O'rinlar"), ("address", "Manzil"),
                           ("kind", "Turi"), ("width", "Qog'oz kengligi")):
            if ctx.data.get(key) not in (None, ""):
                fields.append([label, ctx.data[key]])
        return entry(title, new if old in (None, new) else f"{old} → {new}", fields)
    return build


def _a_settings(ctx):
    b, s = ctx.before, ctx.result
    fields = [change("Kafe nomi", b.get("cafe_name"), s.get("cafe_name")),
              change("Xizmat haqi", b.get("service_percent"), s.get("service_percent"), lambda v: f"{v:g}%")]
    fields = [f for f in fields if f]
    if b.get("receipt") != s.get("receipt"):
        fields.append(["Chek", "ko'rinishi o'zgartirildi"])
    if not fields:
        return None
    return entry("Sozlamalar o'zgartirildi", ", ".join(f"{f[0]}: {f[1]}" for f in fields), fields)


PERMISSION_NAMES = {
    "tables": "Stollar", "cashier": "Kassa", "kitchen": "Oshxona", "reports": "Hisobot", "menu": "Menyu",
    "crm": "CRM", "finance": "Moliya", "halls": "Zallar", "printers": "Printerlar", "users": "Xodimlar",
    "settings": "Sozlamalar", "journal": "Jurnal", "integrations": "Integratsiyalar",
}

# handler nomi -> (kategoriya, yozuv yaratuvchi). None qaytarsa - jurnalga yozilmaydi.
AUDIT = {
    "create_category": ("menu", _a_named("Kategoriya qo'shildi", "Kategoriya")),
    "update_category": ("menu", _a_named("Kategoriya o'zgartirildi", "Kategoriya")),
    "delete_category": ("menu", _a_named("Kategoriya o'chirildi", "Kategoriya", "before")),
    "create_product": ("menu", _a_product),
    "update_product": ("menu", _a_product_update),
    "delete_product": ("menu", _a_named("Mahsulot o'chirildi", "Mahsulot", "before")),
    "save_settings": ("settings", _a_settings),
    "create_hall": ("settings", _a_named("Zal qo'shildi", "Zal")),
    "update_hall": ("settings", _a_named("Zal o'zgartirildi", "Zal")),
    "delete_hall": ("settings", _a_named("Zal o'chirildi", "Zal", "before")),
    "create_table": ("settings", _a_named("Stol qo'shildi", "Stol")),
    "update_table": ("settings", _a_named("Stol o'zgartirildi", "Stol")),
    "delete_table": ("settings", _a_named("Stol o'chirildi", "Stol", "before")),
    "create_printer": ("settings", _a_named("Printer qo'shildi", "Printer")),
    "update_printer": ("settings", _a_named("Printer o'zgartirildi", "Printer")),
    "delete_printer": ("settings", _a_named("Printer o'chirildi", "Printer", "before")),
    "add_item": ("orders", _a_add_item),
    "update_item": ("orders", _a_update_item),
    "discard_order": ("orders", lambda ctx: None if ctx.result.get("deleted") else _a_cancel_order(ctx)),
    "kitchen_print": ("orders", _a_kitchen_print),
    "kitchen_send": ("orders", lambda ctx: entry(
        "Oshxona ekraniga yuborildi", f"Buyurtma #{ctx.result['id']} · {order_place(ctx.result)}",
        [["Buyurtma", f"#{ctx.result['id']}"], ["Joy", order_place(ctx.result)]],
        [{"name": i["name"], "qty": i["qty"]} for i in ctx.result["items"]], f"order:{ctx.result['id']}")),
    "kitchen_ready": ("orders", _a_kitchen_ready),
    "pay_order": ("sales", _a_pay),
    "cancel_order": ("sales", _a_cancel_order),
    "cancel_sale": ("sales", _a_cancel_sale),
    "return_sale": ("sales", lambda ctx: entry(
        "Savdodan qaytarish", f"Buyurtma #{ctx.result['order_id']} · {fmt_money(ctx.result['amount'])} qaytarildi",
        [["Buyurtma", f"#{ctx.result['order_id']}"], ["Qaytarilgan summa", fmt_money(ctx.result["amount"])],
         ["Sabab", ctx.result.get("reason")]],
        [{"name": l["name"], "qty": l["qty"], "price": l["price"]} for l in ctx.result["items"]],
        f"order:{ctx.result['order_id']}")),
    "create_finance_type": ("finance", lambda ctx: entry(
        "Tranzaksiya turi yaratildi", f"{clean_name(ctx.data.get('name'))} ({'Kirim' if ctx.data.get('direction') == 'in' else 'Chiqim'})",
        [["Nomi", clean_name(ctx.data.get("name"))], ["Yo'nalishi", "Kirim" if ctx.data.get("direction") == "in" else "Chiqim"]])),
    "create_finance_entry": ("finance", _a_finance_entry),
    "cancel_finance_entry": ("finance", _a_cancel_finance),
    "create_supplier": ("finance", lambda ctx: entry(
        "Ta'minotchi qo'shildi", ctx.result.get("name"), [["Nomi", ctx.result.get("name")], ["Telefon", ctx.result.get("phone")]])),
    "set_account_balance": ("finance", lambda ctx: _a_balance(ctx, "account")),
    "set_customer_balance": ("finance", lambda ctx: _a_balance(ctx, "customer")),
    "set_supplier_balance": ("finance", lambda ctx: _a_balance(ctx, "supplier")),
    "create_customer": ("crm", lambda ctx: _a_customer(ctx, "Mijoz qo'shildi")),
    "update_customer": ("crm", lambda ctx: _a_customer(ctx, "Mijoz ma'lumotlari o'zgartirildi")),
    "add_debt": ("crm", _a_add_debt),
    "pay_debt": ("crm", _a_pay_debt),
    "cancel_debt_payment": ("crm", _a_cancel_debt_payment),
    "import_file": ("menu", _a_import),
    "create_user": ("users", lambda ctx: _a_user(ctx, "Xodim qo'shildi")),
    "update_user": ("users", lambda ctx: _a_user(ctx, "Xodim ma'lumotlari o'zgartirildi")),
    "delete_user": ("users", lambda ctx: entry(
        "Xodim bloklandi", f"{ctx.before.get('full_name')} ({ctx.before.get('username')})",
        [["Xodim", ctx.before.get("full_name")], ["Login", ctx.before.get("username")]], entity=f"user:{ctx.params[0]}")),
    "send_customer_message": ("crm", lambda ctx: entry(
        "Mijozlarga Telegram xabar yuborildi", f"{ctx.result['sent']} ta mijozga: {ctx.data.get('text', '')[:80]}",
        [["Kimga", "barcha ulangan mijozlar" if ctx.data.get("all") else ", ".join(ctx.result["names"])],
         ["Soni", ctx.result["sent"]], ["Xabar", ctx.data.get("text")]])),
    "save_telegram": ("settings", lambda ctx: entry(
        "Telegram bot sozlamalari saqlandi", "yoqildi" if ctx.result.get("enabled") else "o'chirildi",
        [["Holati", "yoqilgan" if ctx.result.get("enabled") else "o'chirilgan"],
         ["Chatlar", ", ".join(c.get("title") or str(c["id"]) for c in ctx.result.get("chats", []))],
         ["Yuboriladigan bo'limlar", ", ".join(JOURNAL_CATEGORIES.get(c, c) for c in ctx.result.get("categories", []))],
         ["Token", "yangilandi" if ctx.data.get("token") else None]])),
}


class AuditContext:
    def __init__(self, conn, user, params, data, result, before):
        self.conn, self.user, self.params, self.data, self.result, self.before = conn, user, params, data, result, before or {}


def clean_request(data):
    """Jurnalga yoziladigan so'rov ma'lumoti - parol, rasm, fayl kabi narsalarsiz."""
    out = {}
    for k, v in (data or {}).items():
        if k in SECRET_FIELDS or (isinstance(v, str) and v.startswith("data:")):
            if v:
                out[k] = "•••"
            continue
        out[k] = v
    return out


def write_journal(conn, user, category, rec, action, request=None):
    details = {"fields": rec["fields"], "items": rec.get("items"), "request": request}
    cur = conn.execute(
        """INSERT INTO audit_log (created_at, user_id, user_name, category, action, title, summary, details, entity)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now(), user["id"], user["full_name"], category, action, rec["title"], rec["summary"],
         json.dumps(details, ensure_ascii=False, default=str), rec.get("entity")),
    )
    return cur.lastrowid


def audit(conn, user, fn, params, data, result, before):
    """Amal muvaffaqiyatli bo'lgandan keyin jurnalga yozadi. Telegram uchun xabar matnini qaytaradi."""
    spec = AUDIT.get(fn.__name__)
    if not spec:
        return None
    category, build = spec
    try:
        conn.execute("SAVEPOINT audit")
        rec = build(AuditContext(conn, user, params, data, result if isinstance(result, dict) else {}, before))
        if not rec:
            conn.execute("RELEASE audit")
            return None
        category = rec.pop("category", None) or category
        write_journal(conn, user, category, rec, fn.__name__, clean_request(data))
        conn.execute("RELEASE audit")
    except Exception as e:  # jurnal xatosi asosiy amalni buzmasin
        conn.execute("ROLLBACK TO audit")
        conn.execute("RELEASE audit")
        sys.stderr.write(f"[{now()}] Jurnalga yozib bo'lmadi ({fn.__name__}): {e!r}\n")
        return None
    return telegram_message(conn, user, category, rec)


# --- integratsiyalar (Telegram bot va kelajakdagilar)


def get_integration(conn, name):
    row = conn.execute("SELECT * FROM integrations WHERE name = ?", (name,)).fetchone()
    config = {}
    if row:
        try:
            config = json.loads(row["config"] or "{}")
        except ValueError:
            config = {}
    return bool(row and row["enabled"]), config


def save_integration(conn, name, enabled, config):
    conn.execute(
        """INSERT INTO integrations (name, enabled, config, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled, config = excluded.config,
                                           updated_at = excluded.updated_at""",
        (name, 1 if enabled else 0, json.dumps(config, ensure_ascii=False), now()),
    )


def html_escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def telegram_text(conn, user, category, rec, created_at=None):
    cafe = get_settings(conn)["cafe_name"]
    emoji = {"sales": "🧾", "orders": "🍽", "finance": "💰", "crm": "👥", "menu": "📦", "users": "🧑‍💼",
             "settings": "⚙️", "auth": "🔑"}.get(category, "📌")
    lines = [f"{emoji} <b>{html_escape(rec['title'])}</b>"]
    if rec.get("summary"):
        lines.append(html_escape(rec["summary"]))
    for i in (rec.get("items") or [])[:30]:
        price = f" — {fmt_money(i['price'] * i['qty'])}" if i.get("price") is not None else ""
        lines.append(f"  • {html_escape(i['name'])} × {i['qty']}{price}")
    extra = [f for f in rec.get("fields", []) if f[0] in ("Chegirma", "Mijoz", "To'lov muddati", "Ta'minotchi", "Izoh", "Sabab")]
    for label, value in extra:
        if value and str(value) not in (rec.get("summary") or ""):
            lines.append(f"{html_escape(label)}: {html_escape(value)}")
    lines.append(f"\n👤 {html_escape(user['full_name'])} · 🕒 {created_at or now()}")
    lines.append(f"🏷 {html_escape(JOURNAL_CATEGORIES.get(category, category))} · {html_escape(cafe)}")
    return "\n".join(lines)


def telegram_message(conn, user, category, rec):
    enabled, cfg = get_integration(conn, "telegram")
    if not enabled or not cfg.get("token") or not cfg.get("chats"):
        return None
    if category not in cfg.get("categories", TELEGRAM_DEFAULT_CATEGORIES):
        return None
    return cfg["token"], [c["id"] for c in cfg["chats"]], telegram_text(conn, user, category, rec)


def mask_token(token):
    if not token:
        return ""
    return token[:6] + "•" * 6 + token[-4:] if len(token) > 12 else "•" * len(token)


def telegram_public(conn):
    enabled, cfg = get_integration(conn, "telegram")
    return {
        "enabled": enabled,
        "token_set": bool(cfg.get("token")),
        "token_hint": mask_token(cfg.get("token")),
        "bot": cfg.get("bot"),
        "chats": cfg.get("chats", []),
        "categories": cfg.get("categories", list(TELEGRAM_DEFAULT_CATEGORIES)),
        "all_categories": JOURNAL_CATEGORIES,
        "status": {"last_ok": telegram.notifier.last_ok, "last_error": telegram.notifier.last_error,
                   "sent": telegram.notifier.sent},
    }


@route("GET", "/api/integrations", ("integrations",))
def list_integrations(conn, user, params, data, query):
    enabled, cfg = get_integration(conn, "telegram")
    c_enabled, c_cfg = get_integration(conn, "customer_bot")
    return [{"key": "telegram", "enabled": enabled, "configured": bool(cfg.get("token") and cfg.get("chats"))},
            {"key": "customer_bot", "enabled": c_enabled, "configured": bool(c_cfg.get("token"))}]


@route("GET", "/api/integrations/telegram", ("integrations",))
def get_telegram(conn, user, params, data, query):
    return telegram_public(conn)


def parse_chats(value):
    if not isinstance(value, list):
        raise ApiError(400, "Chatlar ro'yxat bo'lishi kerak")
    chats = []
    for c in value:
        if not isinstance(c, dict):
            c = {"id": c}
        chat_id = str(c.get("id", "")).strip()
        if not re.fullmatch(r"-?\d{3,20}|@[A-Za-z0-9_]{4,64}", chat_id):
            raise ApiError(400, f"Chat ID noto'g'ri: {chat_id or 'bo`sh'}. Raqam (masalan 123456789 yoki -100...) bo'lsin")
        if chat_id not in [x["id"] for x in chats]:
            chats.append({"id": chat_id, "title": str(c.get("title") or "").strip()[:100]})
    return chats


@route("PUT", "/api/integrations/telegram", ("integrations",))
def save_telegram(conn, user, params, data, query):
    _, cfg = get_integration(conn, "telegram")
    token = (data.get("token") or "").strip()
    if token:
        if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,100}", token):
            raise ApiError(400, "Bot tokeni noto'g'ri ko'rinishda. BotFather bergan tokenni to'liq nusxalang")
        if token != cfg.get("token"):
            cfg["bot"] = data.get("bot") if isinstance(data.get("bot"), dict) else None
        cfg["token"] = token
    elif isinstance(data.get("bot"), dict):
        cfg["bot"] = data["bot"]
    if "chats" in data:
        cfg["chats"] = parse_chats(data["chats"])
    if "categories" in data:
        if not isinstance(data["categories"], list):
            raise ApiError(400, "Bo'limlar ro'yxat bo'lishi kerak")
        cfg["categories"] = [c for c in JOURNAL_CATEGORIES if c in data["categories"]]
    enabled = bool(data.get("enabled"))
    if enabled and not cfg.get("token"):
        raise ApiError(400, "Avval bot tokenini kiriting")
    if enabled and not cfg.get("chats"):
        raise ApiError(400, "Xabar boradigan kamida bitta chat qo'shing")
    save_integration(conn, "telegram", enabled, cfg)
    return telegram_public(conn)


def telegram_token(conn, data):
    token = (data.get("token") or "").strip()
    if not token:
        token = get_integration(conn, "telegram")[1].get("token")
    if not token:
        raise ApiError(400, "Bot tokenini kiriting")
    return token


def deferred_telegram(fn):
    """Telegram so'rovi internetni kutadi - baza qulfidan tashqarida bajariladi."""
    def run():
        try:
            return fn()
        except telegram.TelegramError as e:
            raise ApiError(502, str(e))
    return Deferred(run)


@route("POST", "/api/integrations/telegram/check", ("integrations",))
def check_telegram(conn, user, params, data, query):
    token = telegram_token(conn, data)
    return deferred_telegram(lambda: {
        k: v for k, v in telegram.get_me(token).items() if k in ("id", "username", "first_name")})


@route("POST", "/api/integrations/telegram/chats", ("integrations",))
def find_telegram_chats(conn, user, params, data, query):
    token = telegram_token(conn, data)
    return deferred_telegram(lambda: telegram.find_chats(token))


@route("POST", "/api/integrations/telegram/test", ("integrations",))
def test_telegram(conn, user, params, data, query):
    token = telegram_token(conn, data)
    chats = parse_chats(data["chats"]) if "chats" in data else get_integration(conn, "telegram")[1].get("chats", [])
    if not chats:
        raise ApiError(400, "Avval chat qo'shing")
    text = telegram_text(conn, user, "settings", entry(
        "✅ Sinov xabari", "CafePOS jurnali shu chatga keladi: sotuvlar, kirim-chiqim, qarzlar va boshqalar."))

    def run():
        results = []
        for c in chats:
            try:
                telegram.send_message(token, c["id"], text)
                results.append({"id": c["id"], "title": c.get("title"), "ok": True})
            except telegram.TelegramError as e:
                results.append({"id": c["id"], "title": c.get("title"), "ok": False, "error": str(e)})
        if not any(r["ok"] for r in results):
            raise ApiError(502, "; ".join(r["error"] for r in results))
        return results
    return Deferred(run)


# Oddiy ulash: token -> "Ulash", keyin botga /start yoziladi va chat o'zi qo'shiladi


def telegram_update(conn, user, change, title=None, summary=""):
    """Telegram sozlamasini baza qulfi ostida o'zgartiradi (Deferred ichidan chaqiriladi)."""
    with db_lock:
        try:
            enabled, cfg = get_integration(conn, "telegram")
            enabled = change(cfg, enabled)
            save_integration(conn, "telegram", enabled, cfg)
            if title:
                write_journal(conn, user, "settings", entry(title, summary), "telegram")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return telegram_public(conn)


@route("POST", "/api/integrations/telegram/connect", ("integrations",))
def connect_telegram(conn, user, params, data, query):
    token = (data.get("token") or "").strip()
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,100}", token):
        raise ApiError(400, "Token noto'g'ri. @BotFather bergan tokenni to'liq nusxalang")

    def run():
        try:
            me = telegram.get_me(token)
        except telegram.TelegramError as e:
            raise ApiError(502, str(e))
        bot = {k: me.get(k) for k in ("id", "username", "first_name")}

        def change(cfg, enabled):
            if cfg.get("token") != token:
                cfg["chats"] = []
            cfg.update(token=token, bot=bot)
            return bool(cfg.get("chats"))
        return telegram_update(conn, user, change, "Telegram bot ulandi", "@" + str(bot["username"]))
    return Deferred(run)


@route("POST", "/api/integrations/telegram/link", ("integrations",))
def link_telegram_chats(conn, user, params, data, query):
    """Botga /start yozgan chatlarni topib, o'zi qo'shadi va salom xabarini yuboradi."""
    _, cfg = get_integration(conn, "telegram")
    token = cfg.get("token")
    if not token:
        raise ApiError(400, "Avval botni ulang")
    known = {str(c["id"]) for c in cfg.get("chats", [])}
    cafe = get_settings(conn)["cafe_name"]

    def run():
        try:
            found = [c for c in telegram.find_chats(token) if str(c["id"]) not in known]
        except telegram.TelegramError as e:
            raise ApiError(502, str(e))
        if not found:
            return dict(telegram_public_locked(conn), added=[])
        for c in found:
            try:
                telegram.send_message(token, c["id"], f"✅ <b>{html_escape(cafe)}</b> ulandi.\n"
                                      "Endi dasturdagi amallar (sotuv, kirim-chiqim, qarzlar...) shu yerga keladi.")
            except telegram.TelegramError:
                pass

        def change(cfg, enabled):
            chats = cfg.setdefault("chats", [])
            for c in found:
                if str(c["id"]) not in {str(x["id"]) for x in chats}:
                    chats.append({"id": str(c["id"]), "title": c["title"]})
            return True
        names = ", ".join(c["title"] or str(c["id"]) for c in found)
        return dict(telegram_update(conn, user, change, "Telegram chat qo'shildi", names),
                    added=[c["title"] or str(c["id"]) for c in found])
    return Deferred(run)


def telegram_public_locked(conn):
    with db_lock:
        return telegram_public(conn)


@route("DELETE", "/api/integrations/telegram", ("integrations",))
def disconnect_telegram(conn, user, params, data, query):
    save_integration(conn, "telegram", False, {})
    write_journal(conn, user, "settings", entry("Telegram bot uzildi"), "telegram")
    return telegram_public(conn)


# --- mijozlar boti: mijoz telefon raqamini yuborib ulanadi, balansini ko'radi, chek va xabarlar oladi

OUTBOX = threading.local()  # so'rov davomida yig'iladi, commit'dan keyin yuboriladi
CUSTOMER_BOT_DEFAULTS = {"notify_sales": True, "notify_payments": True}
BTN_BALANCE, BTN_ORDERS = "💰 Balans", "🧾 Xaridlarim"
MAIN_KEYBOARD = {"keyboard": [[{"text": BTN_BALANCE}, {"text": BTN_ORDERS}]], "resize_keyboard": True}
CONTACT_KEYBOARD = {"keyboard": [[{"text": "📱 Telefon raqamni yuborish", "request_contact": True}]],
                    "resize_keyboard": True, "one_time_keyboard": True}


def customer_bot_config(conn):
    enabled, cfg = get_integration(conn, "customer_bot")
    for k, v in CUSTOMER_BOT_DEFAULTS.items():
        cfg.setdefault(k, v)
    return enabled, cfg


def notify_customer(conn, customer, text, kind=None):
    """Mijoz botga ulangan bo'lsa xabarni navbatga qo'yadi (commit'dan keyin yuboriladi)."""
    if not customer or not customer.get("telegram_chat_id"):
        return False
    enabled, cfg = customer_bot_config(conn)
    if not enabled or not cfg.get("token"):
        return False
    if kind and not cfg.get(kind, True):
        return False
    items = getattr(OUTBOX, "items", None)
    message = (cfg["token"], [customer["telegram_chat_id"]], text)
    if items is None:  # so'rovdan tashqarida (masalan, testda) - darhol navbatga
        telegram.customer_notifier.send(*message)
    else:
        items.append(message)
    return True


def fmt_date(value):
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d.%m.%Y") + value[10:16]
    except (TypeError, ValueError):
        return value or ""


def receipt_text(conn, order, customer):
    cafe = html_escape(get_settings(conn)["cafe_name"])
    lines = [f"🧾 <b>{cafe}</b> — xaridingiz uchun rahmat!", f"Buyurtma #{order['id']} · {fmt_date(order.get('closed_at') or now())}", ""]
    for i in order["items"]:
        lines.append(f"• {html_escape(i['name'])} × {i['qty']} — {fmt_money(i['price'] * i['qty'])}")
    lines.append("")
    if order.get("service"):
        lines.append(f"Xizmat haqi ({order['service_percent']:g}%): {fmt_money(order['service'])}")
    if order.get("discount"):
        lines.append(f"Chegirma: −{fmt_money(order['discount'])}")
    lines.append(f"<b>Jami: {fmt_money(order['total'])}</b>")
    lines.append(f"To'lov: {ACCOUNT_NAMES.get(order['payment_method'], order['payment_method'])}")
    if order["payment_method"] == "debt":
        debt = one(conn, "SELECT due_date FROM debts WHERE order_id = ? ORDER BY id DESC", order["id"])
        lines.append(f"\n📌 Qarzga yozildi, to'lov muddati: <b>{fmt_date(debt.get('due_date'))}</b>")
        lines.append(f"Umumiy qarzingiz: <b>{fmt_money(customer_remaining(conn, customer['id']))}</b>")
    return "\n".join(lines)


def balance_text(conn, customer):
    debts = [d for d in debts_query(conn, "AND d.customer_id = ?", (customer["id"],)) if d["remaining"] > 0]
    cafe = html_escape(get_settings(conn)["cafe_name"])
    if not debts:
        return f"💰 <b>Balansingiz</b> · {cafe}\n\n✅ Qarzingiz yo'q. Rahmat!"
    lines = [f"💰 <b>Balansingiz</b> · {cafe}", "", f"Umumiy qarz: <b>{fmt_money(sum(d['remaining'] for d in debts))}</b>", ""]
    for d in debts:
        when = (f"⚠️ {-d['days']} kun o'tdi" if d["days"] < 0 else "bugun to'lash kerak" if d["days"] == 0
                else f"{d['days']} kun qoldi")
        note = f" ({html_escape(d['comment'])})" if d.get("comment") else ""
        lines.append(f"• {fmt_money(d['remaining'])} — muddati {fmt_date(d['due_date'])}, {when}{note}")
    return "\n".join(lines)


def orders_text(conn, customer):
    orders = rows(conn.execute(
        """SELECT id, total, payment_method, closed_at FROM orders WHERE customer_id = ? AND status = 'paid'
           ORDER BY id DESC LIMIT 10""", (customer["id"],)))
    if not orders:
        return "🧾 Hozircha xaridlar yo'q."
    lines = ["🧾 <b>Oxirgi xaridlaringiz</b>", ""]
    for o in orders:
        lines.append(f"• {fmt_date(o['closed_at'])} · #{o['id']} · <b>{fmt_money(o['total'])}</b> · "
                     f"{ACCOUNT_NAMES.get(o['payment_method'], o['payment_method'])}")
    return "\n".join(lines)


def customer_bot_reply(conn, update):
    """Botga kelgan xabarga javoblar: [(chat_id, matn, klaviatura)]. Baza qulfi ostida chaqiriladi."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return []
    chat_id = str(chat["id"])
    cafe = html_escape(get_settings(conn)["cafe_name"])
    customer = one(conn, "SELECT * FROM customers WHERE telegram_chat_id = ?", chat_id)
    contact = msg.get("contact")
    if contact:
        if contact.get("user_id") and contact["user_id"] != (msg.get("from") or {}).get("id"):
            return [(chat_id, "Iltimos, <b>o'zingizning</b> raqamingizni yuboring 👇", CONTACT_KEYBOARD)]
        try:
            phone = normalize_phone(contact.get("phone_number"))
        except ApiError:
            phone = ""
        found = one(conn, "SELECT * FROM customers WHERE phone = ?", phone)
        if not found:
            return [(chat_id, f"😕 {html_escape(phone)} raqami {cafe} mijozlari ro'yxatida topilmadi.\n"
                              "Kassirga raqamingizni ayting va keyin qayta urinib ko'ring.", CONTACT_KEYBOARD)]
        conn.execute("UPDATE customers SET telegram_chat_id = NULL WHERE telegram_chat_id = ?", (chat_id,))
        conn.execute("UPDATE customers SET telegram_chat_id = ?, telegram_linked_at = ? WHERE id = ?",
                     (chat_id, now(), found["id"]))
        conn.commit()
        return [(chat_id, f"✅ Assalomu alaykum, <b>{html_escape(found['name'])}</b>!\n"
                          f"Siz {cafe} botiga ulandingiz. Endi xaridlaringiz cheki va to'lovlar shu yerga keladi.\n"
                          f"Qarzingizni ko'rish uchun <b>{BTN_BALANCE}</b> ni bosing.", MAIN_KEYBOARD),
                (chat_id, balance_text(conn, found), MAIN_KEYBOARD)]
    if not customer:
        return [(chat_id, f"Assalomu alaykum! <b>{cafe}</b> botiga xush kelibsiz.\n"
                          "Balansingiz va xaridlaringizni ko'rish uchun telefon raqamingizni yuboring 👇", CONTACT_KEYBOARD)]
    text = (msg.get("text") or "").strip()
    if text in (BTN_BALANCE, "/balans", "/balance"):
        return [(chat_id, balance_text(conn, customer), MAIN_KEYBOARD)]
    if text in (BTN_ORDERS, "/xaridlar"):
        return [(chat_id, orders_text(conn, customer), MAIN_KEYBOARD)]
    return [(chat_id, f"Salom, {html_escape(customer['name'])}! Quyidagi tugmalardan foydalaning 👇", MAIN_KEYBOARD)]


class CustomerBotPoller:
    """Mijozlar botiga kelgan xabarlarni orqa fonda o'qiydi (long polling - webhook/oq IP kerak emas)."""

    def __init__(self, conn):
        self.conn = conn
        self.offset = 0
        self.token = None
        self.last_error = None

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        import time
        while True:
            with db_lock:
                enabled, cfg = customer_bot_config(self.conn)
            token = cfg.get("token") if enabled else None
            if token != self.token:
                self.token, self.offset = token, 0
            if not token:
                time.sleep(3)
                continue
            try:
                updates = telegram.get_updates(token, self.offset)
                self.last_error = None
            except telegram.TelegramError as e:
                self.last_error = f"{time.strftime('%H:%M:%S')} · {e}"
                time.sleep(5)
                continue
            for upd in updates:
                self.offset = max(self.offset, upd.get("update_id", 0) + 1)
                try:
                    with db_lock:
                        replies = customer_bot_reply(self.conn, upd)
                    for chat_id, text, keyboard in replies:
                        telegram.send_message(token, chat_id, text, keyboard)
                except Exception as e:  # bitta xabar xatosi botni to'xtatmasin
                    self.last_error = f"{time.strftime('%H:%M:%S')} · {e}"


customer_poller = None


def customer_bot_public(conn):
    enabled, cfg = customer_bot_config(conn)
    linked = conn.execute("SELECT COUNT(*) FROM customers WHERE telegram_chat_id IS NOT NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    n = telegram.customer_notifier
    return {
        "enabled": enabled, "token_set": bool(cfg.get("token")), "bot": cfg.get("bot"),
        "notify_sales": cfg["notify_sales"], "notify_payments": cfg["notify_payments"],
        "linked": linked, "customers": total,
        "status": {"last_ok": n.last_ok, "sent": n.sent,
                   "last_error": n.last_error or (customer_poller.last_error if customer_poller else None)},
    }


@route("GET", "/api/integrations/customer-bot", ("integrations",))
def get_customer_bot(conn, user, params, data, query):
    return customer_bot_public(conn)


@route("POST", "/api/integrations/customer-bot/connect", ("integrations",))
def connect_customer_bot(conn, user, params, data, query):
    token = (data.get("token") or "").strip()
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,100}", token):
        raise ApiError(400, "Token noto'g'ri. @BotFather bergan tokenni to'liq nusxalang")
    if token == get_integration(conn, "telegram")[1].get("token"):
        raise ApiError(400, "Bu token xodimlar botiniki. Mijozlar uchun @BotFather'da alohida bot yarating")

    def run():
        try:
            me = telegram.get_me(token)
        except telegram.TelegramError as e:
            raise ApiError(502, str(e))
        bot = {k: me.get(k) for k in ("id", "username", "first_name")}
        with db_lock:
            try:
                _, cfg = customer_bot_config(conn)
                old_id = (cfg.get("bot") or {}).get("id") or cfg.get("last_bot_id")
                if old_id and old_id != bot["id"]:  # boshqa bot - eski ulanishlar ishlamaydi
                    conn.execute("UPDATE customers SET telegram_chat_id = NULL, telegram_linked_at = NULL")
                cfg.update(token=token, bot=bot)
                cfg.pop("last_bot_id", None)
                save_integration(conn, "customer_bot", True, cfg)
                write_journal(conn, user, "crm", entry("Mijozlar boti ulandi", "@" + str(bot["username"])), "customer_bot")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return customer_bot_public(conn)
    return Deferred(run)


@route("PUT", "/api/integrations/customer-bot", ("integrations",))
def save_customer_bot(conn, user, params, data, query):
    enabled, cfg = customer_bot_config(conn)
    if not cfg.get("token"):
        raise ApiError(400, "Avval botni ulang")
    for k in CUSTOMER_BOT_DEFAULTS:
        if k in data:
            cfg[k] = bool(data[k])
    save_integration(conn, "customer_bot", bool(data.get("enabled", enabled)), cfg)
    return customer_bot_public(conn)


@route("DELETE", "/api/integrations/customer-bot", ("integrations",))
def disconnect_customer_bot(conn, user, params, data, query):
    _, cfg = customer_bot_config(conn)
    save_integration(conn, "customer_bot", False, {"last_bot_id": (cfg.get("bot") or {}).get("id")})
    write_journal(conn, user, "crm", entry("Mijozlar boti uzildi"), "customer_bot")
    return customer_bot_public(conn)


@route("POST", "/api/customers/message", ("crm",))
def send_customer_message(conn, user, params, data, query):
    """Mijozlarga bot orqali xabar: hammaga (all=true) yoki tanlanganlarga (customer_ids)."""
    enabled, cfg = customer_bot_config(conn)
    if not enabled or not cfg.get("token"):
        raise ApiError(400, "Mijozlar boti ulanmagan (Integratsiyalar → Mijozlar boti)")
    text = (data.get("text") or "").strip()
    if len(text) < 2:
        raise ApiError(400, "Xabar matnini yozing")
    if len(text) > 3500:
        raise ApiError(400, "Xabar juda uzun (3500 belgigacha)")
    sql = "SELECT id, name, telegram_chat_id FROM customers WHERE telegram_chat_id IS NOT NULL"
    args = []
    if not data.get("all"):
        ids = [to_int(i, "customer_ids") for i in (data.get("customer_ids") or [])]
        if not ids:
            raise ApiError(400, "Mijozlarni tanlang")
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args = ids
    targets = rows(conn.execute(sql, args))
    if not targets:
        raise ApiError(400, "Tanlangan mijozlarning hech biri botga ulanmagan")
    cafe = html_escape(get_settings(conn)["cafe_name"])
    body = f"📢 <b>{cafe}</b>\n\n{html_escape(text)}"
    for t in targets:
        notify_customer(conn, t, body)
    return {"sent": len(targets), "names": [t["name"] for t in targets[:20]]}


# --- jurnal sahifasi


@route("GET", "/api/journal", ("journal",))
def list_journal(conn, user, params, data, query):
    q = lambda k: (query.get(k, [""])[0] or "").strip()
    today = datetime.now().date().isoformat()
    date_from, date_to = q("from") or today, q("to") or today
    where, args = ["j.created_at >= ?", "j.created_at < date(?, '+1 day')"], [date_from, date_to]
    if q("user_id"):
        where.append("j.user_id = ?")
        args.append(to_int(q("user_id"), "user_id"))
    if q("category"):
        where.append("j.category = ?")
        args.append(q("category"))
    if q("q"):
        where.append("(j.title LIKE ? OR j.summary LIKE ? OR j.user_name LIKE ?)")
        args += [f"%{q('q')}%"] * 3
    limit = min(to_int(q("limit") or 200, "limit", 1), 1000)
    offset = to_int(q("offset") or 0, "offset", 0)
    sql_where = " WHERE " + " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM audit_log j{sql_where}", args).fetchone()[0]
    items = rows(conn.execute(
        f"""SELECT j.id, j.created_at, j.user_id, j.user_name, j.category, j.action, j.title, j.summary
            FROM audit_log j{sql_where} ORDER BY j.id DESC LIMIT ? OFFSET ?""", args + [limit, offset]))
    users = rows(conn.execute("SELECT id, full_name FROM users ORDER BY active DESC, full_name"))
    return {"items": items, "total": total, "from": date_from, "to": date_to,
            "users": users, "categories": JOURNAL_CATEGORIES}


@route("GET", r"/api/journal/(\d+)", ("journal",))
def journal_detail(conn, user, params, data, query):
    row = one(conn, """SELECT j.*, u.username, u.role FROM audit_log j LEFT JOIN users u ON u.id = j.user_id
                       WHERE j.id = ?""", params[0])
    if not row:
        raise ApiError(404, "Yozuv topilmadi")
    row["details"] = json.loads(row["details"] or "{}")
    row["category_name"] = JOURNAL_CATEGORIES.get(row["category"], row["category"])
    return row



class LoginGuard:
    """PIN atigi 4 raqam - taxmin qilib topishning oldini olish: 5 ta xatodan keyin kutish."""

    LIMIT, BLOCK = 5, 60

    def __init__(self):
        self.fails = {}
        self.lock = threading.Lock()

    def blocked(self, ip):
        with self.lock:
            count, until = self.fails.get(ip, (0, 0))
            left = int(until - datetime.now().timestamp())
            return left if left > 0 else 0

    def fail(self, ip):
        with self.lock:
            count, until = self.fails.get(ip, (0, 0))
            count += 1
            if count >= self.LIMIT:
                self.fails[ip] = (0, datetime.now().timestamp() + self.BLOCK)
            else:
                self.fails[ip] = (count, until)

    def ok(self, ip):
        with self.lock:
            self.fails.pop(ip, None)


LOGIN_GUARD = LoginGuard()


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

    def send_file(self, f):
        self.send_response(200)
        self.send_header("Content-Type", f.ctype)
        self.send_header("Content-Length", str(len(f.content)))
        self.send_header("Content-Disposition", f'attachment; filename="{f.filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(f.content)

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
        self.telegram_out = None
        OUTBOX.items = []
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
            if self.telegram_out:  # faqat saqlangan (commit) amallar Telegramga boradi
                telegram.notifier.send(*self.telegram_out)
            for message in OUTBOX.items:  # mijozlarga cheklar va xabarlar
                telegram.customer_notifier.send(*message)
            OUTBOX.items = []
            status, payload, headers = result
            if isinstance(payload, Deferred):
                payload = payload.fn()
            if isinstance(payload, FileResponse):
                return self.send_file(payload)
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
            if method == "GET":
                return 200, fn(conn, user, m.groups(), data, query), None
            before_fn = AUDIT_BEFORE.get(fn.__name__)
            before = before_fn(conn, m.groups(), data) if before_fn else None
            result = fn(conn, user, m.groups(), data, query)
            self.telegram_out = audit(conn, user, fn, m.groups(), data, result, before)
            return 200, result, None
        raise ApiError(405 if path_matched else 404, "Topilmadi")

    def login(self, conn, data):
        ip = self.client_address[0]
        wait = LOGIN_GUARD.blocked(ip)
        if wait:
            raise ApiError(429, f"Ko'p noto'g'ri urinish. {wait} soniyadan keyin qayta urining")
        if data.get("pin") not in (None, ""):  # PIN kod bilan kirish (ekrandagi raqamlar)
            row = conn.execute("SELECT * FROM users WHERE pin_lookup = ? AND active = 1",
                               (pin_hash(conn, str(data["pin"]).strip()),)).fetchone()
            if not row:
                LOGIN_GUARD.fail(ip)
                raise ApiError(401, "PIN kod noto'g'ri")
        else:
            require(data, "username", "password")
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND active = 1", (data["username"].strip(),)
            ).fetchone()
            if not row or not secrets.compare_digest(
                hash_password(data["password"], row["salt"])[0], row["password_hash"]
            ):
                LOGIN_GUARD.fail(ip)
                raise ApiError(401, "Login yoki parol noto'g'ri")
        LOGIN_GUARD.ok(ip)
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)", (token, row["id"], expires)
        )
        cookie = f"sid={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Strict"
        user = public_user(row)
        agent = self.headers.get("User-Agent", "")
        device = ("Android ilova" if "CafePOS" in agent else "iPhone/iPad" if re.search(r"iPhone|iPad", agent)
                  else "Android" if "Android" in agent else "Kompyuter")
        rec = entry("Tizimga kirdi", f"{user['full_name']} ({user['username']}) · {device}",
                    [["Xodim", user["full_name"]], ["Login", user["username"]], ["Qurilma", device],
                     ["IP manzil", self.client_address[0]]])
        write_journal(conn, user, "auth", rec, "login")
        self.telegram_out = telegram_message(conn, user, "auth", rec)
        return 200, user, {"Set-Cookie": cookie}


def make_server(port=PORT, host="0.0.0.0", poll_bots=False):
    conn = connect()
    init_db(conn)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.conn = conn
    if poll_bots:  # mijozlar botiga kelgan xabarlarni o'qish
        global customer_poller
        customer_poller = CustomerBotPoller(conn)
        customer_poller.start()
    return server


def main():
    try:
        server = make_server(poll_bots=True)
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
    print(f"  Kirish: PIN {DEFAULT_PIN} (administrator)   yoki login: admin / parol: admin123")
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
