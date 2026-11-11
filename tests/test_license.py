"""Litsenziya testlari:  python -m unittest discover tests"""

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import licensing  # noqa: E402
from licensing import _G, _L, _P, _mul  # noqa: E402

DAY = 86400
T0 = 1_800_000_000  # testlardagi "hozir"


# --- test uchun imzolash (dasturning o'zida faqat tekshirish bor)

def _compress(p):
    zinv = pow(p[2], _P - 2, _P)
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def keypair(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    return (a, h[32:]), _compress(_mul(a, _G))


def sign(secret, public, msg):
    a, prefix = secret
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    big_r = _compress(_mul(r, _G))
    k = int.from_bytes(hashlib.sha512(big_r + public + msg).digest(), "little") % _L
    return big_r + ((r + k * a) % _L).to_bytes(32, "little")


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


SECRET, PUBLIC = keypair(b"\x01" * 32)
KEY = "CAFE-AAAA-BBBB-CCCC-DDDD"
DEVICE = "device-test-0001"


def make_token(now, expires, key=KEY, device=DEVICE, grace=7 * DAY, secret=SECRET):
    payload = {"v": 1, "key": key, "device_id": device, "customer": "Test Kafe", "plan": "monthly",
               "expires_at": iso(expires), "offline_until": iso(min(expires, now + grace)), "issued_at": iso(now)}
    body = b64url(json.dumps(payload).encode())
    return body + "." + b64url(sign(secret, PUBLIC, body.encode()))


class FakeServer:
    """Adminka o'rnida: kalit holatini qo'lda boshqaramiz."""

    def __init__(self, clock):
        self.clock = clock
        self.expires = T0 + 30 * DAY
        self.device = None
        self.blocked = False
        self.down = False
        self.calls = []

    def post(self, url, payload):
        action = url.rsplit("/", 1)[1]
        self.calls.append(action)
        if self.down:
            raise licensing._Network("aloqa yo'q")
        if payload["key"] != KEY:
            return 404, {"ok": False, "code": "invalid_key"}
        if self.blocked:
            return 403, {"ok": False, "code": "blocked"}
        if self.device is None:
            if action != "activate":
                return 409, {"ok": False, "code": "not_activated"}
            self.device = payload["device_id"]
        elif self.device != payload["device_id"]:
            return 409, {"ok": False, "code": "device_mismatch"}
        now = self.clock()
        if self.expires <= now:
            return 402, {"ok": False, "code": "expired", "expires_at": iso(self.expires)}
        return 200, {"ok": True, "token": make_token(now, self.expires, device=payload["device_id"])}


class Ed25519Test(unittest.TestCase):
    def test_rfc8032_vector(self):
        pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertTrue(licensing.ed25519_verify(pub, b"", sig))
        self.assertFalse(licensing.ed25519_verify(pub, b"x", sig))
        self.assertFalse(licensing.ed25519_verify(pub, b"", sig[:-1] + b"\x00"))

    def test_token(self):
        token = make_token(T0, T0 + DAY)
        self.assertEqual(licensing.verify_token(token, PUBLIC)["key"], KEY)
        body, sig = token.split(".")
        fake = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        fake["expires_at"] = "2099-01-01T00:00:00.000Z"
        forged = b64url(json.dumps(fake).encode()) + "." + sig
        self.assertIsNone(licensing.verify_token(forged, PUBLIC))
        self.assertIsNone(licensing.verify_token("bema'ni", PUBLIC))
        other_secret, _ = keypair(b"\x02" * 32)
        self.assertIsNone(licensing.verify_token(make_token(T0, T0 + DAY, secret=other_secret), PUBLIC))


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = T0
        self.srv = FakeServer(lambda: self.now)
        self.path = os.path.join(self.tmp.name, "license.json")
        self.lic = self.manager()

    def manager(self, device=DEVICE):
        return licensing.LicenseManager(path=self.path, server_url="http://x", public_key=PUBLIC, device=device,
                                        clock=lambda: self.now, post=self.srv.post)

    def test_activate_and_persist(self):
        st = self.lic.status()
        self.assertEqual((st["ok"], st["state"], st["needs_key"]), (False, "none", True))
        with self.assertRaises(licensing.LicenseError):
            self.lic.activate("salom")
        with self.assertRaises(licensing.LicenseError):
            self.lic.activate("CAFE-XXXX-XXXX-XXXX-XXXX")
        self.assertEqual(self.lic.status()["state"], "none")  # noto'g'ri urinish hech narsani o'zgartirmaydi

        st = self.lic.activate("cafe aaaa bbbb cccc dddd")
        self.assertTrue(st["ok"])
        self.assertEqual((st["customer"], st["days_left"], st["warn"]), ("Test Kafe", 30, False))
        self.assertEqual(st["key"], "CAFE-AAAA-****-****-DDDD")
        self.assertTrue(self.manager().status()["ok"])  # qayta ishga tushganda fayldan o'qiladi
        self.assertEqual(self.manager(device="boshqa-kompyuter").status()["state"], "device_mismatch")

    def test_expiry_and_renewal(self):
        self.lic.activate(KEY)
        self.now = T0 + 26 * DAY
        self.srv.calls.clear()
        st = self.lic.refresh()
        self.assertEqual((st["ok"], st["warn"], st["days_left"]), (True, True, 4))
        self.now = T0 + 30 * DAY + 60
        self.assertEqual(self.lic.status()["state"], "expired")  # internetsiz ham muddat tugaydi
        self.assertEqual(self.lic.refresh()["state"], "expired")
        self.srv.expires += 30 * DAY  # admin to'lovni qabul qildi
        st = self.lic.refresh()
        self.assertTrue(st["ok"])
        self.assertEqual(st["days_left"], 30)

    def test_offline_grace(self):
        self.lic.activate(KEY)
        self.srv.down = True
        self.now = T0 + 6 * DAY
        self.assertTrue(self.lic.refresh()["ok"])  # aloqa yo'q, lekin 7 kun ichida
        self.now = T0 + 7 * DAY + 60
        self.assertEqual(self.lic.refresh()["state"], "need_online")
        self.srv.down = False
        self.assertTrue(self.lic.refresh()["ok"])

    def test_clock_rollback(self):
        self.lic.activate(KEY)
        self.now = T0 + 3 * DAY
        self.assertTrue(self.lic.status()["ok"])
        self.now = T0 + 1 * DAY  # soat orqaga surildi
        self.assertEqual(self.lic.status()["state"], "clock")
        self.now = T0 - 2 * DAY
        self.assertEqual(self.lic.status()["state"], "clock")

    def test_block_and_device_reset(self):
        self.lic.activate(KEY)
        self.srv.blocked = True
        self.assertEqual(self.lic.refresh()["state"], "blocked")
        self.assertEqual(self.manager().status()["state"], "blocked")  # qayta ishga tushirish yordam bermaydi
        self.srv.blocked = False
        self.assertTrue(self.lic.refresh()["ok"])

        # Admin qurilmani uzdi: eski kompyuter kalitni o'zi qayta egallab olmasligi kerak
        self.srv.device = None
        st = self.lic.refresh()
        self.assertEqual((st["state"], st["needs_key"]), ("not_activated", True))
        self.srv.calls.clear()
        self.lic.refresh()
        self.assertEqual(self.srv.calls, [])
        self.assertTrue(self.lic.activate(KEY)["ok"])  # qo'lda qayta kiritilsa - ishlaydi

    def test_other_device_and_tamper(self):
        self.srv.device = "boshqa-kompyuter"
        with self.assertRaises(licensing.LicenseError):
            self.lic.activate(KEY)
        self.assertEqual(self.lic.status()["state"], "none")

        self.srv.device = None
        self.lic.activate(KEY)
        with open(self.path) as f:
            data = json.load(f)
        body, sig = data["token"].split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        payload["expires_at"] = payload["offline_until"] = "2099-01-01T00:00:00.000Z"
        data["token"] = b64url(json.dumps(payload).encode()) + "." + sig
        with open(self.path, "w") as f:
            json.dump(data, f)
        self.assertEqual(self.manager().status()["state"], "invalid")

    def test_forged_server_response_ignored(self):
        other_secret, _ = keypair(b"\x03" * 32)
        self.srv.post = lambda url, payload: (200, {"ok": True, "token": make_token(
            self.now, self.now + 9999 * DAY, secret=other_secret)})
        lic = self.manager()
        with self.assertRaises(licensing.LicenseError):
            lic.activate(KEY)
        self.assertEqual(lic.status()["state"], "none")

    def test_expired_on_activation_unlocks_after_payment(self):
        self.srv.expires = T0 - DAY
        self.assertEqual(self.lic.activate(KEY)["state"], "expired")
        self.srv.expires = T0 + 30 * DAY
        self.assertTrue(self.lic.refresh()["ok"])


class ServerGateTest(unittest.TestCase):
    """Litsenziya bo'lmasa server API'si ishlamasligi kerak."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CAFEPOS_DB"] = os.path.join(cls.tmp.name, "test.db")
        import server
        server.DB_PATH = os.environ["CAFEPOS_DB"]
        server.UPLOAD_DIR = os.path.join(cls.tmp.name, "uploads")
        cls.fake = FakeServer(time.time)
        cls.fake.expires = time.time() + 30 * DAY
        cls.server = server.make_server(port=0, host="127.0.0.1")
        cls.server.license = licensing.LicenseManager(
            path=os.path.join(cls.tmp.name, "license.json"), server_url="http://x", public_key=PUBLIC,
            device=DEVICE, post=cls.fake.post)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.conn.close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req) as res:
                return res.status, json.loads(res.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_gate(self):
        login = {"username": "admin", "password": "admin123"}
        status, body = self.call("POST", "/api/login", login)
        self.assertEqual(status, 402)
        self.assertEqual(body["license"]["state"], "none")
        self.assertEqual(self.call("GET", "/api/me")[0], 402)
        with urlopen(self.base + "/") as res:  # sahifaning o'zi ochiladi - litsenziya ekrani shu yerda
            self.assertEqual(res.status, 200)

        # Litsenziya bo'lmasa ham ishga tushirgich serverni "ishlayapti" deb bilishi kerak (oyna ochilsin)
        import desktop
        old_url, desktop.URL = desktop.URL, self.base + "/"
        try:
            self.assertTrue(desktop.server_running())
        finally:
            desktop.URL = old_url

        status, body = self.call("GET", "/api/license")
        self.assertEqual((status, body["ok"], body["needs_key"]), (200, False, True))
        status, body = self.call("POST", "/api/license/activate", {"key": "CAFE-XXXX-XXXX-XXXX-XXXX"})
        self.assertEqual(status, 400)
        self.assertIn("mavjud emas", body["error"])

        status, body = self.call("POST", "/api/license/activate", {"key": KEY})
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.call("POST", "/api/login", login)[0], 200)
        self.assertEqual(self.call("POST", "/api/license/activate", {"key": KEY})[0], 403)

        self.fake.blocked = True  # admin blokladi -> keyingi tekshiruvda dastur to'xtaydi
        self.server.license.refresh()
        self.assertEqual(self.call("POST", "/api/login", login)[0], 402)
        self.fake.blocked = False
        self.server.license._last_refresh = 0  # ketma-ket tekshiruvlar orasidagi 5 soniyalik pauzani kutmaymiz
        status, body = self.call("POST", "/api/license/refresh")
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.call("POST", "/api/login", login)[0], 200)


if __name__ == "__main__":
    unittest.main()
