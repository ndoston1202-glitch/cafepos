"""CafePOS litsenziyasi: kalitni adminka serverida aktivlashtirish va muddatni tekshirish.

Faqat Python standart kutubxonasi ishlatiladi.

Ishlash tartibi:
  1. Foydalanuvchi kalitni kiritadi -> server kalitni shu kompyuterga bog'laydi va imzolangan token beradi.
  2. Dastur ishga tushganda va har 6 soatda server bilan tekshiradi (yangi muddat shu yerda olinadi).
  3. Internet bo'lmasa, oxirgi token bilan `offline_until` gacha ishlaydi.
Token Ed25519 bilan imzolangan - uni qo'lda o'zgartirib bo'lmaydi.
"""

import base64
import calendar
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_URL = os.environ.get("CAFEPOS_LICENSE_SERVER", "https://cafepos-adminka.netlify.app").rstrip("/")
LICENSE_PATH = os.environ.get("CAFEPOS_LICENSE", os.path.join(BASE_DIR, "license.json"))
# Adminkaning ochiq kaliti (Ed25519, 32 bayt). Shaxsiy kalit faqat adminka serverida turadi.
PUBLIC_KEY = base64.b64decode("CXb/TL7IPgGFmsO4YLvpOr8fTlKRCbwuX6vyilPQ/LQ=")

APP_VERSION = "1.0"
CHECK_EVERY = 6 * 3600      # litsenziya joyida bo'lsa - har 6 soatda
RETRY_EVERY = 10 * 60       # bloklangan/tugagan bo'lsa - har 10 daqiqada (to'lovdan keyin tez ochilsin)
CLOCK_SLACK = 3600          # soat farqiga ruxsat (1 soat)
WARN_DAYS = 5               # muddat tugashiga shuncha kun qolganda ogohlantirish
HTTP_TIMEOUT = 12

# ------------------------------------------------------------ Ed25519 imzosini tekshirish (RFC 8032)

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _point(data):
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def ed25519_verify(public_key, message, signature):
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _point(public_key)
    r = _point(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    left = _mul(s, _G)
    right = _add(r, _mul(h, a))
    return ((left[0] * right[2] - right[0] * left[2]) % _P == 0
            and (left[1] * right[2] - right[1] * left[2]) % _P == 0)


# ------------------------------------------------------------ yordamchilar

def _b64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_token(token, public_key=PUBLIC_KEY):
    """Token: base64url(JSON) + "." + base64url(imzo). To'g'ri bo'lsa payload (dict), aks holda None."""
    try:
        body, sig = str(token).split(".")
        if not ed25519_verify(public_key, body.encode("ascii"), _b64url(sig)):
            return None
        payload = json.loads(_b64url(body).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (ValueError, UnicodeError):
        return None


def _ts(iso):
    """'2026-12-11T08:05:00.000Z' -> Unix vaqti (soniya)."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", str(iso))
    if not m:
        raise ValueError("vaqt formati noto'g'ri: %r" % (iso,))
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


def _machine_raw():
    try:
        if sys.platform == "win32":
            import winreg
            access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access) as key:
                return str(winreg.QueryValueEx(key, "MachineGuid")[0])
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if m:
                return m.group(1)
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if os.path.isfile(path):
                with open(path) as f:
                    value = f.read().strip()
                if value:
                    return value
    except Exception:  # reestr/fayl o'qilmasa - zaxira usul
        pass
    return "%s-%012x" % (socket.gethostname(), uuid.getnode())


def device_id():
    """Shu kompyuterning o'zgarmas identifikatori."""
    return hashlib.sha256(("cafepos:" + _machine_raw()).encode("utf-8")).hexdigest()[:40]


def mask_key(key):
    return (key[:9] + "-****-****-" + key[-4:]) if key and len(key) >= 24 else (key or "")


class LicenseError(Exception):
    pass


class _Network(Exception):
    pass


def _post(url, payload):
    """(status, json) qaytaradi. Server bilan aloqa bo'lmasa _Network."""
    req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                  headers={"Content-Type": "application/json", "User-Agent": "CafePOS/" + APP_VERSION})
    try:
        try:
            with urlopen(req, timeout=HTTP_TIMEOUT) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))
    except (URLError, OSError, ValueError) as e:
        raise _Network(str(e))


