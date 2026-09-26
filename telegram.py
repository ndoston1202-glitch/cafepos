"""Telegram bot: jurnal xabarlarini chatlarga yuborish (orqa fonda, navbat bilan).

Faqat Python standart kutubxonasi. Internet bo'lmasa yoki Telegram javob bermasa,
dastur ishlashda davom etadi - xato "oxirgi xato" sifatida saqlanadi.
"""

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")


class TelegramError(Exception):
    pass


def call(token, method, params=None, timeout=10):
    if not token:
        raise TelegramError("Bot tokeni kiritilmagan")
    url = f"{API}/bot{token}/{method}"
    body = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=timeout) as res:
            data = json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode())
        except ValueError:
            raise TelegramError(f"Telegram xatosi: HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise TelegramError(f"Telegram'ga ulanib bo'lmadi (internetni tekshiring): {getattr(e, 'reason', e)}")
    except ValueError:
        raise TelegramError("Telegram noto'g'ri javob qaytardi")
    if not data.get("ok"):
        desc = data.get("description", "")
        if "Unauthorized" in desc:
            raise TelegramError("Bot tokeni noto'g'ri")
        if "chat not found" in desc:
            raise TelegramError("Chat topilmadi - botga avval /start yozing")
        raise TelegramError(f"Telegram: {desc}")
    return data["result"]


def get_me(token):
    return call(token, "getMe")


def find_chats(token):
    """Botga yozgan chatlar (shaxsiy, guruh, kanal) - chat ID ni aniqlash uchun."""
    chats = {}
    for upd in call(token, "getUpdates", {"limit": 100, "timeout": 0}):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            title = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            chats[chat["id"]] = {"id": chat["id"], "title": title or chat.get("username") or "", "type": chat.get("type")}
    return list(chats.values())


def send_message(token, chat_id, text):
    return call(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
    })


class Notifier:
    """Xabarlarni orqa fonda yuboradi - API so'rovlari Telegram'ni kutib qolmaydi."""

    def __init__(self):
        self.queue = queue.Queue(maxsize=1000)
        self.last_error = None
        self.last_ok = None
        self.sent = 0
        self._thread = None

    def _worker(self):
        while True:
            token, chats, text = self.queue.get()
            for chat in chats:
                for attempt in range(3):
                    try:
                        send_message(token, chat, text)
                        self.sent += 1
                        self.last_ok = time.strftime("%Y-%m-%d %H:%M:%S")
                        self.last_error = None
                        break
                    except TelegramError as e:
                        self.last_error = f"{time.strftime('%H:%M:%S')} · {e}"
                        if "token" in str(e) or "topilmadi" in str(e):
                            break
                        time.sleep(2 * (attempt + 1))
            self.queue.task_done()

    def send(self, token, chats, text):
        if not token or not chats:
            return
        if not self._thread:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()
        try:
            self.queue.put_nowait((token, list(chats), text))
        except queue.Full:
            self.last_error = "Navbat to'lib ketdi - xabarlar yuborilmayapti"


notifier = Notifier()
