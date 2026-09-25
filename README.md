# ☕ CafePOS — kafe uchun kassa tizimi

Yengil POS tizimi. **Faqat Python kerak**: `pip install`, Node.js yoki internet shart emas.
Ma'lumotlar shu papkadagi `cafepos.db` (SQLite) faylida saqlanadi.

## 🚀 Ishga tushirish

| Fayl | Vazifasi |
|------|----------|
| **ISHGA_TUSHIR.bat** | Dasturni yoqadi, brauzer o'zi ochiladi |
| **YANGILASH.bat** | GitHub'dan yangi versiyani oladi |

To'xtatish uchun qora oynani yoping.

### Birinchi marta
1. **Python 3.8+** o'rnating: https://www.python.org/downloads/ ("Add Python to PATH" ni belgilang)
2. **ISHGA_TUSHIR.bat** ga ikki marta bosing
3. Brauzerda `http://localhost:8000` ochiladi

Login: **admin** · Parol: **admin123** — kirgandan keyin *Xodimlar* bo'limida parolni o'zgartiring.

Linux/macOS: `python3 server.py`

Boshqa qurilmalardan (planshet, telefon) bir xil Wi-Fi tarmog'ida
`http://<kompyuter-IP>:8000` orqali kirish mumkin.

## ✨ Imkoniyatlar

- 🏛️ **Zallar** — Asosiy zal, Banket zali, Kabinalar va h.k.; stollar zal bo'yicha ko'rinadi
- 🪑 **Stollar** — bo'sh/band holati, joriy summa; bosilganda buyurtma ochiladi
- 🥡 **Olib ketish** buyurtmalari
- 🧾 **Buyurtma** — kategoriya bo'yicha menyu, miqdorni +/− bilan o'zgartirish
- 💰 **Kassa** — naqd / karta / Payme / Click, chegirma, qaytim hisoblash
- 🖨️ **Chek** — 58/80 mm termoprinterga chop etish
- 🍳 **Oshxona printeri** — har bir taom o'ziga biriktirilgan printerdan chiqadi (Oshxona, Salat, Bar...)
- 🖥️ **Oshxona ekrani** — oshxona kompyuterida buyurtmalar ko'rinadi, yangi buyurtmada ovoz chiqadi
- 📊 **Hisobot** — davr bo'yicha tushum, o'rtacha chek, to'lov turlari, ko'p sotilgan taomlar, ofitsiantlar
- 🍽️ **Menyu** — taom nomi, tannarxi, sotish narxi, rasmi, kategoriyasi va printeri
- 💹 **Foyda** — tannarx asosida hisobotda foyda ko'rinadi
- 👥 **Xodimlar** — rollar va login

### Rollar

| Rol | Ruxsatlar |
|-----|-----------|
| **Administrator** | Hammasi |
| **Kassir** | Buyurtma, to'lov qabul qilish, bekor qilish, hisobot |
| **Ofitsiant** | Stollar va buyurtma qo'shish (to'lov va hisobotsiz) |
| **Oshpaz** | Faqat oshxona ekrani |

## 🍳 Oshxona printerlari

1. Printerni kompyuterga **USB yoki Wi-Fi** orqali ulang va Windows'da drayverini o'rnating
2. **🖨️ Printerlar → + Printer qo'shish**, nom bering (masalan *Oshxona*, *Salatxona*, *Bar*):
   - **🔌 USB / Wi-Fi** — kompyuterga o'rnatilgan printerlar ro'yxati avtomatik chiqadi, keraklisini tanlang
   - **🌐 Tarmoq (IP)** — drayversiz LAN/Wi-Fi termoprinter: IP manzilni yozing yoki
     **🔍 Tarmoqdan qidirish** tugmasi bilan toping (9100-port)
3. **🧪 Sinov** tugmasi bilan tekshiring
4. **🍽️ Menyu**da taomga printer tanlang (ixtiyoriy): osh → Oshxona, salat → Salatxona, ichimlik → Bar
5. Buyurtmada **🖨️ Oshxona printeriga** bosing — har bir taom o'z printeridan chiqadi.
   Faqat yangi qo'shilgan taomlar chiqadi; kamaytirilgan taom "BEKOR" bo'lib chiqadi.

Printerlar dastur ishlayotgan (server) kompyuterga ulangan bo'lishi kerak.

## 🖥️ Oshxona ekrani

Oshxonadagi kompyuter yoki planshetda `http://<server-IP>:8000` ni oching va **Oshpaz** rolidagi
xodim bilan kiring. Buyurtmada **🖥️ Oshxona kompyuteriga** bosilganda buyurtma ekranda paydo bo'ladi.
Yuqoridagi ro'yxatdan bo'limni (masalan faqat "Oshxona" yoki "Bar") tanlash mumkin.

## 🛠️ Texnologiya

- Backend: Python standart kutubxonasi (`http.server` + `sqlite3`) — `server.py`
- Printerlar: `printing.py` (Windows `winspool`, CUPS, TCP 9100)
- Frontend: oddiy HTML/CSS/JavaScript — `static/`
- Taom rasmlari `uploads/` papkasida saqlanadi
- Parollar PBKDF2 bilan xeshlanadi, sessiya HttpOnly cookie orqali

## 🧪 Testlar

```
python -m unittest discover tests
```

## ⚙️ Sozlamalar (ixtiyoriy)

| O'zgaruvchi | Standart | Tavsif |
|-------------|----------|--------|
| `CAFEPOS_PORT` | `8000` | Server porti |
| `CAFEPOS_DB` | `cafepos.db` | Baza fayli yo'li |