MESSAGES = {
    "none": "Dasturdan foydalanish uchun litsenziya kalitini kiriting",
    "expired": "Foydalanish muddati tugagan. To'lov qilgach, \"Qayta tekshirish\" tugmasini bosing",
    "blocked": "Litsenziya bloklangan. Administratorga murojaat qiling",
    "invalid_key": "Bunday kalit mavjud emas. Kalitni tekshirib qayta kiriting",
    "device_mismatch": "Bu kalit boshqa kompyuterda aktivlashtirilgan. Administratorga murojaat qiling",
    "not_activated": "Kalit bu kompyuterdan uzilgan. Kalitni qayta kiriting",
    "invalid": "Litsenziya ma'lumoti buzilgan. Kalitni qayta kiriting",
    "need_online": "Litsenziyani tekshirish uchun kompyuterni internetga ulang va \"Qayta tekshirish\"ni bosing",
    "clock": "Kompyuter soati noto'g'ri. Sana va vaqtni to'g'rilab, \"Qayta tekshirish\"ni bosing",
}
# Server shu kodlarni qaytarsa, dastur bloklanadi (token o'chiriladi)
DENY_CODES = ("expired", "blocked", "invalid_key", "device_mismatch", "not_activated")
# Bu holatlarda foydalanuvchi kalitni (qayta) kiritishi mumkin
NEEDS_KEY = ("none", "invalid_key", "not_activated", "invalid", "device_mismatch", "blocked")


