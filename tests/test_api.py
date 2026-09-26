"""API testlari:  python -m unittest discover tests"""

import base64
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req) as res:
                return res.status, json.loads(res.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def login(self, username, password):
        status, _ = self.call("POST", "/api/login", {"username": username, "password": password})
        assert status == 200, status
        return self


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CAFEPOS_DB"] = os.path.join(cls.tmp.name, "test.db")
        import server

        server.DB_PATH = os.environ["CAFEPOS_DB"]
        server.UPLOAD_DIR = os.path.join(cls.tmp.name, "uploads")
        cls.server = server.make_server(port=0, host="127.0.0.1")
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.admin = Client(cls.base).login("admin", "admin123")
        for username, role in (("kassir", "cashier"), ("ofitsiant", "waiter")):
            cls.admin.call("POST", "/api/users", {
                "username": username, "full_name": username.title(), "role": role, "password": "1234",
            })
        cls.cashier = Client(cls.base).login("kassir", "1234")
        cls.waiter = Client(cls.base).login("ofitsiant", "1234")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.conn.close()
        cls.tmp.cleanup()

    def free_table(self):
        _, tables = self.admin.call("GET", "/api/tables")
        return next(t for t in tables if not t["order"])

    def test_login_required(self):
        status, _ = Client(self.base).call("GET", "/api/tables")
        self.assertEqual(status, 401)

    def test_wrong_password(self):
        status, body = Client(self.base).call("POST", "/api/login", {"username": "admin", "password": "x"})
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_static_index(self):
        from urllib.request import urlopen

        with urlopen(self.base + "/") as res:
            self.assertIn(b"CafePOS", res.read())

    def test_full_order_flow(self):
        table = self.free_table()
        _, products = self.waiter.call("GET", "/api/products")
        p1, p2 = products[0], products[1]

        status, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        self.assertEqual(status, 200)
        # Band stolga qayta bosilsa - o'sha buyurtma qaytadi
        _, again = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        self.assertEqual(again["id"], order["id"])

        oid = order["id"]
        self.waiter.call("POST", f"/api/orders/{oid}/items", {"product_id": p1["id"], "qty": 2})
        self.waiter.call("POST", f"/api/orders/{oid}/items", {"product_id": p1["id"]})
        _, order = self.waiter.call("POST", f"/api/orders/{oid}/items", {"product_id": p2["id"]})
        self.assertEqual(len(order["items"]), 2)
        self.assertEqual(order["items"][0]["qty"], 3)
        self.assertEqual(order["subtotal"], p1["price"] * 3 + p2["price"])

        item2 = order["items"][1]["id"]
        _, order = self.waiter.call("PUT", f"/api/orders/{oid}/items/{item2}", {"qty": 0})
        self.assertEqual(len(order["items"]), 1)

        # Ofitsiant to'lov qabul qila olmaydi
        status, _ = self.waiter.call("POST", f"/api/orders/{oid}/pay", {"method": "cash"})
        self.assertEqual(status, 403)

        status, paid = self.cashier.call("POST", f"/api/orders/{oid}/pay", {"method": "card", "discount": 1000})
        self.assertEqual(status, 200)
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(paid["total"], p1["price"] * 3 - 1000)

        # Yopilgan buyurtmaga taom qo'shib bo'lmaydi
        status, _ = self.waiter.call("POST", f"/api/orders/{oid}/items", {"product_id": p1["id"]})
        self.assertEqual(status, 409)

        # Stol yana bo'sh
        _, tables = self.admin.call("GET", "/api/tables")
        self.assertIsNone(next(t for t in tables if t["id"] == table["id"])["order"])

        _, report = self.cashier.call("GET", "/api/reports")
        self.assertGreaterEqual(report["summary"]["revenue"], paid["total"])
        self.assertIn("card", [m["method"] for m in report["by_method"]])

    def test_takeaway_and_cancel(self):
        _, order = self.waiter.call("POST", "/api/orders", {"type": "takeaway"})
        self.assertIsNone(order["table_id"])
        status, _ = self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "cash"})
        self.assertEqual(status, 400)  # bo'sh buyurtma
        status, _ = self.cashier.call("POST", f"/api/orders/{order['id']}/cancel")
        self.assertEqual(status, 200)
        _, detail = self.cashier.call("GET", f"/api/orders/{order['id']}")
        self.assertEqual(detail["status"], "cancelled")

    def test_discount_cannot_exceed_total(self):
        _, products = self.admin.call("GET", "/api/products")
        _, order = self.admin.call("POST", "/api/orders", {"type": "takeaway"})
        self.admin.call("POST", f"/api/orders/{order['id']}/items", {"product_id": products[0]["id"]})
        status, _ = self.admin.call(
            "POST", f"/api/orders/{order['id']}/pay", {"method": "cash", "discount": products[0]["price"] + 1}
        )
        self.assertEqual(status, 400)

    def test_menu_admin_only(self):
        status, _ = self.waiter.call("POST", "/api/products", {"name": "X", "price": 1})
        self.assertEqual(status, 403)
        status, _ = self.cashier.call("GET", "/api/users")
        self.assertEqual(status, 403)
        status, _ = self.waiter.call("GET", "/api/reports")
        self.assertEqual(status, 403)

    def test_menu_crud(self):
        _, cat = self.admin.call("POST", "/api/categories", {"name": "Test kategoriya"})
        _, prod = self.admin.call("POST", "/api/products", {"name": "Test taom", "price": "12000", "category_id": cat["id"]})
        self.admin.call("PUT", f"/api/products/{prod['id']}", {"name": "Test taom 2", "price": 15000, "category_id": cat["id"]})
        _, products = self.admin.call("GET", "/api/products")
        found = next(p for p in products if p["id"] == prod["id"])
        self.assertEqual((found["name"], found["price"]), ("Test taom 2", 15000))

        status, _ = self.admin.call("DELETE", f"/api/categories/{cat['id']}")
        self.assertEqual(status, 409)  # ichida taom bor
        self.admin.call("DELETE", f"/api/products/{prod['id']}")
        status, _ = self.admin.call("DELETE", f"/api/categories/{cat['id']}")
        self.assertEqual(status, 200)

    def test_bad_price(self):
        status, _ = self.admin.call("POST", "/api/products", {"name": "X", "price": "abc"})
        self.assertEqual(status, 400)

    def test_deactivated_user_logged_out(self):
        _, u = self.admin.call("POST", "/api/users", {
            "username": "vaqtincha", "full_name": "Vaqtincha", "role": "waiter", "password": "1234",
        })
        client = Client(self.base).login("vaqtincha", "1234")
        self.admin.call("PUT", f"/api/users/{u['id']}", {"full_name": "Vaqtincha", "role": "waiter", "active": False})
        status, _ = client.call("GET", "/api/tables")
        self.assertEqual(status, 401)

    def new_order_with(self, *product_ids):
        _, order = self.admin.call("POST", "/api/orders", {"type": "takeaway"})
        for pid in product_ids:
            _, order = self.admin.call("POST", f"/api/orders/{order['id']}/items", {"product_id": pid})
        return order

    def test_kitchen_print_by_product_printer(self):
        # Soxta tarmoq printeri: kelgan baytlarni yig'adi
        received = []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()

        def accept():
            while True:
                try:
                    c, _ = listener.accept()
                except OSError:
                    return
                with c:
                    data = b""
                    while chunk := c.recv(4096):
                        data += chunk
                    received.append(data)

        threading.Thread(target=accept, daemon=True).start()
        port = listener.getsockname()[1]
        try:
            _, kitchen = self.admin.call("POST", "/api/printers", {
                "name": "Oshxona", "kind": "network", "address": "127.0.0.1", "port": port, "width": 80,
            })
            _, dead = self.admin.call("POST", "/api/printers", {
                "name": "Bar", "kind": "network", "address": "127.0.0.1", "port": 1, "width": 58,
            })
            _, p_hot = self.admin.call("POST", "/api/products", {"name": "Manti", "price": 20000, "printer_id": kitchen["id"]})
            _, p_bar = self.admin.call("POST", "/api/products", {"name": "Sharbat", "price": 9000, "printer_id": dead["id"]})
            _, p_none = self.admin.call("POST", "/api/products", {"name": "Non", "price": 3000})

            order = self.new_order_with(p_hot["id"], p_hot["id"], p_bar["id"], p_none["id"])
            self.assertEqual(order["pending_print"], 3)  # printersiz "Non" hisoblanmaydi

            # Bar printeri ishlamaydi - Oshxona chiqadi, Bar kutib turadi
            status, res = self.admin.call("POST", f"/api/orders/{order['id']}/kitchen-print")
            self.assertEqual(status, 200)
            self.assertEqual(res["printed"], ["Oshxona"])
            self.assertEqual(len(res["errors"]), 1)
            self.assertEqual(res["pending_print"], 1)
            for _ in range(50):
                if received:
                    break
                time.sleep(0.05)
            self.assertIn(b"2 x Manti", received[0])
            self.assertNotIn(b"Sharbat", received[0])

            # Mantini 1 taga kamaytiramiz - faqat "BEKOR" qatori chiqadi
            manti = next(i for i in res["items"] if i["name"] == "Manti")
            self.admin.call("PUT", f"/api/orders/{order['id']}/items/{manti['id']}", {"qty": 1})
            self.admin.call("DELETE", f"/api/printers/{dead['id']}")
            status, res = self.admin.call("POST", f"/api/orders/{order['id']}/kitchen-print")
            self.assertEqual(status, 200)
            for _ in range(50):
                if len(received) > 1:
                    break
                time.sleep(0.05)
            self.assertIn(b"BEKOR: 1 x Manti", received[1])
            self.assertEqual(res["pending_print"], 0)

            status, res = self.admin.call("POST", f"/api/orders/{order['id']}/kitchen-print")
            self.assertEqual((status, res["printed"], res["errors"]), (200, [], []))  # yangi narsa yo'q - xatosiz
        finally:
            listener.close()

    def test_kitchen_screen(self):
        _, products = self.admin.call("GET", "/api/products")
        order = self.new_order_with(products[0]["id"], products[0]["id"], products[1]["id"])
        self.assertEqual(order["pending_kds"], 3)
        status, res = self.waiter.call("POST", f"/api/orders/{order['id']}/kitchen-send")
        self.assertEqual(status, 200)
        self.assertEqual(res["pending_kds"], 0)

        cook = Client(self.base)
        self.admin.call("POST", "/api/users", {"username": "oshpaz", "full_name": "Oshpaz", "role": "cook", "password": "1234"})
        cook.login("oshpaz", "1234")
        _, tickets = cook.call("GET", "/api/kitchen")
        mine = [t for t in tickets if t["order_id"] == order["id"]]
        self.assertEqual(sum(l["qty"] for t in mine for l in t["lines"]), 3)

        # Ofitsiant oshxona ekranini ko'ra olmaydi
        status, _ = self.waiter.call("GET", "/api/kitchen")
        self.assertEqual(status, 403)

        # Taom olib tashlansa - oshxonaga BEKOR boradi
        item = order["items"][1]
        self.admin.call("PUT", f"/api/orders/{order['id']}/items/{item['id']}", {"qty": 0})
        _, res = self.admin.call("POST", f"/api/orders/{order['id']}/kitchen-send")
        self.assertEqual(res["items"][0]["qty"], 2)
        _, tickets = cook.call("GET", "/api/kitchen")
        lines = [l for t in tickets if t["order_id"] == order["id"] for l in t["lines"]]
        self.assertIn({"name": item["name"], "qty": -1}, lines)

        for t in tickets:
            cook.call("POST", f"/api/kitchen/{t['id']}/ready")
        _, tickets = cook.call("GET", "/api/kitchen")
        self.assertEqual(tickets, [])

    def test_halls(self):
        _, halls = self.admin.call("GET", "/api/halls")
        self.assertEqual([h["name"] for h in halls][:3], ["Asosiy zal", "Banket zali", "Kabinalar"])
        _, tables = self.admin.call("GET", "/api/tables")
        cabins = [t for t in tables if t["hall_name"] == "Kabinalar"]
        self.assertEqual(len(cabins), 4)

        _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": cabins[0]["id"]})
        self.assertEqual((order["hall_name"], order["table_name"]), ("Kabinalar", "Kabina 1"))

        _, hall = self.admin.call("POST", "/api/halls", {"name": "Terassa"})
        _, table = self.admin.call("POST", "/api/tables", {"name": "T1", "seats": 2, "hall_id": hall["id"]})
        status, _ = self.admin.call("DELETE", f"/api/halls/{hall['id']}")
        self.assertEqual(status, 409)  # zalda stol bor
        self.admin.call("DELETE", f"/api/tables/{table['id']}")
        status, _ = self.admin.call("DELETE", f"/api/halls/{hall['id']}")
        self.assertEqual(status, 200)
        status, _ = self.admin.call("POST", "/api/tables", {"name": "X", "hall_id": 9999})
        self.assertEqual(status, 404)
        self.admin.call("POST", f"/api/orders/{order['id']}/cancel")

    def test_cost_image_and_profit(self):
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        ).decode()
        status, prod = self.admin.call("POST", "/api/products", {
            "name": "Salat Sezar", "price": 30000, "cost": 12000, "image": "data:image/png;base64," + png,
        })
        self.assertEqual(status, 200)
        _, products = self.admin.call("GET", "/api/products")
        p = next(x for x in products if x["id"] == prod["id"])
        self.assertEqual(p["cost"], 12000)
        self.assertTrue(p["image"])

        from urllib.request import urlopen
        with urlopen(self.base + "/uploads/" + p["image"]) as res:
            self.assertTrue(res.read().startswith(b"\x89PNG"))

        # Noto'g'ri fayl turi
        status, _ = self.admin.call("PUT", f"/api/products/{p['id']}", {
            "name": "Salat Sezar", "price": 30000, "image": "data:text/html;base64,PGgxPg==",
        })
        self.assertEqual(status, 400)

        # Foyda hisoboti: 2 x (30000 - 12000) = 36000
        _, before = self.admin.call("GET", "/api/reports")
        order = self.new_order_with(p["id"], p["id"])
        self.admin.call("POST", f"/api/orders/{order['id']}/pay", {"method": "cash"})
        _, after = self.admin.call("GET", "/api/reports")
        self.assertEqual(after["summary"]["cost"] - before["summary"]["cost"], 24000)
        self.assertEqual(after["summary"]["profit"] - before["summary"]["profit"], 36000)

        # Rasmni o'chirish - fayl ham o'chadi
        self.admin.call("PUT", f"/api/products/{p['id']}", {
            "name": "Salat Sezar", "price": 30000, "cost": 12000, "remove_image": True,
        })
        self.assertFalse(os.path.exists(os.path.join(self.server_module().UPLOAD_DIR, p["image"])))

    @staticmethod
    def server_module():
        import server
        return server

    def test_system_printer_kind(self):
        status, _ = self.admin.call("POST", "/api/printers", {"name": "Bar", "kind": "system", "address": ""})
        self.assertEqual(status, 400)
        status, pr = self.admin.call("POST", "/api/printers", {"name": "Bar", "kind": "system", "address": "XP-80C"})
        self.assertEqual(status, 200)
        status, devices = self.admin.call("GET", "/api/printers/system")
        self.assertEqual(status, 200)
        self.assertIsInstance(devices, list)
        status, _ = self.waiter.call("GET", "/api/printers/system")
        self.assertEqual(status, 403)
        self.admin.call("DELETE", f"/api/printers/{pr['id']}")

    def test_service_charge(self):
        _, halls = self.admin.call("GET", "/api/halls")
        main = next(h for h in halls if h["name"] == "Asosiy zal")
        cabins = next(h for h in halls if h["name"] == "Kabinalar")
        _, tables = self.admin.call("GET", "/api/tables")
        main_table = next(t for t in tables if t["hall_id"] == main["id"] and not t["order"])
        cabin = next(t for t in tables if t["hall_id"] == cabins["id"] and not t["order"])
        _, product = self.admin.call("POST", "/api/products", {"name": "Kabob", "price": 50000})

        status, _ = self.cashier.call("PUT", "/api/settings", {"service_percent": 10})
        self.assertEqual(status, 403)
        status, _ = self.admin.call("PUT", "/api/settings", {"service_percent": 150})
        self.assertEqual(status, 400)
        _, settings = self.admin.call("PUT", "/api/settings", {"service_percent": "10", "cafe_name": "Test Kafe"})
        self.assertEqual((settings["service_percent"], settings["cafe_name"]), (10, "Test Kafe"))
        self.admin.call("PUT", f"/api/halls/{cabins['id']}", {"name": "Kabinalar", "sort": 2, "service_percent": 15})
        try:
            # Asosiy zal: umumiy 10%
            _, o1 = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": main_table["id"]})
            _, o1 = self.waiter.call("POST", f"/api/orders/{o1['id']}/items", {"product_id": product["id"], "qty": 2})
            self.assertEqual((o1["subtotal"], o1["service_percent"], o1["service"], o1["total"]), (100000, 10, 10000, 110000))

            # Stol kartasi va kassa ro'yxatida ham xizmat haqi bilan
            _, tables = self.admin.call("GET", "/api/tables")
            self.assertEqual(next(t for t in tables if t["id"] == main_table["id"])["order"]["total"], 110000)
            _, open_orders = self.cashier.call("GET", "/api/orders?status=open")
            self.assertEqual(next(o for o in open_orders if o["id"] == o1["id"])["total"], 110000)

            # Kabina: zal foizi 15%
            _, o2 = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": cabin["id"]})
            _, o2 = self.waiter.call("POST", f"/api/orders/{o2['id']}/items", {"product_id": product["id"]})
            self.assertEqual((o2["service"], o2["total"]), (7500, 57500))

            # Olib ketish: xizmat haqi yo'q
            _, o3 = self.admin.call("POST", "/api/orders", {"type": "takeaway"})
            _, o3 = self.admin.call("POST", f"/api/orders/{o3['id']}/items", {"product_id": product["id"]})
            self.assertEqual((o3["service"], o3["total"]), (0, 50000))

            # To'lovda chegirma bilan; keyin foiz o'zgarsa ham yopilgan buyurtma o'zgarmaydi
            _, before = self.admin.call("GET", "/api/reports")
            _, paid = self.cashier.call("POST", f"/api/orders/{o1['id']}/pay", {"method": "cash", "discount": 5000})
            self.assertEqual((paid["service"], paid["total"]), (10000, 105000))
            self.admin.call("PUT", "/api/settings", {"service_percent": 20})
            _, again = self.admin.call("GET", f"/api/orders/{o1['id']}")
            self.assertEqual((again["service"], again["total"]), (10000, 105000))
            _, after = self.admin.call("GET", "/api/reports")
            self.assertEqual(after["summary"]["service"] - before["summary"]["service"], 10000)
            self.assertEqual(after["summary"]["revenue"] - before["summary"]["revenue"], 105000)
        finally:
            self.admin.call("PUT", "/api/settings", {"service_percent": 0})
            self.admin.call("PUT", f"/api/halls/{cabins['id']}", {"name": "Kabinalar", "sort": 2, "service_percent": ""})
            # Ochiq qolgan buyurtmalar boshqa testlarga xalaqit bermasin
            _, open_orders = self.admin.call("GET", "/api/orders?status=open")
            for o in open_orders:
                self.admin.call("POST", f"/api/orders/{o['id']}/cancel")

    def test_custom_permissions(self):
        # Kassir, lekin faqat kassa ruxsati bilan (hisobot yo'q) + menyu ruxsati qo'shilgan
        status, u = self.admin.call("POST", "/api/users", {
            "first_name": "Aziz", "last_name": "Karimov", "username": "aziz", "password": "1234",
            "role": "cashier", "phone": "+998901234567", "permissions": ["cashier", "menu", "nonsense"],
        })
        self.assertEqual(status, 200)
        aziz = Client(self.base).login("aziz", "1234")
        _, me = aziz.call("GET", "/api/me")
        self.assertEqual(me["permissions"], ["cashier", "menu"])
        self.assertEqual(me["full_name"], "Aziz Karimov")
        self.assertEqual(aziz.call("GET", "/api/reports")[0], 403)
        self.assertEqual(aziz.call("GET", "/api/orders?status=open")[0], 200)
        self.assertEqual(aziz.call("POST", "/api/categories", {"name": "Aziz kat"})[0], 200)
        self.assertEqual(aziz.call("POST", "/api/orders", {"type": "takeaway"})[0], 403)  # stollar ruxsati yo'q

        # Ruxsat o'zgarsa darhol kuchga kiradi (qayta kirish shart emas)
        self.admin.call("PUT", f"/api/users/{u['id']}", {
            "first_name": "Aziz", "last_name": "Karimov", "role": "cashier", "permissions": ["reports"],
        })
        self.assertEqual(aziz.call("GET", "/api/reports")[0], 200)
        self.assertEqual(aziz.call("POST", "/api/categories", {"name": "X"})[0], 403)

        # Xodimlar ruxsati bor, lekin admin emas - admin yarata olmaydi
        self.admin.call("PUT", f"/api/users/{u['id']}", {
            "first_name": "Aziz", "role": "cashier", "permissions": ["users"],
        })
        status, _ = aziz.call("POST", "/api/users", {
            "first_name": "Boss", "username": "boss", "password": "1234", "role": "admin",
        })
        self.assertEqual(status, 403)
        _, users = aziz.call("GET", "/api/users")
        admin_row = next(x for x in users if x["username"] == "admin")
        self.assertEqual(aziz.call("DELETE", f"/api/users/{admin_row['id']}")[0], 403)

        # O'chirish = bloklash
        self.assertEqual(self.admin.call("DELETE", f"/api/users/{u['id']}")[0], 200)
        self.assertEqual(aziz.call("GET", "/api/me")[0], 401)
        status, _ = Client(self.base).call("POST", "/api/login", {"username": "aziz", "password": "1234"})
        self.assertEqual(status, 401)

    def test_old_users_get_role_defaults(self):
        _, users = self.admin.call("GET", "/api/users")
        waiter = next(u for u in users if u["username"] == "ofitsiant")
        self.assertEqual(waiter["permissions"], ["tables"])
        self.assertEqual(waiter["first_name"], "Ofitsiant")

    def test_network_info(self):
        status, info = self.waiter.call("GET", "/api/network")
        self.assertEqual(status, 200)
        self.assertIn("main", info)
        self.assertIsInstance(info["others"], list)
        if info["main"]:
            self.assertTrue(info["main"].startswith("http://"))
            self.assertNotIn(info["main"], info["others"])

    def test_dashboard(self):
        _, before = self.admin.call("GET", "/api/dashboard?period=today")
        _, prod = self.admin.call("POST", "/api/products", {"name": "Dashboard taom", "price": 40000})
        order = self.new_order_with(prod["id"], prod["id"])
        self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "click"})
        for period, size in (("today", 24), ("week", 7), ("year", 12)):
            status, d = self.admin.call("GET", f"/api/dashboard?period={period}")
            self.assertEqual(status, 200)
            self.assertEqual(len(d["series"]), size)
            self.assertEqual(sum(x["value"] for x in d["series"]), d["summary"]["revenue"])
        _, after = self.admin.call("GET", "/api/dashboard?period=today")
        self.assertEqual(after["today"]["revenue"] - before["today"]["revenue"], 80000)
        self.assertEqual(after["today"]["orders"] - before["today"]["orders"], 1)
        self.assertEqual(after["summary"]["items"] - before["summary"]["items"], 2)
        click = next(m for m in after["by_method"] if m["method"] == "click")
        self.assertGreaterEqual(click["revenue"], 80000)
        self.assertEqual(self.waiter.call("GET", "/api/dashboard")[0], 403)
        self.assertEqual(self.admin.call("GET", "/api/dashboard?period=x")[0], 400)

    def test_finance(self):
        _, types = self.admin.call("GET", "/api/finance/types")
        names = {t["name"]: t for t in types}
        topup = names["Mijoz balansini to'ldirish"]
        supplier = names["Ta'minotchiga pul berish"]
        self.assertEqual((topup["direction"], topup["is_system"]), ("in", 1))
        self.assertEqual((supplier["direction"], supplier["is_system"]), ("out", 1))

        # Dublikat bo'lmaydi (katta-kichik harf va bo'sh joylar farqi hisobga olinmaydi)
        status, _ = self.admin.call("POST", "/api/finance/types", {"name": "  mijoz  balansini TO'LDIRISH ", "direction": "in"})
        self.assertEqual(status, 409)
        status, rent = self.admin.call("POST", "/api/finance/types", {"name": "Ijara to'lovi", "direction": "out"})
        self.assertEqual(status, 200)
        self.assertEqual(self.admin.call("POST", "/api/finance/types", {"name": "X", "direction": "in"})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/finance/types", {"name": "Boshqa", "direction": "?"})[0], 400)
        # O'zgartirib/o'chirib bo'lmaydi
        self.assertEqual(self.admin.call("PUT", f"/api/finance/types/{rent['id']}", {"name": "Y"})[0], 404)
        self.assertEqual(self.admin.call("DELETE", f"/api/finance/types/{rent['id']}")[0], 404)

        _, before = self.admin.call("GET", "/api/finance/balance")
        cash0 = next(a for a in before["accounts"] if a["account"] == "cash")["balance"]
        _, e1 = self.admin.call("POST", "/api/finance/entries",
                                {"type_id": topup["id"], "account": "cash", "amount": 100000, "comment": "Ali aka"})
        _, e2 = self.admin.call("POST", "/api/finance/entries", {"type_id": rent["id"], "account": "cash", "amount": 30000})
        self.assertEqual(self.admin.call("POST", "/api/finance/entries", {"type_id": rent["id"], "account": "safe", "amount": 1})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/finance/entries", {"type_id": rent["id"], "account": "cash", "amount": 0})[0], 400)
        _, bal = self.admin.call("GET", "/api/finance/balance")
        self.assertEqual(next(a for a in bal["accounts"] if a["account"] == "cash")["balance"] - cash0, 70000)

        # Savdo tushumi ham kassaga tushadi
        order = self.new_order_with(self.admin.call("GET", "/api/products")[1][0]["id"])
        _, paid = self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "cash"})
        _, bal2 = self.admin.call("GET", "/api/finance/balance")
        self.assertEqual(next(a for a in bal2["accounts"] if a["account"] == "cash")["balance"] - cash0, 70000 + paid["total"])

        # Bekor qilish: balansdan chiqadi, lekin ro'yxatda qoladi; ikki marta bekor qilib bo'lmaydi
        self.assertEqual(self.admin.call("POST", f"/api/finance/entries/{e2['id']}/cancel", {"reason": "xato"})[0], 200)
        self.assertEqual(self.admin.call("POST", f"/api/finance/entries/{e2['id']}/cancel")[0], 409)
        self.assertIn(self.admin.call("DELETE", f"/api/finance/entries/{e2['id']}")[0], (404, 405))  # o'chirib bo'lmaydi
        _, bal3 = self.admin.call("GET", "/api/finance/balance")
        self.assertEqual(next(a for a in bal3["accounts"] if a["account"] == "cash")["balance"] - cash0, 100000 + paid["total"])
        _, lst = self.admin.call("GET", "/api/finance/entries")
        mine = {e["id"]: e for e in lst["entries"] if e["source"] == "manual"}
        self.assertEqual(mine[e2["id"]]["status"], "cancelled")
        self.assertEqual(mine[e1["id"]]["comment"], "Ali aka")
        self.assertTrue(any(e["source"] == "sale" and e["id"] == order["id"] for e in lst["entries"]))
        _, only_out = self.admin.call("GET", "/api/finance/entries?direction=out")
        self.assertTrue(all(e["direction"] == "out" for e in only_out["entries"]))

        # Ruxsatsiz xodim kira olmaydi
        self.assertEqual(self.cashier.call("GET", "/api/finance/balance")[0], 403)

    def test_empty_table_not_busy_and_discard(self):
        table = self.free_table()
        _, products = self.waiter.call("GET", "/api/products")
        _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        # Taom qo'shilmagan - stol bo'sh, kassada ham ko'rinmaydi
        _, tables = self.waiter.call("GET", "/api/tables")
        self.assertIsNone(next(t for t in tables if t["id"] == table["id"])["order"])
        _, open_orders = self.cashier.call("GET", "/api/orders?status=open")
        self.assertNotIn(order["id"], [o["id"] for o in open_orders])
        # Chiqib ketdi - buyurtma butunlay o'chadi
        status, res = self.waiter.call("POST", f"/api/orders/{order['id']}/discard")
        self.assertEqual((status, res["deleted"]), (200, True))
        self.assertEqual(self.waiter.call("GET", f"/api/orders/{order['id']}")[0], 404)

        # Taom bor, oshxonaga yuborilmagan - ofitsiant bekor qila oladi
        _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        self.waiter.call("POST", f"/api/orders/{order['id']}/items", {"product_id": products[0]["id"]})
        _, tables = self.waiter.call("GET", "/api/tables")
        self.assertIsNotNone(next(t for t in tables if t["id"] == table["id"])["order"])
        status, res = self.waiter.call("POST", f"/api/orders/{order['id']}/discard")
        self.assertEqual((status, res["deleted"]), (200, False))
        _, detail = self.admin.call("GET", f"/api/orders/{order['id']}")
        self.assertEqual(detail["status"], "cancelled")

        # Oshxonaga yuborilgan - ofitsiant emas, faqat kassir
        _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        self.waiter.call("POST", f"/api/orders/{order['id']}/items", {"product_id": products[0]["id"]})
        self.waiter.call("POST", f"/api/orders/{order['id']}/kitchen-send")
        self.assertEqual(self.waiter.call("POST", f"/api/orders/{order['id']}/discard")[0], 403)
        self.assertEqual(self.admin.call("POST", f"/api/orders/{order['id']}/discard")[0], 200)

    def test_cancel_sale_refunds(self):
        _, prod = self.admin.call("POST", "/api/products", {"name": "Qaytariladigan", "price": 25000})
        order = self.new_order_with(prod["id"], prod["id"])
        _, paid = self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "card"})
        card = lambda b: next(a for a in b["accounts"] if a["account"] == "card")["balance"]
        _, bal_before = self.admin.call("GET", "/api/finance/balance")
        _, rep_before = self.admin.call("GET", "/api/reports")

        self.assertEqual(self.waiter.call("POST", f"/api/finance/sales/{order['id']}/cancel")[0], 403)
        self.assertEqual(self.admin.call("POST", f"/api/finance/sales/{order['id']}/cancel", {"reason": "xato chek"})[0], 200)
        self.assertEqual(self.admin.call("POST", f"/api/finance/sales/{order['id']}/cancel")[0], 409)

        _, bal_after = self.admin.call("GET", "/api/finance/balance")
        self.assertEqual(card(bal_before) - card(bal_after), paid["total"])  # kirgan pul chiqdi
        _, rep_after = self.admin.call("GET", "/api/reports")
        self.assertEqual(rep_before["summary"]["revenue"] - rep_after["summary"]["revenue"], paid["total"])
        _, lst = self.admin.call("GET", "/api/finance/entries?source=sales")
        row = next(e for e in lst["entries"] if e["id"] == order["id"])
        self.assertEqual((row["status"], row["cancel_reason"]), ("cancelled", "xato chek"))
        _, detail = self.admin.call("GET", f"/api/orders/{order['id']}")
        self.assertEqual(detail["status"], "refunded")  # o'chirilmadi, tarixda qoldi

    def test_crm_customers_and_debts(self):
        from datetime import date, timedelta
        today = date.today()
        # Mijoz: telefon normallashtiriladi va takrorlanmaydi
        status, ali = self.admin.call("POST", "/api/customers", {"name": "Ali Valiyev", "phone": "90 123-45-67", "gender": "m"})
        self.assertEqual((status, ali["phone"]), (200, "+998901234567"))
        self.assertEqual(self.admin.call("POST", "/api/customers", {"name": "Boshqa", "phone": "+998901234567", "gender": "f"})[0], 409)
        self.assertEqual(self.admin.call("POST", "/api/customers", {"name": "Ali", "phone": "12", "gender": "m"})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/customers", {"name": "Ali", "phone": "901112233", "gender": "x"})[0], 400)
        _, found = self.admin.call("GET", "/api/customers?q=4567")
        self.assertIn(ali["id"], [c["id"] for c in found])
        _, edited = self.admin.call("PUT", f"/api/customers/{ali['id']}", {"name": "Ali Valiyev", "phone": "901234567", "gender": "m"})
        self.assertEqual(edited["name"], "Ali Valiyev")

        # Qarzga sotish: mijoz va muddat shart
        _, prod = self.admin.call("POST", "/api/products", {"name": "Qarz taom", "price": 60000})
        order = self.new_order_with(prod["id"])
        self.assertEqual(self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "debt"})[0], 400)
        _, bal0 = self.admin.call("GET", "/api/finance/balance")
        status, paid = self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {
            "method": "debt", "customer_id": ali["id"], "due_date": str(today - timedelta(days=2))})
        self.assertEqual(status, 200)
        _, bal1 = self.admin.call("GET", "/api/finance/balance")
        self.assertEqual(bal1["total"], bal0["total"])  # qarzga sotuv kassaga pul keltirmaydi

        # Qo'lda qarz: 2 kundan keyin (vaqti keldi) va 10 kundan keyin (muddati bor)
        self.admin.call("POST", f"/api/customers/{ali['id']}/debts", {"amount": 20000, "due_date": str(today + timedelta(days=2))})
        self.admin.call("POST", f"/api/customers/{ali['id']}/debts", {"amount": 30000, "due_date": str(today + timedelta(days=10))})
        _, debts = self.admin.call("GET", "/api/debts")
        mine = {d["amount"]: d for d in debts["debts"] if d["customer_id"] == ali["id"]}
        self.assertEqual(mine[paid["total"]]["bucket"], "overdue")
        self.assertEqual(mine[20000]["bucket"], "due")
        self.assertEqual(mine[30000]["bucket"], "later")

        # To'lov usullari -> hisoblar: naqd->naqd, click->karta, terminal/ko'chirish->hisob raqam
        acc = lambda b, k: next(a for a in b["accounts"] if a["account"] == k)["balance"]
        overdue = mine[paid["total"]]
        self.assertEqual(self.admin.call("POST", f"/api/debts/{overdue['id']}/pay", {"amount": 10, "method": "bitcoin"})[0], 400)
        self.assertEqual(self.admin.call("POST", f"/api/debts/{overdue['id']}/pay", {"amount": 10**9, "method": "cash"})[0], 400)
        for method, account, amount in (("cash", "cash", 10000), ("click", "card", 20000),
                                        ("terminal", "bank", 5000), ("transfer", "bank", 5000)):
            _, before = self.admin.call("GET", "/api/finance/balance")
            _, res = self.admin.call("POST", f"/api/debts/{overdue['id']}/pay", {"amount": amount, "method": method})
            self.assertEqual(res["account"], account)
            _, after = self.admin.call("GET", "/api/finance/balance")
            self.assertEqual(acc(after, account) - acc(before, account), amount)

        # Qolganini to'lasa - qarz yopiladi va ro'yxatdan chiqadi
        left = paid["total"] - 40000
        _, res = self.admin.call("POST", f"/api/debts/{overdue['id']}/pay", {"method": "cash"})
        self.assertEqual(res["remaining"], 0)
        self.assertEqual(self.admin.call("POST", f"/api/debts/{overdue['id']}/pay", {"method": "cash"})[0], 409)
        _, debts = self.admin.call("GET", "/api/debts")
        self.assertNotIn(overdue["id"], [d["id"] for d in debts["debts"]])

        # Mijoz kartasi: qarz, to'langan, qoldiq; to'lovni bekor qilish qarzni qayta ochadi
        _, card = self.admin.call("GET", f"/api/customers/{ali['id']}")
        self.assertEqual(card["remaining"], 50000)
        last = card["payments"][0]
        self.assertEqual(last["amount"], left)
        self.assertEqual(self.admin.call("POST", f"/api/debt-payments/{last['id']}/cancel", {"reason": "xato"})[0], 200)
        self.assertEqual(self.admin.call("POST", f"/api/debt-payments/{last['id']}/cancel")[0], 409)
        _, card = self.admin.call("GET", f"/api/customers/{ali['id']}")
        self.assertEqual(card["remaining"], 50000 + left)

        # Qarzga sotilgan savdoni bekor qilish - to'lovlar bor ekan, avval ular bekor qilinadi
        self.assertEqual(self.admin.call("POST", f"/api/finance/sales/{order['id']}/cancel")[0], 409)
        # Ofitsiantda CRM ruxsati yo'q
        self.assertEqual(self.waiter.call("GET", "/api/debts")[0], 403)

    def upload(self, client, kind, content, name="fayl.xlsx"):
        return client.call("POST", f"/api/import/{kind}", {"file_name": name, "data": base64.b64encode(content).decode()})

    def test_import_template_and_products(self):
        import xlsx
        from urllib.request import Request
        # Shablon - haqiqiy xlsx, o'zimiz o'qiy olamiz
        req = Request(self.base + "/api/import/products/template")
        with self.admin.opener.open(req) as res:
            self.assertIn("spreadsheetml", res.headers["Content-Type"])
            template = res.read()
        headers, rows = xlsx.read_table(template)
        self.assertEqual(headers[:3], ["nomi", "kategoriya", "sotish narxi"])
        self.assertEqual(len(rows), 2)  # izoh qatorlari (#) o'tkazib yuboriladi

        content = xlsx.write_xlsx(
            ["Nomi*", "Kategoriya", "Sotish narxi*", "Tannarxi", "Printer"],
            [["Import Somsa", "Import Kategoriya", "8 000", "3000", ""],
             ["Import Manti", "Import Kategoriya", 12000, "", ""],
             ["", "X", 1000, "", ""],                       # nomi yo'q
             ["Import Xato", "", "abc", "", ""],             # narx son emas
             ["Import Printersiz", "", 5000, "", "Yo'q printer"]])
        status, res = self.upload(self.admin, "products", content)
        self.assertEqual(status, 200)
        self.assertEqual((res["created"], res["updated"]), (2, 0))
        self.assertEqual([e["row"] for e in res["errors"]], [4, 5, 6])
        _, products = self.admin.call("GET", "/api/products")
        somsa = next(p for p in products if p["name"] == "Import Somsa")
        self.assertEqual((somsa["price"], somsa["cost"], somsa["category_name"]), (8000, 3000, "Import Kategoriya"))

        # Qayta yuklansa - yangilanadi, dublikat bo'lmaydi
        again = xlsx.write_xlsx(["Nomi", "Sotish narxi"], [["import somsa", 9000]])
        _, res = self.upload(self.admin, "products", again)
        self.assertEqual((res["created"], res["updated"]), (0, 1))
        _, products = self.admin.call("GET", "/api/products")
        self.assertEqual(sum(1 for p in products if p["name"].lower() == "import somsa"), 1)

        # CSV ham qabul qilinadi; ustun yetishmasa - tushunarli xato
        _, res = self.upload(self.admin, "products", "Nomi;Sotish narxi\nImport CSV;7000\n".encode(), "a.csv")
        self.assertEqual(res["created"], 1)
        status, err = self.upload(self.admin, "products", xlsx.write_xlsx(["Nomi"], [["X"]]))
        self.assertEqual(status, 400)
        self.assertIn("Sotish narxi", err["error"])
        self.assertEqual(self.upload(self.admin, "products", b"not a real file at all")[0], 400)
        self.assertEqual(self.upload(self.waiter, "products", again)[0], 403)

    def test_import_customers(self):
        import xlsx
        content = xlsx.write_xlsx(["Ismi*", "Telefon*", "Jinsi*"], [
            ["Import Ali", "90 700 00 01", "Erkak"],
            ["Import Laylo", "+998 90 700 00 02", "ayol"],
            ["Import Xato", "12", "Erkak"],
            ["Import Jins", "90 700 00 03", "?"],
        ])
        _, res = self.upload(self.admin, "customers", content)
        self.assertEqual((res["created"], len(res["errors"])), (2, 2))
        _, found = self.admin.call("GET", "/api/customers?q=Import")
        self.assertEqual({c["name"]: c["gender"] for c in found}, {"Import Ali": "m", "Import Laylo": "f"})
        _, res = self.upload(self.admin, "customers", xlsx.write_xlsx(["Ismi", "Telefon", "Jinsi"], [["Ali Yangi", "907000001", "E"]]))
        self.assertEqual(res["updated"], 1)

    def test_set_balances(self):
        _, b = self.admin.call("GET", "/api/balances")
        cash = next(a for a in b["accounts"]["accounts"] if a["account"] == "cash")["balance"]
        # Kassa: yangi balans o'rnatiladi, farq tuzatish yozuvi bo'ladi
        _, r = self.admin.call("POST", "/api/balances/account", {"account": "cash", "balance": cash + 500000, "comment": "Boshlang'ich qoldiq"})
        self.assertEqual((r["old"], r["new"]), (cash, cash + 500000))
        self.admin.call("POST", "/api/balances/account", {"account": "cash", "balance": cash + 400000})
        _, b = self.admin.call("GET", "/api/balances")
        self.assertEqual(next(a for a in b["accounts"]["accounts"] if a["account"] == "cash")["balance"], cash + 400000)
        self.assertEqual(self.admin.call("POST", "/api/balances/account", {"account": "cash", "balance": cash + 400000})[0], 400)
        # Tuzatish turlari kirim/chiqim oynasida tanlanmaydi (is_adjust)
        _, types = self.admin.call("GET", "/api/finance/types")
        self.assertEqual(sum(t["is_adjust"] for t in types), 2)

        # Mijoz: qarz 0 -> 80000 -> 30000
        _, c = self.admin.call("POST", "/api/customers", {"name": "Balans Mijoz", "phone": "907771122", "gender": "m"})
        self.admin.call("POST", "/api/balances/customer", {"customer_id": c["id"], "balance": 80000})
        _, r = self.admin.call("POST", "/api/balances/customer", {"customer_id": c["id"], "balance": 30000})
        self.assertEqual((r["old"], r["new"]), (80000, 30000))
        _, card = self.admin.call("GET", f"/api/customers/{c['id']}")
        self.assertEqual(card["remaining"], 30000)
        self.assertEqual(self.admin.call("POST", "/api/balances/customer", {"customer_id": c["id"], "balance": -5})[0], 400)
        _, b2 = self.admin.call("GET", "/api/balances")
        self.assertEqual(b2["accounts"]["total"], b["accounts"]["total"])  # mijoz tuzatishi kassaga ta'sir qilmaydi

        # Ta'minotchi: yaratish (dublikatsiz), balans, chiqim bilan kamayadi
        _, sup = self.admin.call("POST", "/api/suppliers", {"name": "Go'sht do'koni", "phone": "901112233"})
        self.assertEqual(self.admin.call("POST", "/api/suppliers", {"name": "go'sht  DO'KONI"})[0], 409)
        self.admin.call("POST", "/api/balances/supplier", {"supplier_id": sup["id"], "balance": 1000000})
        _, types = self.admin.call("GET", "/api/finance/types")
        pay_type = next(t for t in types if t["name"] == "Ta'minotchiga pul berish")
        self.admin.call("POST", "/api/finance/entries", {"type_id": pay_type["id"], "account": "cash", "amount": 250000, "supplier_id": sup["id"]})
        _, sups = self.admin.call("GET", "/api/suppliers")
        self.assertEqual(next(x for x in sups if x["id"] == sup["id"])["balance"], 750000)
        _, b3 = self.admin.call("GET", "/api/balances")
        self.assertEqual([h["target"] for h in b3["history"][:4]], ["supplier", "customer", "customer", "account"])
        self.assertEqual(self.cashier.call("GET", "/api/balances")[0], 403)

    def test_admin_cannot_demote_self(self):
        _, me = self.admin.call("GET", "/api/me")
        status, _ = self.admin.call("PUT", f"/api/users/{me['id']}", {"full_name": "A", "role": "waiter"})
        self.assertEqual(status, 400)

    def test_journal_records_actions(self):
        table = self.free_table()
        _, products = self.waiter.call("GET", "/api/products")
        _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        self.waiter.call("POST", f"/api/orders/{order['id']}/items", {"product_id": products[0]["id"], "qty": 2})
        status, paid = self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "cash"})
        self.assertEqual(status, 200)

        status, j = self.admin.call("GET", "/api/journal?category=sales")
        self.assertEqual(status, 200)
        sale = next(i for i in j["items"] if f"#{order['id']} " in i["summary"])
        self.assertEqual(sale["title"], "Sotuv")
        self.assertEqual(sale["user_name"], "Kassir")
        _, detail = self.admin.call("GET", f"/api/journal/{sale['id']}")
        fields = dict(map(tuple, detail["details"]["fields"]))
        self.assertEqual(fields["To'lov usuli"], "Naqd")
        self.assertEqual(detail["details"]["items"][0]["qty"], 2)

        _, j = self.admin.call("GET", "/api/journal?category=orders&q=" + quote(products[0]["name"]))
        self.assertTrue(any(i["title"] == "Buyurtmaga taom qo'shildi" for i in j["items"]))
        # login ham yoziladi, parol esa hech qayerda saqlanmaydi
        _, j = self.admin.call("GET", "/api/journal?category=auth")
        self.assertTrue(j["items"])
        self.admin.call("POST", "/api/users", {"username": "jurnal_test", "full_name": "Jurnal Test",
                                               "role": "waiter", "password": "sirli-parol"})
        _, j = self.admin.call("GET", "/api/journal?category=users")
        _, detail = self.admin.call("GET", f"/api/journal/{j['items'][0]['id']}")
        self.assertNotIn("sirli-parol", json.dumps(detail))
        self.assertEqual(detail["details"]["request"]["password"], "•••")
        # ruxsatsiz - yo'q
        status, _ = self.cashier.call("GET", "/api/journal")
        self.assertEqual(status, 403)

    def test_journal_product_changes(self):
        _, p = self.admin.call("POST", "/api/products", {"name": "Jurnal somsa", "price": 8000})
        self.admin.call("PUT", f"/api/products/{p['id']}", {"name": "Jurnal somsa", "price": 9000})
        self.admin.call("DELETE", f"/api/products/{p['id']}")
        _, j = self.admin.call("GET", "/api/journal?q=Jurnal%20somsa")
        titles = [i["title"] for i in j["items"]]
        self.assertEqual(titles[:3], ["Mahsulot o'chirildi", "Mahsulot o'zgartirildi", "Mahsulot qo'shildi"])
        self.assertIn("8 000 so'm → 9 000 so'm", j["items"][1]["summary"])
        # xato bilan tugagan amal jurnalga yozilmaydi
        status, _ = self.admin.call("POST", "/api/products", {"name": "", "price": 1})
        self.assertEqual(status, 400)
        _, after = self.admin.call("GET", "/api/journal?category=menu")
        self.assertEqual(after["items"][0]["title"], "Mahsulot o'chirildi")

    def test_telegram_integration(self):
        import server
        import telegram
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs

        sent = []

        class FakeTelegram(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = parse_qs(self.rfile.read(int(self.headers["Content-Length"] or 0)).decode())
                method = self.path.rsplit("/", 1)[-1]
                if "/botBAD" in self.path or "000:" in self.path:
                    result = {"ok": False, "description": "Unauthorized"}
                elif method == "getMe":
                    result = {"ok": True, "result": {"id": 1, "username": "cafe_test_bot", "first_name": "Cafe"}}
                elif method == "getUpdates":
                    result = {"ok": True, "result": [{"message": {"chat": {"id": 555111, "type": "private", "first_name": "Ali"}}}]}
                else:
                    sent.append(body)
                    result = {"ok": True, "result": {}}
                raw = json.dumps(result).encode()
                self.send_response(200 if result["ok"] else 401)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        old_api, telegram.API = telegram.API, f"http://127.0.0.1:{fake.server_address[1]}"
        token = "123456789:AAH" + "x" * 32
        try:
            status, _ = self.cashier.call("GET", "/api/integrations/telegram")
            self.assertEqual(status, 403)
            status, bot = self.admin.call("POST", "/api/integrations/telegram/check", {"token": token})
            self.assertEqual((status, bot["username"]), (200, "cafe_test_bot"))
            status, err = self.admin.call("POST", "/api/integrations/telegram/check", {"token": "000:" + "y" * 30})
            self.assertEqual(status, 502)
            self.assertIn("noto'g'ri", err["error"])
            _, chats = self.admin.call("POST", "/api/integrations/telegram/chats", {"token": token})
            self.assertEqual(chats[0]["id"], 555111)
            # chatsiz yoqib bo'lmaydi
            status, _ = self.admin.call("PUT", "/api/integrations/telegram", {"enabled": True, "token": token, "chats": []})
            self.assertEqual(status, 400)
            status, cfg = self.admin.call("PUT", "/api/integrations/telegram", {
                "enabled": True, "token": token, "chats": [{"id": 555111, "title": "Ali"}],
                "categories": ["sales", "finance"]})
            self.assertEqual(status, 200)
            self.assertNotIn(token, json.dumps(cfg))  # token to'liq qaytarilmaydi
            self.assertTrue(cfg["token_set"])
            _, settings = self.admin.call("GET", "/api/settings")
            self.assertNotIn(token, json.dumps(settings))
            # token kiritilmasa - eskisi qoladi
            status, cfg = self.admin.call("PUT", "/api/integrations/telegram", {
                "enabled": True, "chats": cfg["chats"], "categories": ["sales", "finance"]})
            self.assertEqual((status, cfg["token_set"]), (200, True))

            status, res = self.admin.call("POST", "/api/integrations/telegram/test", {})
            self.assertEqual(status, 200)
            self.assertTrue(res[0]["ok"])
            sent.clear()
            _, types = self.admin.call("GET", "/api/finance/types")
            ftype = next(t for t in types if t["direction"] == "in")
            self.admin.call("POST", "/api/finance/entries", {"type_id": ftype["id"], "account": "cash",
                                                             "amount": 77000, "comment": "Telegram sinov"})
            self.admin.call("POST", "/api/categories", {"name": "Telegramga bormaydi"})  # menu tanlanmagan
            for _ in range(50):
                if sent:
                    break
                time.sleep(0.05)
            telegram.notifier.queue.join()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["chat_id"], ["555111"])
            self.assertIn("77 000 so'm", sent[0]["text"][0])
            self.assertIn("Telegram sinov", sent[0]["text"][0])

            # soddalashtirilgan ulash: uzish -> token bilan ulash -> /start yozganlar o'zi qo'shiladi
            _, cfg = self.admin.call("DELETE", "/api/integrations/telegram")
            self.assertFalse(cfg["token_set"])
            status, _ = self.admin.call("POST", "/api/integrations/telegram/connect", {"token": "yomon"})
            self.assertEqual(status, 400)
            status, cfg = self.admin.call("POST", "/api/integrations/telegram/connect", {"token": token})
            self.assertEqual(status, 200)
            self.assertEqual((cfg["token_set"], cfg["chats"], cfg["enabled"]), (True, [], False))
            self.assertEqual(cfg["bot"]["username"], "cafe_test_bot")
            sent.clear()
            status, cfg = self.admin.call("POST", "/api/integrations/telegram/link", {})
            self.assertEqual((status, cfg["added"], cfg["enabled"]), (200, ["Ali"], True))
            self.assertEqual(len(sent), 1)  # salom xabari
            _, cfg = self.admin.call("POST", "/api/integrations/telegram/link", {})
            self.assertEqual(cfg["added"], [])  # ikkinchi marta qo'shilmaydi
            self.assertEqual(len(cfg["chats"]), 1)
        finally:
            self.admin.call("PUT", "/api/integrations/telegram", {"enabled": False})
            telegram.API = old_api
            fake.shutdown()
            fake.server_close()

    def test_customer_bot(self):
        import server
        import telegram
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs

        sent = []

        class FakeTelegram(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = parse_qs(self.rfile.read(int(self.headers["Content-Length"] or 0)).decode())
                method = self.path.rsplit("/", 1)[-1]
                if method == "getMe":
                    result = {"ok": True, "result": {"id": 77, "username": "kafe_mijoz_bot", "first_name": "Kafe"}}
                else:
                    sent.append({k: v[0] for k, v in body.items()})
                    result = {"ok": True, "result": {}}
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        def wait_sent(n):
            for _ in range(60):
                if len(sent) >= n:
                    break
                time.sleep(0.05)
            telegram.customer_notifier.queue.join()

        fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        old_api, telegram.API = telegram.API, f"http://127.0.0.1:{fake.server_address[1]}"
        token = "987654321:BBH" + "z" * 32
        try:
            status, _ = self.admin.call("POST", "/api/customers/message", {"all": True, "text": "salom"})
            self.assertEqual(status, 400)  # bot ulanmagan
            status, cfg = self.admin.call("POST", "/api/integrations/customer-bot/connect", {"token": token})
            self.assertEqual(status, 200)
            self.assertEqual((cfg["enabled"], cfg["bot"]["username"]), (True, "kafe_mijoz_bot"))

            _, c = self.admin.call("POST", "/api/customers", {"name": "Bot Mijoz", "phone": "+998 97 111 22 33", "gender": "f"})

            def reply(update):
                with server.db_lock:
                    return server.customer_bot_reply(self.server.conn, update)
            chat = {"id": 4242, "type": "private"}
            r = reply({"message": {"chat": chat, "from": {"id": 4242}, "text": "/start"}})
            self.assertTrue(r[0][2]["keyboard"][0][0]["request_contact"])
            r = reply({"message": {"chat": chat, "from": {"id": 4242},
                                   "contact": {"phone_number": "998971112233", "user_id": 999}}})
            self.assertIn("o'zingizning", r[0][1])  # boshqa odamning raqami
            r = reply({"message": {"chat": chat, "from": {"id": 4242},
                                   "contact": {"phone_number": "998900000000", "user_id": 4242}}})
            self.assertIn("topilmadi", r[0][1])
            r = reply({"message": {"chat": chat, "from": {"id": 4242},
                                   "contact": {"phone_number": "998971112233", "user_id": 4242}}})
            self.assertIn("Bot Mijoz", r[0][1])
            self.assertIn("Qarzingiz yo'q", r[1][1])
            _, linked = self.admin.call("GET", "/api/customers?telegram=1")
            self.assertEqual([x["id"] for x in linked], [c["id"]])

            # savdoda mijoz tanlansa - chek botga boradi
            table = self.free_table()
            _, products = self.waiter.call("GET", "/api/products")
            _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
            self.waiter.call("POST", f"/api/orders/{order['id']}/items", {"product_id": products[0]["id"], "qty": 2})
            status, paid = self.cashier.call("POST", f"/api/orders/{order['id']}/pay",
                                             {"method": "cash", "customer_id": c["id"]})
            self.assertEqual(status, 200)
            self.assertTrue(paid["customer_notified"])
            wait_sent(1)
            self.assertEqual(sent[-1]["chat_id"], "4242")
            self.assertIn(f"Buyurtma #{order['id']}", sent[-1]["text"])
            self.assertIn(products[0]["name"], sent[-1]["text"])

            # qarz va balans
            self.admin.call("POST", f"/api/customers/{c['id']}/debts", {"amount": 45000, "due_date": "2030-01-01"})
            r = reply({"message": {"chat": chat, "from": {"id": 4242}, "text": server.BTN_BALANCE}})
            self.assertIn("45 000 so'm", r[0][1])
            r = reply({"message": {"chat": chat, "from": {"id": 4242}, "text": server.BTN_ORDERS}})
            self.assertIn(f"#{order['id']}", r[0][1])
            _, detail = self.admin.call("GET", f"/api/customers/{c['id']}")
            debt_id = next(d["id"] for d in detail["debts"] if d["remaining"] > 0)
            self.admin.call("POST", f"/api/debts/{debt_id}/pay", {"method": "cash", "amount": 5000})
            wait_sent(2)
            self.assertIn("40 000 so'm", sent[-1]["text"])

            # ommaviy xabar
            sent.clear()
            status, res = self.admin.call("POST", "/api/customers/message", {"all": True, "text": "Ertaga 20% chegirma!"})
            self.assertEqual((status, res["sent"]), (200, 1))
            wait_sent(1)
            self.assertIn("Ertaga 20% chegirma!", sent[-1]["text"])
            status, _ = self.waiter.call("POST", "/api/customers/message", {"all": True, "text": "x x"})
            self.assertEqual(status, 403)  # ofitsiantda CRM ruxsati yo'q
        finally:
            self.admin.call("DELETE", "/api/integrations/customer-bot")
            telegram.API = old_api
            fake.shutdown()
            fake.server_close()

    def test_sales_receipts_return(self):
        table = self.free_table()
        _, products = self.waiter.call("GET", "/api/products")
        p1, p2 = products[0], products[1]
        _, order = self.waiter.call("POST", "/api/orders", {"type": "dine_in", "table_id": table["id"]})
        self.waiter.call("POST", f"/api/orders/{order['id']}/items", {"product_id": p1["id"], "qty": 3})
        self.waiter.call("POST", f"/api/orders/{order['id']}/items", {"product_id": p2["id"], "qty": 1})
        _, paid = self.cashier.call("POST", f"/api/orders/{order['id']}/pay", {"method": "cash"})
        _, before = self.admin.call("GET", "/api/reports")
        _, bal0 = self.admin.call("GET", "/api/finance/balance")

        status, sales = self.admin.call("GET", "/api/sales")
        self.assertEqual(status, 200)
        self.assertIn(order["id"], [x["id"] for x in sales["sales"]])
        status, sale = self.admin.call("GET", f"/api/sales/{order['id']}")
        self.assertEqual((status, sale["can_manage"]), (200, True))
        item1 = next(i for i in sale["items"] if i["name"] == p1["name"])

        # ko'p qaytarib bo'lmaydi
        status, _ = self.cashier.call("POST", f"/api/sales/{order['id']}/return", {"items": [{"item_id": item1["id"], "qty": 4}]})
        self.assertEqual(status, 400)
        status, _ = self.waiter.call("POST", f"/api/sales/{order['id']}/return", {"items": [{"item_id": item1["id"], "qty": 1}]})
        self.assertEqual(status, 403)
        status, ret = self.cashier.call("POST", f"/api/sales/{order['id']}/return",
                                        {"items": [{"item_id": item1["id"], "qty": 1}], "reason": "sovuq edi"})
        self.assertEqual(status, 200)
        self.assertEqual(ret["amount"], round(p1["price"] * paid["total"] / paid["subtotal"]))

        _, after = self.admin.call("GET", "/api/reports")
        self.assertEqual(after["summary"]["revenue"], before["summary"]["revenue"] - ret["amount"])
        _, bal1 = self.admin.call("GET", "/api/finance/balance")
        cash = lambda b: next(a["balance"] for a in b["accounts"] if a["account"] == "cash")
        self.assertEqual(cash(bal1), cash(bal0) - ret["amount"])
        _, sale = self.admin.call("GET", f"/api/sales/{order['id']}")
        self.assertEqual(sale["returned"], ret["amount"])
        self.assertEqual(sale["returns"][0]["reason"], "sovuq edi")
        _, entries = self.admin.call("GET", "/api/finance/entries?source=sales")
        self.assertTrue(any(e["source"] == "return" and e["amount"] == ret["amount"] for e in entries["entries"]))
        _, only = self.admin.call("GET", "/api/sales?status=returned")
        self.assertEqual([x["id"] for x in only["sales"]], [order["id"]])

        # qolganini bekor qilish: savdo butunlay hisobdan chiqadi
        status, _ = self.cashier.call("POST", f"/api/finance/sales/{order['id']}/cancel", {"reason": "xato"})
        self.assertEqual(status, 200)
        _, bal2 = self.admin.call("GET", "/api/finance/balance")
        self.assertEqual(cash(bal2), cash(bal0) - paid["total"])
        status, _ = self.cashier.call("POST", f"/api/sales/{order['id']}/return", {"items": [{"item_id": item1["id"], "qty": 1}]})
        self.assertEqual(status, 409)

    def test_pin_login(self):
        import server
        # administrator standart PIN bilan kiradi
        status, me = Client(self.base).call("POST", "/api/login", {"pin": "1234"})
        self.assertEqual((status, me["username"]), (200, "admin"))
        # xodim faqat PIN bilan yaratiladi
        status, u = self.admin.call("POST", "/api/users", {"username": "pinli", "first_name": "Pinli",
                                                          "role": "waiter", "pin": "4321"})
        self.assertEqual(status, 200)
        status, me = Client(self.base).call("POST", "/api/login", {"pin": "4321"})
        self.assertEqual((status, me["username"]), (200, "pinli"))
        # bir xil PIN ikki xodimda bo'lmaydi, format tekshiriladi
        status, _ = self.admin.call("PUT", f"/api/users/{u['id']}", {"first_name": "Pinli", "role": "waiter", "pin": "1234"})
        self.assertEqual(status, 409)
        status, _ = self.admin.call("PUT", f"/api/users/{u['id']}", {"first_name": "Pinli", "role": "waiter", "pin": "12a4"})
        self.assertEqual(status, 400)
        self.admin.call("PUT", f"/api/users/{u['id']}", {"first_name": "Pinli", "role": "waiter", "pin": "5555"})
        self.assertEqual(Client(self.base).call("POST", "/api/login", {"pin": "4321"})[0], 401)
        _, users = self.admin.call("GET", "/api/users")
        self.assertTrue(next(x for x in users if x["id"] == u["id"])["has_pin"])
        self.assertNotIn("pin_lookup", json.dumps(users))
        _, settings = self.admin.call("GET", "/api/settings")
        self.assertNotIn("_pin_secret", settings)
        # 5 ta xatodan keyin kutish kerak
        server.LOGIN_GUARD.ok("127.0.0.1")
        for _ in range(4):
            Client(self.base).call("POST", "/api/login", {"pin": "0000"})
        status, err = Client(self.base).call("POST", "/api/login", {"pin": "0000"})
        self.assertEqual(status, 401)
        status, err = Client(self.base).call("POST", "/api/login", {"pin": "5555"})
        self.assertEqual(status, 429)
        server.LOGIN_GUARD.ok("127.0.0.1")
        self.assertEqual(Client(self.base).call("POST", "/api/login", {"pin": "5555"})[0], 200)
        server.LOGIN_GUARD.ok("127.0.0.1")

    def test_no_duplicates(self):
        a = self.admin
        _, cat = a.call("POST", "/api/categories", {"name": "Dublikat kategoriya"})
        self.assertEqual(a.call("POST", "/api/categories", {"name": "  dublikat   KATEGORIYA "})[0], 409)
        _, cat2 = a.call("POST", "/api/categories", {"name": "Boshqa kategoriya"})
        self.assertEqual(a.call("PUT", f"/api/categories/{cat2['id']}", {"name": "Dublikat kategoriya"})[0], 409)
        self.assertEqual(a.call("PUT", f"/api/categories/{cat['id']}", {"name": "Dublikat kategoriya"})[0], 200)

        _, p = a.call("POST", "/api/products", {"name": "Dublikat taom", "price": 1000})
        status, err = a.call("POST", "/api/products", {"name": "dublikat taom", "price": 2000})
        self.assertEqual(status, 409)
        self.assertIn("allaqachon bor", err["error"])
        _, p2 = a.call("POST", "/api/products", {"name": "Boshqa taom", "price": 1000})
        self.assertEqual(a.call("PUT", f"/api/products/{p2['id']}", {"name": "Dublikat taom", "price": 1})[0], 409)
        # o'chirilgan mahsulot nomi qayta ishlatilishi mumkin
        a.call("DELETE", f"/api/products/{p['id']}")
        self.assertEqual(a.call("POST", "/api/products", {"name": "Dublikat taom", "price": 1000})[0], 200)

        _, hall = a.call("POST", "/api/halls", {"name": "Dublikat zal"})
        self.assertEqual(a.call("POST", "/api/halls", {"name": "dublikat zal"})[0], 409)
        self.assertEqual(a.call("POST", "/api/tables", {"name": "D1", "hall_id": hall["id"]})[0], 200)
        self.assertEqual(a.call("POST", "/api/tables", {"name": "d1", "hall_id": hall["id"]})[0], 409)

        self.assertEqual(a.call("POST", "/api/suppliers", {"name": "Dublikat ta'minotchi", "phone": "90 700 11 22"})[0], 200)
        self.assertEqual(a.call("POST", "/api/suppliers", {"name": "dublikat ta'minotchi"})[0], 409)
        self.assertEqual(a.call("POST", "/api/suppliers", {"name": "Boshqa nom", "phone": "+998 90 700 11 22"})[0], 409)

        self.assertEqual(a.call("POST", "/api/customers", {"name": "Dub Mijoz", "phone": "+998 93 700 11 22", "gender": "m"})[0], 200)
        self.assertEqual(a.call("POST", "/api/customers", {"name": "Boshqa", "phone": "937001122", "gender": "m"})[0], 409)

        self.assertEqual(a.call("POST", "/api/users", {"first_name": "Dub", "phone": "+998 94 700 11 22",
                                                       "role": "staff", "permissions": ["tables"], "pin": "6061"})[0], 200)
        self.assertEqual(a.call("POST", "/api/users", {"first_name": "Dub2", "phone": "94 700 11 22",
                                                       "role": "staff", "permissions": ["tables"], "pin": "6062"})[0], 409)

    def test_receipt_settings(self):
        _, s = self.admin.call("GET", "/api/settings")
        self.assertTrue(s["receipt"]["show_cafe_name"])
        self.assertEqual(s["receipt"]["footer_text"], "Xaridingiz uchun rahmat!")
        status, s = self.admin.call("PUT", "/api/settings", {"receipt": {
            "show_waiter": False, "show_logo": True, "header_text": " Tel: 90 123 ", "paper_width": "58", "junk": 1}})
        self.assertEqual(status, 200)
        r = s["receipt"]
        self.assertEqual((r["show_waiter"], r["show_logo"], r["header_text"], r["paper_width"]), (False, True, "Tel: 90 123", 58))
        self.assertNotIn("junk", r)
        self.assertEqual(self.admin.call("PUT", "/api/settings", {"receipt": {"footer_text": "x" * 400}})[0], 400)
        self.assertEqual(self.waiter.call("PUT", "/api/settings", {"receipt": {}})[0], 403)
        _, j = self.admin.call("GET", "/api/journal?category=settings")
        self.assertIn("Chek", json.dumps(j["items"][0], ensure_ascii=False))
        self.admin.call("PUT", "/api/settings", {"receipt": {}})  # standartga qaytarish

    def test_cancel_finance_entry_without_order(self):
        _, types = self.admin.call("GET", "/api/finance/types")
        ftype = next(t for t in types if t["direction"] == "in")
        _, e = self.admin.call("POST", "/api/finance/entries", {"type_id": ftype["id"], "account": "cash", "amount": 1000})
        for _ in range(3):  # id ni buyurtmalar sonidan kattaroq qilamiz
            _, e = self.admin.call("POST", "/api/finance/entries", {"type_id": ftype["id"], "account": "cash", "amount": 1000})
        status, _ = self.admin.call("POST", f"/api/finance/entries/{e['id'] + 100000 - 100000}/cancel", {"reason": "x"})
        self.assertEqual(status, 200)



class PrintingTest(unittest.TestCase):
    def test_scan_finds_open_port(self):
        import printing

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            found = printing.scan_network(port=port, hosts=["127.0.0.1", "127.0.0.2"])
        finally:
            listener.close()
        self.assertEqual(found, [{"address": "127.0.0.1", "port": port}])

    def test_connection_type(self):
        import printing

        self.assertEqual(printing.connection_type("USB001"), "USB")
        self.assertEqual(printing.connection_type("WSD-1234"), "Wi-Fi / tarmoq")
        self.assertEqual(printing.connection_type("192.168.1.50"), "Wi-Fi / tarmoq")

    def test_virtual_printers_hidden(self):
        import printing

        fake = [
            {"name": "XP-80C", "port": "USB001", "connection": "USB"},
            {"name": "Microsoft Print to PDF", "port": "PORTPROMPT:", "connection": ""},
            {"name": "Fax", "port": "SHRFAX:", "connection": ""},
        ]
        original = printing._cups_printers
        printing._cups_printers = lambda: fake
        try:
            if os.name != "nt":
                self.assertEqual([p["name"] for p in printing.list_system_printers()], ["XP-80C"])
        finally:
            printing._cups_printers = original


class MigrationTest(unittest.TestCase):
    def test_old_database_is_upgraded(self):
        import server

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            # Birinchi versiyadagi baza (zallar, printerlar yo'q)
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE tables (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    seats INTEGER NOT NULL DEFAULT 4, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER,
                    name TEXT NOT NULL, price INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
                    product_id INTEGER, name TEXT NOT NULL, price INTEGER NOT NULL, qty INTEGER NOT NULL);
                INSERT INTO tables (name) VALUES ('Stol 1'), ('Stol 2');
                INSERT INTO products (name, price) VALUES ('Choy', 5000);
            """)
            conn.close()

            old_path, server.DB_PATH = server.DB_PATH, path
            try:
                conn = server.connect()
                server.init_db(conn)
                server.init_db(conn)  # ikkinchi marta ham xatosiz
                names = {r["name"]: r["hall_id"] for r in conn.execute("SELECT * FROM tables")}
                main = conn.execute("SELECT id FROM halls WHERE name = 'Asosiy zal'").fetchone()[0]
                self.assertEqual(names["Stol 1"], main)
                self.assertIn("Kabina 1", names)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM halls").fetchone()[0], 3)
                self.assertEqual(conn.execute("SELECT name FROM products").fetchone()[0], "Choy")
                conn.close()
            finally:
                server.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
