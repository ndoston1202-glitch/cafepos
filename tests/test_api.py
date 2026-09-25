"""API testlari:  python -m unittest discover tests"""

import json
import os
import sys
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
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

    def test_admin_cannot_demote_self(self):
        _, me = self.admin.call("GET", "/api/me")
        status, _ = self.admin.call("PUT", f"/api/users/{me['id']}", {"full_name": "A", "role": "waiter"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
