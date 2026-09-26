# ☕ CafePOS — kafe uchun kassa tizimi

Yengil POS tizimi. **Faqat Python kerak**: `pip install`, Node.js yoki internet shart emas.
Ma'lumotlar shu papkadagi `cafepos.db` (SQLite) faylida saqlanadi.

## 🚀 Ishga tushirish

CafePOS kompyuterda **alohida oynada** (desktop dastur kabi) ochiladi — manzil qatori va tablarsiz.
Server qora oynasiz orqa fonda ishlaydi.

| Fayl | Vazifasi |
|------|----------|
| **ORNATISH.bat** | Bir marta: ish stoli va Pusk menyusiga "CafePOS" yorlig'ini qo'shadi |
| **ISHGA_TUSHIR.bat** | Dasturni ochadi (yorliq bilan bir xil) |
| **TOXTATISH.bat** | Orqa fondagi serverni to'xtatadi |
| **YANGILASH.bat** | GitHub'dan yangi versiyani oladi va dasturni qayta ochadi |
| **TARMOQQA_RUXSAT.bat** | Telefon/planshetdan kirish uchun fayervolda ruxsat (bir marta) |

Oynani yopish serverni to'xtatmaydi — telefon va planshetlar ishlashda davom etadi.
Xatolar `cafepos.log` fayliga yoziladi.

### Kirish

Kirish ekranida **PIN kod** (4 raqam) ekrandagi raqamlar bilan teriladi — klaviatura shart emas.
Administratorning standart PIN kodi: **1234** (Sotuvchilar bo'limida o'zgartiring). Har bir xodimga
PIN kod Sotuvchilar → xodimni tahrirlash orqali beriladi. "Login va parol bilan kirish" ham qolgan.
5 marta noto'g'ri terilsa, 1 daqiqa kutish kerak bo'ladi.

### Birinchi marta
1. **Python 3.8+** o'rnating: https://www.python.org/downloads/ ("Add Python to PATH" ni belgilang)
2. **ORNATISH.bat** ga ikki marta bosing
3. Ish stolidagi **CafePOS** ikonkasini oching

Login: **admin** · Parol: **admin123** — kirgandan keyin *Xodimlar* bo'limida parolni o'zgartiring.

Linux/macOS: `python3 desktop.py` (yoki faqat server: `python3 server.py`)

## ✨ Imkoniyatlar

