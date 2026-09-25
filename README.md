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

- 🪑 **Stollar** — bo'sh/band holati, joriy summa; bosilganda buyurtma ochiladi
- 🥡 **Olib ketish** buyurtmalari
- 🧾 **Buyurtma** — kategoriya bo'yicha menyu, miqdorni +/− bilan o'zgartirish
- 💰 **Kassa** — naqd / karta / Payme / Click, chegirma, qaytim hisoblash
- 🖨️ **Chek** — 58/80 mm termoprinterga chop etish
- 📊 **Hisobot** — davr bo'yicha tushum, o'rtacha chek, to'lov turlari, ko'p sotilgan taomlar, ofitsiantlar
- 🍽️ **Menyu** — kategoriya va taomlarni boshqarish
- 👥 **Xodimlar** — rollar va login

### Rollar

| Rol | Ruxsatlar |
|-----|-----------|
| **Administrator** | Hammasi |
| **Kassir** | Buyurtma, to'lov qabul qilish, bekor qilish, hisobot |
| **Ofitsiant** | Stollar va buyurtma qo'shish (to'lov va hisobotsiz) |

## 🛠️ Texnologiya

- Backend: Python standart kutubxonasi (`http.server` + `sqlite3`) — `server.py`
- Frontend: oddiy HTML/CSS/JavaScript — `static/`
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