class LicenseManager:
    def __init__(self, path=None, server_url=None, public_key=None, device=None, clock=time.time, post=None):
        self.path = path or LICENSE_PATH
        self.server_url = server_url or SERVER_URL
        self.public_key = public_key or PUBLIC_KEY
        self.device = device or device_id()
        self.clock = clock
        self.post = post or _post
        self.lock = threading.RLock()
        self.data = {}
        self._verified = (None, None)   # (token, payload) - imzoni har safar qayta tekshirmaslik uchun
        self._saved_seen = 0
        self._wake = threading.Event()
        self._last_refresh = 0
        self.load()

    # --- fayl

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        self._saved_seen = self.data.get("last_seen") or 0

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)
        self._saved_seen = self.data.get("last_seen") or 0

    # --- holat

    def _payload(self):
        token = self.data.get("token")
        if not token:
            return None
        if self._verified[0] != token:
            self._verified = (token, verify_token(token, self.public_key))
        return self._verified[1]

    def status(self):
        """Joriy holat. `ok` False bo'lsa, dastur ishlamasligi kerak."""
        with self.lock:
            state, info = self._state()
            key = self.data.get("key")
            result = {
                "ok": state == "ok",
                "state": state,
                "message": "" if state == "ok" else MESSAGES[state],
                "key": mask_key(key),
                "needs_key": state in NEEDS_KEY,
                "customer": info.get("customer"),
                "plan": info.get("plan"),
                "expires_at": info.get("expires_at"),
                "days_left": info.get("days_left"),
                "warn": state == "ok" and info.get("days_left", 99) <= WARN_DAYS,
            }
            return result

    def _state(self):
        now = self.clock()
        denied = self.data.get("denied")
        if not self.data.get("key"):
            return "none", {}
        if denied:
            return denied.get("code", "blocked"), {"expires_at": denied.get("expires_at")}
        if not self.data.get("token"):
            return "need_online", {}
        p = self._payload()
        try:
            if not p or p.get("key") != self.data["key"]:
                return "invalid", {}
            expires, offline, issued = _ts(p["expires_at"]), _ts(p["offline_until"]), _ts(p["issued_at"])
        except (KeyError, ValueError):
            return "invalid", {}
        info = {"customer": p.get("customer"), "plan": p.get("plan"), "expires_at": p["expires_at"],
                "days_left": max(0, int((expires - now + 86399) // 86400))}
        if p.get("device_id") != self.device:
            return "device_mismatch", info
        if now >= expires:
            return "expired", info
        last_seen = self.data.get("last_seen") or 0
        if now < issued - CLOCK_SLACK or now < last_seen - CLOCK_SLACK:
            return "clock", info
        if now >= offline:
            return "need_online", info
        # Soat orqaga surilishini aniqlash uchun oxirgi ko'rilgan vaqtni eslab qolamiz
        if now > last_seen:
            self.data["last_seen"] = now
            if now - self._saved_seen > 3600:
                try:
                    self.save()
                except OSError:
                    pass
        return "ok", info

    # --- server bilan aloqa

    def _request(self, action, key):
        return self.post("%s/api/v1/%s" % (self.server_url, action), {
            "key": key, "device_id": self.device, "device_name": socket.gethostname()[:100],
            "app_version": APP_VERSION,
        })

    def _apply(self, key, status, body):
        """Server javobini saqlaydi. Tushunarsiz javob (429, 5xx) bo'lsa False."""
        with self.lock:
            if status == 200 and body.get("ok") and body.get("token"):
                payload = verify_token(body["token"], self.public_key)
                if not payload or payload.get("device_id") != self.device or payload.get("key") != key:
                    return False  # soxta server yoki buzilgan javob - e'tiborga olinmaydi
                self.data = {"key": key, "token": body["token"], "last_seen": self.clock()}
            elif body.get("code") in DENY_CODES:
                self.data = {"key": key, "denied": {"code": body["code"], "expires_at": body.get("expires_at")}}
            else:
                return False
            self.save()
            return True

    def activate(self, key):
        """Kalitni shu kompyuterga bog'laydi. Xato bo'lsa LicenseError."""
        key = re.sub(r"[^A-Za-z0-9]", "", str(key or "")).upper()
        if not (key.startswith("CAFE") and len(key) == 20):
            raise LicenseError("Kalit formati noto'g'ri. Namuna: CAFE-XXXX-XXXX-XXXX-XXXX")
        key = "CAFE-" + "-".join(key[i:i + 4] for i in range(4, 20, 4))
        try:
            status, body = self._request("activate", key)
        except _Network:
            raise LicenseError("Serverga ulanib bo'lmadi. Internetni tekshirib, qayta urining")
        if status == 200 or body.get("code") == "expired":
            # "expired": kalit shu kompyuterga bog'landi, lekin muddati tugagan - to'lovdan keyin o'zi ochiladi
            if self._apply(key, status, body):
                return self.status()
        if status == 429:
            raise LicenseError("Juda ko'p urinish. Bir daqiqadan keyin qayta urining")
        code = body.get("code")
        raise LicenseError(MESSAGES.get(code) or body.get("message") or "Server xatosi (%s)" % status)

    def refresh(self):
        """Server bilan tekshiradi (yangi muddatni oladi). Aloqa bo'lmasa, holat o'zgarmaydi."""
        with self.lock:
            key = self.data.get("key")
            state = self._state()[0]
        if not key or state in ("invalid_key", "not_activated", "device_mismatch"):
            return self.status()  # bu holatlarda kalitni qayta kiritish kerak
        self._last_refresh = time.time()
        try:
            status, body = self._request("check", key)
        except _Network:
            return self.status()
        with self.lock:
            if self.data.get("key") == key:  # shu orada boshqa kalit kiritilmagan bo'lsa
                self._apply(key, status, body)
        return self.status()

    def refresh_throttled(self, min_gap=5):
        if time.time() - self._last_refresh < min_gap:
            return self.status()
        return self.refresh()

    # --- fon tekshiruvi

    def start_background(self):
        thread = threading.Thread(target=self._loop, name="license", daemon=True)
        thread.start()
        return thread

    def _loop(self):
        while True:
            try:
                st = self.refresh()
            except Exception as e:  # fon oqimi hech qachon to'xtamasin
                print("Litsenziya tekshiruvida xato: %r" % (e,))
                st = {"ok": False}
            sys.stdout.flush()
            self._wake.wait(CHECK_EVERY if st.get("ok") else RETRY_EVERY)
            self._wake.clear()