- 🏛️ **Zallar** — Asosiy zal, Banket zali, Kabinalar va h.k.; stollar zal bo'yicha ko'rinadi
- 🪑 **Stollar** — bo'sh/band holati, joriy summa; bosilganda buyurtma ochiladi
- 🥡 **Olib ketish** buyurtmalari
- 🧾 **Buyurtma** — kategoriya bo'yicha menyu, miqdorni +/− bilan o'zgartirish
- 👥 **CRM** — Mijozlar (telefon, ism, jins; qarzlar va to'lovlar tarixi) va Mijozlar qarzi
  (Muddati o'tgan / To'lov vaqti keldi / Muddati bor). Buyurtmani "Qarzga" to'lash mumkin.
  Qarz to'lovi: Naqd → naqd kassa, Click → karta, Terminal va Pul ko'chirish → hisob raqam
- 📥 **Import** — Mahsulotlar va Mijozlarni Excel shablon (.xlsx) yoki CSV orqali bir yo'la qo'shish
- ⚖️ **Balansni o'rnatish** (Moliya) — kassa hisoblari, mijoz va ta'minotchi balansini o'zgartirish;
  farq tuzatish sifatida tarixga yoziladi
- 💵 **Moliya** — Kassa (Naqd/Karta/Payme/Click balansi), kirim-chiqim, tranzaksiyalar tarixi,
  tranzaksiya turlari (takrorlanmaydi, o'zgartirilmaydi; bajarilgani bekor qilinadi, o'chirilmaydi)
- 🧾 **Xizmat haqi** — stolda o'tirganlarga foiz (⚙️ Sozlamalar); har bir zal uchun alohida foiz qo'yish mumkin
- 💰 **Kassa** — naqd / karta / Payme / Click, chegirma, qaytim hisoblash
- 🖨️ **Chek** — 58/80 mm termoprinterga chop etish
- 🍳 **Oshxona printeri** — har bir taom o'ziga biriktirilgan printerdan chiqadi (Oshxona, Salat, Bar...)
- 🖥️ **Oshxona ekrani** — oshxona kompyuterida buyurtmalar ko'rinadi, yangi buyurtmada ovoz chiqadi
- 📊 **Hisobot** — davr bo'yicha tushum, o'rtacha chek, to'lov turlari, ko'p sotilgan taomlar, ofitsiantlar
- 🍽️ **Menyu** — taom nomi, tannarxi, sotish narxi, rasmi, kategoriyasi va printeri
- 💹 **Foyda** — tannarx asosida hisobotda foyda ko'rinadi
- 👥 **Xodimlar** — rollar va login
- 📓 **Jurnal** — barcha amallar (sotuv, taom qo'shish, kirim-chiqim, qarz, mahsulot, xodim, sozlama, tizimga kirish):
  kim, qachon va nima qilgani. Qatorni bossangiz batafsil ma'lumot ochiladi. Yozuvlar o'chirilmaydi
- 🔌 **Integratsiyalar** — tashqi xizmatlar bilan ulanish (yangilari qo'shib boriladi):
  - **Telegram bot** (xodimlar uchun) — jurnaldagi amallar tanlangan chat/guruhlarga xabar bo'lib boradi
  - **Mijozlar boti** — mijoz botga telefon raqamini yuborib ulanadi; savdoda mijoz tanlansa chek darhol
    botga boradi, **💰 Balans** tugmasida qarzi va muddatlari ko'rinadi, **🧾 Xaridlarim** — oxirgi xaridlar;
    CRM → Mijozlar'dan barcha yoki tanlangan mijozlarga xabar yuborish mumkin
- 🧾 **Savdolar** (Moliya va Hisobotlar ichida) — barcha cheklar: ko'rish, qayta chop etish,
  qisman qaytarish (tanlangan taomlar, pul qaytariladi) va to'liq bekor qilish

### 🤖 Telegram botni ulash

1. Telegram'da **@BotFather** → `/newbot` → bot nomini yozing → **token** beriladi
2. CafePOS → **Integratsiyalar → Telegram bot** → tokenni qo'ying → **Ulash**
3. **Botni ochish** → **Start** bosing — chat o'zi qo'shiladi va xabarlar kela boshlaydi
   (guruh uchun: botni guruhga qo'shib, guruhda biror narsa yozing)

Kompyuterda internet bo'lishi kerak. Internet uzilsa dastur ishlashda davom etadi, xato Telegram sahifasida ko'rinadi.

### Xodimlar va ruxsatlar

**👥 Xodimlar → + Xodim qo'shish**: ismi, familiyasi, username, parol, telefon, rol va
**bo'limlarga kirish ruxsati** (Stollar, Kassa, Oshxona, Hisobot, Menyu, CRM, Moliya, Zallar, Printerlar, Xodimlar, Sozlamalar, Jurnal, Integratsiyalar).
Rol tanlanganda standart ruxsatlar belgilanadi, keyin xohlagancha o'zgartirish mumkin.
Ruxsat serverda tekshiriladi va darhol kuchga kiradi.

| Rol | Standart ruxsatlar |
|-----|-----------|
| **Administrator** | Hammasi (o'zgarmaydi) |
| **Kassir** | Stollar, Kassa, Oshxona, Hisobot |
| **Ofitsiant** | Stollar |
| **Oshpaz** | Oshxona |

## 📶 Telefon, planshet va boshqa kompyuterlardan kirish

1. Qurilma dastur ishlayotgan kompyuter bilan **bitta Wi-Fi / tarmoqda** bo'lsin
2. Manzilni **⚙️ Sozlamalar** bo'limidan yoki qora oynadan oling (masalan `http://192.168.1.10:8000`)
3. Qurilma brauzerida shu manzilni oching va o'z login/parolingiz bilan kiring
4. Ochilmasa, **TARMOQQA_RUXSAT.bat** ni bir marta ishga tushiring (Windows fayervolida portni ochadi)

Maslahat: kompyuterga routerda doimiy IP bering, shunda manzil o'zgarmaydi.

### 📲 Telefonga ilova qilib o'rnatish (brauzer panelisiz)

CafePOS o'rnatiladigan veb-ilova (PWA): bosh ekranda o'z ikonkasi bilan, Chrome/Safari panelisiz ochiladi.

**Android** — CafePOS ilovasi (APK):
1. Telefonda yuklab oling: https://github.com/ndoston1202-glitch/cafepos/releases/download/android-latest/CafePOS.apk
2. Faylni oching → "noma'lum manbadan o'rnatish"ga ruxsat bering → **O'rnatish**
3. Ilova shu Wi-Fi'dagi CafePOS serverini o'zi topadi (yoki manzilni qo'lda yozing)

Ilova kodi: `android/` papkasida; GitHub Actions uni har o'zgarishda yig'ib, yuqoridagi havolaga joylaydi.

**iPhone (Safari)**: CafePOS'ni oching → **Ulashish** → **Add to Home Screen / На экран «Домой»**

Yo'riqnoma dasturning o'zida ham bor: kirish sahifasida (telefonda) va ⚙️ Sozlamalar bo'limida.

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
- Desktop oyna: `desktop.py` (Chrome/Edge ilova rejimi `--app`)
- Printerlar: `printing.py` (Windows `winspool`, CUPS, TCP 9100)
- Telegram: `telegram.py` (Bot API, xabarlar orqa fonda navbat bilan yuboriladi)
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
