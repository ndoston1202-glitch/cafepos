// CafePOS - interfeys (kutubxonasiz, oddiy JavaScript)
"use strict";

const state = { user: null, categories: [], products: [], settings: { cafe_name: "CafePOS", service_percent: 0 } };

const ROLE_NAMES = { admin: "Administrator", cashier: "Kassir", waiter: "Ofitsiant", cook: "Oshpaz" };
const PRINTER_KINDS = {
  system: "Kompyuterga ulangan (USB / Wi-Fi)",
  network: "Tarmoq termoprinteri (IP manzil)",
  windows: "Ulashilgan printer (eski)",
};
const METHOD_NAMES = { cash: "Naqd", card: "Karta", payme: "Payme", click: "Click", debt: "Qarzga" };

// ------------------------------------------------------------ yordamchilar

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Eski Safari (iOS 12) uchun: "a ?? b" o'rniga
function ifNull(value, fallback) {
  return value === null || value === undefined ? fallback : value;
}

function esc(value) {
  return String(ifNull(value, "")).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function percent(n) {
  return (+n || 0).toLocaleString("ru-RU").replace(",", ".") + "%";
}

function money(n) {
  return Math.round(n || 0).toLocaleString("ru-RU").replace(/,/g, " ") + " so'm";
}

function today() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function time(s) {
  return s ? s.slice(11, 16) : "";
}

// Hash o'zgarmasa ham sahifani qayta chizadi
function go(hash) {
  if (location.hash === hash) router();
  else location.hash = hash;
}

// "Asosiy zal · Stol 3" yoki "Olib ketish"
function place(o) {
  if (o.type === "takeaway") return "Olib ketish";
  return [o.hall_name, o.table_name].filter(Boolean).join(" · ");
}

// Foydalanuvchida shu ruxsatlardan birortasi bormi
function can(...perms) {
  return !!state.user && perms.some((p) => state.user.permissions.includes(p));
}

let toastTimer;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ""), 2500);
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && url !== "/api/login") {
    state.user = null;
    renderLogin();
    throw new Error(data.error || "Tizimga kiring");
  }
  if (!res.ok) throw new Error(data.error || "Xato: " + res.status);
  return data;
}

// Xatoni foydalanuvchiga ko'rsatadigan o'ram
function safe(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (e) {
      toast(e.message, true);
    }
  };
}

// ------------------------------------------------------------ modal

function openModal(html, onMount) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal">${html}</div></div>`;
  const bg = $(".modal-bg", root);
  bg.addEventListener("click", (e) => { if (e.target === bg) closeModal(); });
  $$("[data-close]", root).forEach((b) => b.addEventListener("click", closeModal));
  const first = $("input, select", root);
  if (first) first.focus();
  if (onMount) onMount($(".modal", root));
}

function closeModal() {
  $("#modal-root").innerHTML = "";
}

// Rasmni kichraytirib JPEG data URL qiladi (server va tarmoqqa yengil bo'lsin)
function resizeImage(file, maxSize) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxSize / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Rasmni o'qib bo'lmadi"));
    };
    img.src = url;
  });
}

function formData(form) {
  const data = {};
  new FormData(form).forEach((value, key) => { data[key] = value; });
  return data;
}

// ------------------------------------------------------------ login

// ------------------------------------------------------------ telefonga ilova qilib o'rnatish

let installPrompt = null;
const APK_URL = "https://github.com/ndoston1202-glitch/cafepos/releases/download/android-latest/CafePOS.apk";
// CafePOS Android ilovasi ichida ochilganmi (ilova window.CafePOSApp ni beradi)
const inAndroidApp = () => !!window.CafePOSApp;
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $$(".install-btn").forEach((b) => b.classList.remove("hidden"));
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  $$(".install-btn").forEach((b) => b.classList.add("hidden"));
  toast("CafePOS ilova sifatida o'rnatildi ✅");
});
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

function installButton(extraClass = "") {
  if (inAndroidApp()) {
    return `<button type="button" class="install-btn ${extraClass}" data-change-server>🔌 Server manzilini o'zgartirish</button>`;
  }
  if (isStandalone()) return "";
  return `<button type="button" class="install-btn ${extraClass}" data-install>📲 Telefonga ilova qilib o'rnatish</button>`;
}

function bindInstallButtons(root = document) {
  $$("[data-install]", root).forEach((b) => b.addEventListener("click", installApp));
  $$("[data-change-server]", root).forEach((b) => b.addEventListener("click", () => window.CafePOSApp.changeServer()));
}

async function installApp() {
  if (installPrompt) {
    installPrompt.prompt();
    await installPrompt.userChoice.catch(() => {});
    installPrompt = null;
    return;
  }
  showInstallHelp();
}

function showInstallHelp() {
  const origin = location.origin;
  const android = `
    <h3>🤖 Android</h3>
    <ol>
      <li>Telefonda ushbu havolani oching va <b>CafePOS.apk</b> ni yuklab oling:<br>
        <a href="${APK_URL}" target="_blank" rel="noopener">⬇️ CafePOS ilovasini yuklab olish</a></li>
      <li>Yuklangan faylni oching. Telefon "noma'lum manbadan o'rnatish"ga ruxsat so'rasa — <b>Ruxsat berish</b>
        (Chrome yoki Fayllar ilovasi uchun), keyin <b>O'rnatish</b></li>
      <li><b>CafePOS</b> ilovasini oching — u shu Wi-Fi'dagi CafePOS serverini o'zi topadi.
        Topmasa, manzilni qo'lda yozing: <code class="copyable">${esc(location.host)}</code></li>
    </ol>`;
  const ios = `
    <h3>🍏 iPhone / iPad (Safari)</h3>
    <ol>
      <li>CafePOS'ni <b>Safari</b>'da oching: <code>${esc(origin)}</code></li>
      <li>Pastdagi <b>Ulashish</b> tugmasi (kvadrat va yuqoriga strelka)</li>
      <li><b>"Na ekran «Domoy»" / "Add to Home Screen"</b> → <b>Qo'shish</b></li>
    </ol>`;
  openModal(`
    <div class="modal-head"><h2>📲 Telefonga ilova qilib o'rnatish</h2>
      <button type="button" class="icon-btn" data-close aria-label="Yopish">✕</button></div>
    <div class="install-help">${isIOS() ? ios + android : android + ios}
      <p class="muted">O'rnatilgach CafePOS bosh ekrandan o'z ikonkasi bilan, brauzer paneli va Google logosisiz ochiladi.</p>
    </div>
    <div class="actions"><button class="btn primary" data-close>Tushunarli</button></div>`, (m) => {
    $$(".copyable", m).forEach((c) => c.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(c.textContent); toast("Nusxa olindi"); } catch { /* ruxsat yo'q */ }
    }));
  });
}

function renderLogin() {
  $("#app").innerHTML = `
    <div class="auth">
      <div class="auth-glow"></div>
      <section class="auth-hero">
        <img class="auth-hero-logo" src="/img/logo.png" alt="CafePos — ERP dasturi">
        <h1>Kafengiz nazorat ostida<br><span>doim va hamma joyda</span></h1>
        <p>Stollar, buyurtmalar, oshxona va kassani yagona tizimda boshqaring</p>
      </section>
      <form class="auth-card" id="login-form">
        <div class="auth-card-brand">
          <img src="/img/logo-icon.png" alt="">
          <span>Cafe<b>Pos</b></span>
        </div>
        <h2>Kirish</h2>
        <label><span>Foydalanuvchi nomi <em>*</em></span>
          <input name="username" autocomplete="username" autocapitalize="off" required></label>
        <label><span>Parol <em>*</em></span>
          <div class="password-field">
            <input name="password" type="password" autocomplete="current-password" required>
            <button type="button" class="eye" id="toggle-password" aria-label="Parolni ko'rsatish">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </label>
        <div class="error" id="login-error"></div>
        <button class="auth-submit">Kirish</button>
        <div class="auth-divider"><span>CafePos · ERP dasturi</span></div>
        <p class="auth-note">Login va parolni administratoringizdan oling</p>
        ${installButton("auth-install")}
      </form>
    </div>`;
  bindInstallButtons($("#app"));
  $("#toggle-password").addEventListener("click", () => {
    const input = $("input[name=password]");
    input.type = input.type === "password" ? "text" : "password";
    $("#toggle-password").classList.toggle("on", input.type === "text");
  });
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      state.user = await api("POST", "/api/login", formData(e.target));
      await loadSettings();
      if (!location.hash || location.hash === "#/") location.hash = defaultRoute();
      router();
    } catch (err) {
      $("#login-error").textContent = err.message;
    }
  });
  $("input[name=username]").focus();
}

async function logout() {
  await api("POST", "/api/logout").catch(() => {});
  state.user = null;
  location.hash = "";
  renderLogin();
}

// ------------------------------------------------------------ layout

// Chiziqli ikonkalar (24x24, rang - currentColor)
const ICONS = {
  tables: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  cashier: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  kitchen: '<path d="M17 21a1 1 0 0 0 1-1v-5.35c0-.46.32-.84.73-1.04a4 4 0 0 0-2.14-7.59 5 5 0 0 0-9.18 0 4 4 0 0 0-2.14 7.59c.41.2.73.58.73 1.04V20a1 1 0 0 0 1 1Z"/><path d="M6 17h12"/>',
  reports: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
  menu: '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>',
  halls: '<path d="M3 21h18M5 21V8l7-5 7 5v13"/><path d="M9 21v-6h6v6"/>',
  printer: '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect x="6" y="14" width="12" height="8" rx="1"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
  burger: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1Z"/>',
  sales: '<path d="M5 2v20l2.5-1.5L10 22l2-1.5 2 1.5 2.5-1.5L19 22V2l-2.5 1.5L14 2l-2 1.5L10 2 7.5 3.5Z"/><path d="M9 8h6M9 12h6M9 16h4"/>',
  box: '<path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  cash: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
  cancel: '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  finance: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .9-3 2s1.3 1.8 3 2 3 .9 3 2-1.3 2-3 2c-1.4 0-2.5-.5-3-1.5M12 6v2M12 16v2"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  crm: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
  debt: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M9 14h6M9 18h4"/>',
  bank: '<path d="M3 21h18M4 10h16M5 10v8M9.5 10v8M14.5 10v8M19 10v8M12 3 3 8h18Z"/>',
  terminal: '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M8 6h8v4H8zM8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>',
  transfer: '<path d="M4 7h14l-3-3M20 17H6l3 3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  upload: '<path d="M12 16V4M6 10l6-6 6 6M4 20h16"/>',
  download: '<path d="M12 4v12M6 10l6 6 6-6M4 20h16"/>',
  scale: '<path d="M12 3v18M5 7h14M5 7l-3 7a4 4 0 0 0 6 0Zm14 0-3 7a4 4 0 0 0 6 0ZM8 21h8"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  in: '<path d="M12 5v14M5 12l7 7 7-7"/>',
  out: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  collapse: '<path d="m11 17-5-5 5-5M18 17l-5-5 5-5"/>',
  expand: '<path d="m6 17 5-5-5-5M13 17l5-5-5-5"/>',
  journal: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/><path d="M9 7h7M9 11h5"/>',
  plug: '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0Z"/><path d="M12 18v4"/>',
  telegram: '<path d="m22 3-9.5 18-3-7.5L2 10.5Z"/><path d="m9.5 13.5 5-5"/>',
};

function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
}

// Chap menyu: bo'lim {perm, href, icon, name} yoki guruh {icon, name, children}
const NAV = [
  { perm: "reports", href: "#/dashboard", icon: "home", name: "Bosh sahifa" },
  { perm: "tables", href: "#/tables", icon: "tables", name: "Stollar" },
  { perm: "kitchen", href: "#/kitchen", icon: "kitchen", name: "Oshxona" },
  { perm: "users", href: "#/users", icon: "users", name: "Sotuvchilar" },
  { perm: "menu", href: "#/menu", icon: "box", name: "Mahsulotlar" },
  { id: "crm", icon: "crm", name: "CRM", children: [
    { perm: "crm", href: "#/crm/customers", icon: "users", name: "Mijozlar" },
    { perm: "crm", href: "#/crm/debts", icon: "debt", name: "Mijozlar qarzi" },
  ] },
  { id: "reports", icon: "reports", name: "Hisobotlar", children: [
    { perm: "reports", href: "#/reports", icon: "reports", name: "Hisobot" },
    { perm: "reports", href: "#/reports/sales", icon: "sales", name: "Savdolar" },
  ] },
  { id: "finance", icon: "finance", name: "Moliya", children: [
    { perm: "finance", href: "#/finance", icon: "cashier", name: "Kassa" },
    { perm: "finance", href: "#/finance/entries", icon: "list", name: "Tranzaksiyalar" },
    { perm: "finance", href: "#/finance/sales", icon: "sales", name: "Savdolar" },
    { perm: "finance", href: "#/finance/types", icon: "plus", name: "Tranzaksiya yaratish" },
    { perm: "finance", href: "#/finance/balances", icon: "scale", name: "Balansni o'rnatish" },
  ] },
  { perm: "journal", href: "#/journal", icon: "journal", name: "Jurnal" },
  { perm: "integrations", href: "#/integrations", icon: "plug", name: "Integratsiyalar" },
];
// Sozlamalar alohida: yuqori o'ngdagi tugma, ichida yorliqlar
const SETTINGS_TABS = [
  { perm: "settings", href: "#/settings", icon: "settings", name: "Umumiy" },
  { perm: "halls", href: "#/tables-admin", icon: "halls", name: "Zallar va stollar" },
  { perm: "printers", href: "#/printers", icon: "printer", name: "Printerlar" },
];

function visibleNav() {
  return NAV.map((item) => item.children
    ? Object.assign({}, item, { children: item.children.filter((c) => can(c.perm)) })
    : item)
    .filter((item) => item.children ? item.children.length : can(item.perm));
}

function visibleSettingsTabs() {
  return SETTINGS_TABS.filter((t) => can(t.perm));
}

function defaultRoute() {
  for (const item of visibleNav()) return item.children ? item.children[0].href : item.href;
  const tabs = visibleSettingsTabs();
  return tabs.length ? tabs[0].href : "#/none";
}

// Yuqori paneldagi sarlavha: "Savdo › Stollar"
function pageTrail(hash) {
  if (hash.startsWith("#/order/")) return ["Stollar", "Buyurtma"];
  if (hash === "#/integrations/telegram") return ["Integratsiyalar", "Telegram bot"];
  if (hash === "#/integrations/customer-bot") return ["Integratsiyalar", "Mijozlar boti"];
  for (const item of NAV) {
    if (item.href === hash) return [item.name];
    for (const c of item.children || []) if (c.href === hash) return [item.name, c.name];
  }
  const tab = SETTINGS_TABS.find((t) => t.href === hash);
  return tab ? ["Sozlamalar", tab.name] : ["CafePOS"];
}

function openGroups() {
  try { return JSON.parse(localStorage.getItem("nav-groups") || "{}"); } catch { return {}; }
}

function layout(content) {
  const hash = location.hash.split("?")[0];
  const isActive = (href) => hash === href || (href === "#/tables" && hash.startsWith("#/order/"))
    || (href === "#/integrations" && hash.startsWith("#/integrations/"));
  const initials = state.user.full_name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const saved = openGroups();
  const link = (item, sub) => `
    <a href="${item.href}" class="${isActive(item.href) ? "active" : ""} ${sub ? "sub" : ""}" title="${item.name}">
      ${icon(item.icon)}<span>${item.name}</span>
    </a>`;
  const nav = visibleNav().map((item) => {
    if (!item.children) return link(item);
    const hasActive = item.children.some((c) => isActive(c.href));
    const open = hasActive || saved[item.id] !== false;
    return `
      <div class="nav-group-box ${open ? "open" : ""} ${hasActive ? "has-active" : ""}" data-group="${item.id}">
        <button type="button" class="nav-parent" title="${item.name}" data-first="${item.children[0].href}">
          ${icon(item.icon)}<span>${item.name}</span><i class="chev">${icon("chevron")}</i>
        </button>
        <div class="nav-children">${item.children.map((c) => link(c, true)).join("")}</div>
      </div>`;
  }).join("");
  const tabs = visibleSettingsTabs();
  const inSettings = tabs.some((t) => t.href === hash);
  const trail = pageTrail(hash);

  $("#app").innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <img class="brand-full" src="/img/logo.png" alt="CafePOS">
          <img class="brand-icon" src="/img/logo-icon.png" alt="CafePOS">
          <button class="side-toggle" id="side-toggle"></button>
        </div>
        ${state.settings.cafe_name && state.settings.cafe_name !== "CafePOS"
          ? `<div class="cafe-name">${esc(state.settings.cafe_name)}</div>` : ""}
        <nav class="side-nav">${nav}</nav>
        ${installButton("side-install")}
      </aside>
      <div class="side-backdrop" id="side-backdrop"></div>
      <div class="content">
        <header class="topbar">
          <button class="burger" id="burger" aria-label="Menyu">${icon("burger")}</button>
          <div class="crumbs">${trail.map((t, i) => i < trail.length - 1
            ? `<span class="muted">${esc(t)}</span><i class="chev">${icon("chevron")}</i>` : `<b>${esc(t)}</b>`).join("")}</div>
          <div class="top-actions">
            ${tabs.length ? `<a href="${tabs[0].href}" class="top-btn ${inSettings ? "active" : ""}" title="Sozlamalar">
              ${icon("gear")}<span>Sozlamalar</span></a>` : ""}
            <div class="top-user">
              <div class="avatar">${esc(initials)}</div>
              <div class="who"><b>${esc(state.user.full_name)}</b><small>${ROLE_NAMES[state.user.role]}</small></div>
              <button class="logout" id="logout-btn" title="Chiqish">${icon("logout")}</button>
            </div>
          </div>
        </header>
        <main id="view">
          ${inSettings ? `<nav class="settings-tabs">${tabs.map((t) => `
            <a href="${t.href}" class="${t.href === hash ? "active" : ""}">${icon(t.icon)}<span>${t.name}</span></a>`).join("")}</nav>` : ""}
          ${content}
        </main>
      </div>
    </div>`;

  $("#logout-btn").addEventListener("click", logout);
  bindInstallButtons($("#app"));
  $$(".nav-parent").forEach((b) => b.addEventListener("click", () => {
    // Yig'ilgan panelda guruh bosilsa - birinchi bo'limiga o'tamiz
    if (document.body.classList.contains("side-collapsed") && window.innerWidth > 760) {
      location.hash = b.dataset.first;
      return;
    }
    const box = b.parentElement;
    box.classList.toggle("open");
    const groups = openGroups();
    groups[box.dataset.group] = box.classList.contains("open");
    try { localStorage.setItem("nav-groups", JSON.stringify(groups)); } catch { /* ruxsat yo'q */ }
  }));
  const toggle = $("#side-toggle");
  const syncToggle = () => {
    const collapsed = document.body.classList.contains("side-collapsed");
    toggle.innerHTML = icon(collapsed ? "expand" : "collapse");
    toggle.title = collapsed ? "Menyuni ochish" : "Menyuni yig'ish";
  };
  toggle.addEventListener("click", () => {
    const collapsed = document.body.classList.toggle("side-collapsed");
    try { localStorage.setItem("side-collapsed", collapsed ? "1" : "0"); } catch { /* ruxsat yo'q */ }
    syncToggle();
  });
  syncToggle();
  $("#burger").addEventListener("click", () => document.body.classList.toggle("nav-open"));
  $("#side-backdrop").addEventListener("click", () => document.body.classList.remove("nav-open"));
  document.body.classList.remove("nav-open");
  return $("#view");
}

async function loadSettings() {
  try {
    state.settings = await api("GET", "/api/settings");
  } catch { /* standart sozlamalar qoladi */ }
}

async function loadMenu() {
  [state.categories, state.products] = await Promise.all([
    api("GET", "/api/categories"),
    api("GET", "/api/products"),
  ]);
}

// ------------------------------------------------------------ stollar

async function viewTables() {
  const [halls, tables] = await Promise.all([api("GET", "/api/halls"), api("GET", "/api/tables")]);
  let hall = "all";
  try { hall = localStorage.getItem("hall") || "all"; } catch { /* ruxsat yo'q */ }
  if (hall !== "all" && !halls.some((h) => String(h.id) === hall)) hall = "all";

  // Zalsiz stollar ham ko'rinsin
  const groups = halls.map((h) => ({ id: String(h.id), name: h.name, tables: tables.filter((t) => t.hall_id === h.id) }));
  const orphan = tables.filter((t) => !t.hall_id);
  if (orphan.length) groups.push({ id: "none", name: "Zalsiz", tables: orphan });

  const card = (t) => {
    const o = t.order;
    return `
      <div class="table-card ${o ? "busy" : ""}" data-id="${t.id}">
        <div class="name">${esc(t.name)}</div>
        <div class="muted">${o ? `Band · ${time(o.created_at)} · ${esc(o.waiter_name || "")}` : `Bo'sh · ${t.seats} o'rin`}</div>
        ${o ? `<div class="sum">${money(o.total)}</div>` : ""}
      </div>`;
  };
  const busyCount = (list) => list.filter((t) => t.order).length;

  const view = layout(`
    <div class="toolbar">
      <h2>Stollar</h2>
      <button class="btn primary" id="takeaway-btn">🥡 Olib ketish buyurtmasi</button>
    </div>
    <div class="hall-tabs">
      <button class="btn" data-hall="all">Hammasi <small>${busyCount(tables)}/${tables.length}</small></button>
      ${groups.map((g) => `<button class="btn" data-hall="${g.id}">${esc(g.name)} <small>${busyCount(g.tables)}/${g.tables.length}</small></button>`).join("")}
    </div>
    <div id="halls"></div>`);

  function render() {
    $$("[data-hall]", view).forEach((b) => b.classList.toggle("active", b.dataset.hall === hall));
    const shown = hall === "all" ? groups : groups.filter((g) => g.id === hall);
    $("#halls", view).innerHTML = shown.map((g) => `
      <section class="hall-section">
        <h3>${esc(g.name)} <span class="muted">· band ${busyCount(g.tables)} / ${g.tables.length}</span></h3>
        <div class="tables-grid">${g.tables.map(card).join("") || `<p class="muted">Bu zalda stol yo'q</p>`}</div>
      </section>`).join("") || `<p class="muted">Stollar yo'q. Administrator "⚙️ Zallar va stollar" bo'limida qo'shadi.</p>`;
    $$(".table-card", view).forEach((c) =>
      c.addEventListener("click", safe(async () => {
        const order = await api("POST", "/api/orders", { type: "dine_in", table_id: +c.dataset.id });
        location.hash = "#/order/" + order.id;
      })));
  }

  $$("[data-hall]", view).forEach((b) => b.addEventListener("click", () => {
    hall = b.dataset.hall;
    try { localStorage.setItem("hall", hall); } catch { /* ruxsat yo'q */ }
    render();
  }));
  $("#takeaway-btn").addEventListener("click", safe(async () => {
    const order = await api("POST", "/api/orders", { type: "takeaway" });
    location.hash = "#/order/" + order.id;
  }));
  render();
}

// ------------------------------------------------------------ buyurtma

async function viewOrder(id) {
  await loadMenu();
  let order = await api("GET", "/api/orders/" + id);
  let activeCat = "all";

  const title = () => `${order.type === "takeaway" ? "🥡" : "🪑"} ${esc(place(order))} · #${order.id}`;
  const view = layout(`
    <div class="toolbar">
      <button class="btn" id="back-btn">← Orqaga</button>
      <h2 id="order-title"></h2>
    </div>
    <div class="order-layout">
      <div>
        <div class="cat-tabs" id="cat-tabs"></div>
        <div class="products-grid" id="products"></div>
      </div>
      <div class="panel cart" id="cart"></div>
    </div>`);

  const leave = () => {
    state.leaveHook = null;
    if (history.length > 1) history.back();
    else location.hash = "#/tables";
  };
  // Taom qo'shilmagan buyurtma - stol band bo'lmasin (boshqa bo'limga o'tilganda ham)
  const discardIfEmpty = () => {
    if (order.status === "open" && !order.items.length) {
      api("POST", `/api/orders/${order.id}/discard`).catch(() => {});
    }
  };
  state.leaveHook = discardIfEmpty;

  $("#back-btn").addEventListener("click", () => {
    if (order.status !== "open") return leave();
    if (!order.items.length) {
      discardIfEmpty();
      return leave();
    }
    openModal(`
      <div class="modal-head"><h2>Buyurtmani bekor qilishni xohlaysizmi?</h2>
        <button type="button" class="icon-btn" data-close aria-label="Yopish">✕</button></div>
      <p class="muted">${esc(place(order))} · ${order.items.length} xil taom · ${money(order.total)}</p>
      <div class="confirm-actions">
        <button type="button" class="btn danger big" id="leave-cancel">Ha, bekor qilish</button>
        <button type="button" class="btn primary big" id="leave-keep">Yo'q, saqlab chiqish</button>
        <button type="button" class="btn big" data-close>Buyurtmaga qaytish</button>
      </div>`, (m) => {
      $("#leave-keep", m).addEventListener("click", () => { closeModal(); leave(); });
      $("#leave-cancel", m).addEventListener("click", safe(async () => {
        await api("POST", `/api/orders/${order.id}/discard`);
        closeModal();
        toast("Buyurtma bekor qilindi");
        state.leaveHook = null;
        location.hash = "#/tables";
      }));
    });
  });

  function renderProducts() {
    const cats = [{ id: "all", name: "Hammasi" }, ...state.categories];
    $("#cat-tabs").innerHTML = cats.map((c) =>
      `<button class="btn small ${String(c.id) === String(activeCat) ? "active" : ""}" data-cat="${c.id}">${esc(c.name)}</button>`).join("");
    $$("#cat-tabs [data-cat]").forEach((b) => b.addEventListener("click", () => {
      activeCat = b.dataset.cat;
      renderProducts();
    }));

    const list = state.products.filter((p) => activeCat === "all" || String(p.category_id) === String(activeCat));
    const closed = order.status !== "open" || !can("tables");
    $("#products").innerHTML = list.map((p) => `
      <button class="product-card ${p.image ? "with-img" : ""}" data-id="${p.id}" ${closed ? "disabled" : ""}>
        ${p.image ? `<img src="/uploads/${encodeURIComponent(p.image)}" alt="" loading="lazy">` : ""}
        <span>${esc(p.name)}</span>
        <span class="price">${money(p.price)}</span>
      </button>`).join("") || `<p class="muted">Bu kategoriyada taom yo'q</p>`;
    $$("#products .product-card").forEach((b) => b.addEventListener("click", safe(async () => {
      order = await api("POST", `/api/orders/${order.id}/items`, { product_id: +b.dataset.id, qty: 1 });
      renderCart();
    })));
  }

  function renderCart() {
    $("#order-title").innerHTML = title();
    const open = order.status === "open";
    const statusText = { paid: "✅ To'langan", cancelled: "❌ Bekor qilingan", refunded: "↩️ Pul qaytarilgan" }[order.status];
    $("#cart").innerHTML = `
      <h3>Buyurtma ${statusText ? `<span class="badge">${statusText}</span>` : ""}</h3>
      <div class="muted">${esc(order.waiter_name || "")} · ${time(order.created_at)}</div>
      <div class="cart-items">
        ${order.items.map((i) => `
          <div class="cart-item">
            <div>${esc(i.name)}<div class="muted">${money(i.price)}</div></div>
            <div class="qty">
              ${open ? `<button data-item="${i.id}" data-qty="${i.qty - 1}">−</button>` : ""}
              <b>${i.qty}</b>
              ${open ? `<button data-item="${i.id}" data-qty="${i.qty + 1}">+</button>` : ""}
            </div>
            <div class="right"><b>${money(i.price * i.qty)}</b></div>
          </div>`).join("") || `<p class="muted">Chap tomondan taom tanlang</p>`}
      </div>
      <div class="totals">
        ${order.service || order.discount ? `<div><span>Summa</span><span>${money(order.subtotal)}</span></div>` : ""}
        ${order.service_percent ? `<div class="service"><span>Xizmat haqi (${percent(order.service_percent)})</span><span>+${money(order.service)}</span></div>` : ""}
        ${order.discount ? `<div><span>Chegirma</span><span>−${money(order.discount)}</span></div>` : ""}
        <div class="grand"><span>Jami</span><span>${money(order.total)}</span></div>
        ${order.payment_method ? `<div class="muted"><span>To'lov</span><span>${METHOD_NAMES[order.payment_method]}</span></div>` : ""}
      </div>
      <div class="cart-actions">
        ${open && can("cashier") ? `<button class="btn primary big" id="pay-btn" ${order.items.length ? "" : "disabled"}>💰 To'lash</button>` : ""}
        <div class="row">
          <button class="btn" id="print-btn" ${order.items.length ? "" : "disabled"}>🖨️ Chek</button>
          ${open && can("cashier") ? `<button class="btn danger" id="cancel-btn">Bekor qilish</button>` : ""}
        </div>
        ${open && can("tables") ? `
        <div class="kitchen-actions">
          <button class="btn kitchen" id="kitchen-print-btn" ${order.items.length ? "" : "disabled"}>
            🖨️ Oshxona printeriga${order.pending_print ? ` <span class="count">${order.pending_print}</span>` : ""}
          </button>
          <button class="btn kitchen" id="kitchen-send-btn" ${order.pending_kds ? "" : "disabled"}>
            🖥️ Oshxona kompyuteriga${order.pending_kds ? ` <span class="count">${order.pending_kds}</span>` : ""}
          </button>
        </div>` : ""}
      </div>`;

    $$("#cart [data-item]").forEach((b) => b.addEventListener("click", safe(async () => {
      order = await api("PUT", `/api/orders/${order.id}/items/${b.dataset.item}`, { qty: +b.dataset.qty });
      renderCart();
    })));
    $("#print-btn").addEventListener("click", () => printReceipt(order));
    const payBtn = $("#pay-btn");
    if (payBtn) payBtn.addEventListener("click", () => payModal(order, (paid) => {
      order = paid;
      renderCart();
      renderProducts();
    }));
    const kitchenPrintBtn = $("#kitchen-print-btn");
    if (kitchenPrintBtn) kitchenPrintBtn.addEventListener("click", safe(async () => {
      kitchenPrintBtn.disabled = true;
      try {
        order = await api("POST", `/api/orders/${order.id}/kitchen-print`);
      } finally {
        renderCart();
      }
      // Printer ishlamasa - buyurtmada qolamiz, qayta urinish mumkin
      if (order.errors.length) {
        toast((order.printed.length ? "Chiqarildi: " + order.printed.join(", ") + ". " : "") + order.errors.join("; "), true);
        return;
      }
      if (order.printed.length) toast("Chiqarildi: " + order.printed.join(", ") + " ✅");
      location.hash = "#/tables";
    }));
    const kitchenSendBtn = $("#kitchen-send-btn");
    if (kitchenSendBtn) kitchenSendBtn.addEventListener("click", safe(async () => {
      kitchenSendBtn.disabled = true;
      try {
        order = await api("POST", `/api/orders/${order.id}/kitchen-send`);
        toast("Oshxona ekraniga yuborildi ✅");
      } finally {
        renderCart();
      }
    }));
    const cancelBtn = $("#cancel-btn");
    if (cancelBtn) cancelBtn.addEventListener("click", safe(async () => {
      if (!confirm("Buyurtmani bekor qilasizmi?")) return;
      await api("POST", `/api/orders/${order.id}/cancel`);
      toast("Buyurtma bekor qilindi");
      location.hash = "#/tables";
    }));
  }

  renderProducts();
  renderCart();
}

function payModal(order, onPaid) {
  let method = "cash";
  let customer = null;
  openModal(`
    <h2>To'lov · #${order.id}</h2>
    <div class="pay-methods">
      ${Object.entries(METHOD_NAMES).map(([k, v]) =>
        `<button type="button" class="btn ${k === method ? "active" : ""}" data-method="${k}">${v}</button>`).join("")}
    </div>
    <label><span>Chegirma (so'm)</span><input id="discount" type="number" min="0" value="0"></label>
    <div class="totals"><div class="grand"><span>To'lanadi</span><span id="to-pay"></span></div></div>
    <span class="field-label" id="customer-label"></span>
    <div id="customer-picker"></div>
    <div class="muted small-note hidden" id="tg-note">📨 Chek mijozning Telegram'iga yuboriladi</div>
    <div id="debt-box" class="hidden">
      <label><span>To'lov muddati</span><input id="due-date" type="date" value="${dateAfter(7)}"></label>
    </div>
    <label id="cash-box"><span>Mijoz bergan pul</span><input id="given" type="number" min="0" placeholder="Qaytim hisoblash uchun"></label>
    <div class="change" id="change"></div>
    <label><input type="checkbox" id="print-after" checked style="width:auto"> To'lovdan so'ng chek chiqarish</label>
    <div class="actions">
      <button class="btn" data-close>Bekor</button>
      <button class="btn primary" id="confirm-pay">Tasdiqlash</button>
    </div>`, (modal) => {
    const total = () => Math.max(order.subtotal + order.service - (+$("#discount", modal).value || 0), 0);
    const update = () => {
      $("#to-pay", modal).textContent = money(total());
      $("#cash-box", modal).classList.toggle("hidden", method !== "cash");
      $("#debt-box", modal).classList.toggle("hidden", method !== "debt");
      $("#customer-label", modal).innerHTML = method === "debt" ? "Mijoz <b>(qarzga yoziladi)</b>"
        : "Mijoz <i>(ixtiyoriy — tanlansa savdo mijozga yoziladi)</i>";
      $("#tg-note", modal).classList.toggle("hidden", !(customer && customer.telegram_chat_id));
      const given = +$("#given", modal).value || 0;
      $("#change", modal).textContent = method === "cash" && given
        ? (given >= total() ? "Qaytim: " + money(given - total()) : "Yetmaydi: " + money(total() - given))
        : "";
    };
    $$("[data-method]", modal).forEach((b) => b.addEventListener("click", () => {
      method = b.dataset.method;
      $$("[data-method]", modal).forEach((x) => x.classList.toggle("active", x === b));
      update();
    }));
    $("#discount", modal).addEventListener("input", update);
    $("#given", modal).addEventListener("input", update);
    customerPicker($("#customer-picker", modal), (c) => { customer = c; update(); });
    $("#confirm-pay", modal).addEventListener("click", safe(async () => {
      const body = { method, discount: +$("#discount", modal).value || 0 };
      if (method === "debt" && !customer) throw new Error("Qarzga yozish uchun mijozni tanlang");
      if (customer) body.customer_id = customer.id;
      if (method === "debt") body.due_date = $("#due-date", modal).value;
      const paid = await api("POST", `/api/orders/${order.id}/pay`, body);
      const shouldPrint = $("#print-after", modal).checked;
      closeModal();
      toast(paid.customer_notified ? "To'lov qabul qilindi ✅ Chek mijozga Telegram'da yuborildi" : "To'lov qabul qilindi ✅");
      onPaid(paid);
      if (shouldPrint) printReceipt(paid);
    }));
    update();
  });
}

function printReceipt(order) {
  $("#print-area").innerHTML = `
    <h3>${esc(state.settings.cafe_name)}</h3>
    <div class="c">Buyurtma #${order.id} · ${esc(place(order))}</div>
    <div class="c">${esc(order.closed_at || order.created_at)}</div>
    <div class="c">Ofitsiant: ${esc(order.waiter_name || "-")}</div>
    <hr>
    <table>
      ${order.items.map((i) => `
        <tr><td colspan="2">${esc(i.name)}</td></tr>
        <tr><td>${i.qty} x ${money(i.price)}</td><td style="text-align:right">${money(i.qty * i.price)}</td></tr>
        ${i.returned_qty ? `<tr><td colspan="2">  qaytarildi: ${i.returned_qty}</td></tr>` : ""}`).join("")}
    </table>
    <hr>
    <table>
      ${order.service || order.discount ? `<tr><td>Summa</td><td style="text-align:right">${money(order.subtotal)}</td></tr>` : ""}
      ${order.service ? `<tr><td>Xizmat haqi ${percent(order.service_percent)}</td><td style="text-align:right">+${money(order.service)}</td></tr>` : ""}
      ${order.discount ? `<tr><td>Chegirma</td><td style="text-align:right">-${money(order.discount)}</td></tr>` : ""}
      <tr><td><b>JAMI</b></td><td style="text-align:right"><b>${money(order.total)}</b></td></tr>
      ${order.returned ? `<tr><td>Qaytarildi</td><td style="text-align:right">-${money(order.returned)}</td></tr>
        <tr><td><b>YAKUNIY</b></td><td style="text-align:right"><b>${money(order.total - order.returned)}</b></td></tr>` : ""}
      ${order.payment_method ? `<tr><td>To'lov</td><td style="text-align:right">${METHOD_NAMES[order.payment_method]}</td></tr>` : ""}
    </table>
    <hr>
    <div class="c">${order.status === "paid" ? "Xaridingiz uchun rahmat!" : order.status === "refunded"
      ? "BEKOR QILINGAN CHEK" : "Hisob (to'lanmagan)"}</div>`;
  window.print();
}


// ------------------------------------------------------------ savdolar (barcha cheklar)

const SALE_STATUS = { paid: ["ok", "Sotilgan"], refunded: ["off", "Bekor qilingan"] };

function saleStatusBadge(s) {
  if (s.status === "paid" && s.returned > 0) return `<span class="badge warn">Qisman qaytarilgan</span>`;
  const [cls, name] = SALE_STATUS[s.status] || ["", s.status];
  return `<span class="badge ${cls}">${name}</span>`;
}

async function saleModal(id, onChange) {
  const s = await api("GET", `/api/sales/${id}`);
  const net = s.total - s.returned;
  const itemsHtml = (returnMode) => s.items.map((i) => {
    const left = i.qty - i.returned_qty;
    return `
      <tr class="${left === 0 ? "cancelled" : ""}">
        <td>${esc(i.name)}${i.returned_qty ? `<small class="amount-out"> · ${i.returned_qty} ta qaytarilgan</small>` : ""}</td>
        <td class="right nowrap">${i.qty} × ${money(i.price)}</td>
        <td class="right nowrap">${money(i.qty * i.price)}</td>
        ${returnMode ? `<td class="right">${left ? `<input type="number" class="ret-qty" data-item="${i.id}" data-price="${i.price}"
          min="0" max="${left}" value="0" style="width:70px">` : ""}</td>` : ""}
      </tr>`;
  }).join("");
  const render = (returnMode) => `
    <div class="sale-receipt">
      <div class="modal-head"><h2>Chek #${s.id} ${saleStatusBadge(s)}</h2>
        <button type="button" class="icon-btn" data-close>✕</button></div>
      <div class="jd-meta">
        <div><small>Vaqti</small><b>${esc(s.closed_at || "")}</b></div>
        <div><small>Joy</small><b>${esc(place(s))}</b><span class="muted">${esc(s.waiter_name || "")}</span></div>
        <div><small>Kassir</small><b>${esc(s.cashier_name || "—")}</b>
          ${s.customer_name ? `<span class="muted">Mijoz: ${esc(s.customer_name)}</span>` : ""}</div>
      </div>
      <table class="list sale-items">
        <thead><tr><th>Taom</th><th class="right">Soni × narx</th><th class="right">Summa</th>${returnMode ? `<th class="right">Qaytarish</th>` : ""}</tr></thead>
        <tbody>${itemsHtml(returnMode)}</tbody>
      </table>
      <div class="sale-totals">
        ${s.service || s.discount ? `<div><span>Taomlar</span><b>${money(s.subtotal)}</b></div>` : ""}
        ${s.service ? `<div><span>Xizmat haqi ${percent(s.service_percent)}</span><b>+${money(s.service)}</b></div>` : ""}
        ${s.discount ? `<div><span>Chegirma</span><b>−${money(s.discount)}</b></div>` : ""}
        <div class="grand"><span>Jami</span><b>${money(s.total)}</b></div>
        ${s.returned ? `<div class="amount-out"><span>Qaytarilgan</span><b>−${money(s.returned)}</b></div>
          <div class="grand"><span>Yakuniy</span><b>${money(net)}</b></div>` : ""}
        <div><span>To'lov</span><b>${METHOD_NAMES[s.payment_method] || s.payment_method}${s.due_date ? ` · muddat ${esc(s.due_date)}` : ""}</b></div>
      </div>
      ${s.returns.length ? `<h3>Qaytarishlar</h3>${s.returns.map((r) => `
        <div class="return-row"><div><b>${money(r.amount)}</b> · ${r.items.map((x) => `${esc(x.name)} × ${x.qty}`).join(", ")}
          <small class="muted">${esc(r.created_at.slice(0, 16))} · ${esc(r.user_name || "")}${r.reason ? " · " + esc(r.reason) : ""}</small></div></div>`).join("")}` : ""}
      ${s.status === "refunded" ? `<div class="notice error-notice">Bekor qilingan: ${esc(s.refunded_at || "")} · ${esc(s.refunded_by_name || "")}
        ${s.refund_reason ? " · " + esc(s.refund_reason) : ""}</div>` : ""}
      ${returnMode ? `
        <label><span>Qaytarish sababi</span><input id="ret-reason" placeholder="Masalan: taom sovuq edi"></label>
        <div class="ret-preview" id="ret-preview"></div>
        <div class="actions"><button type="button" class="btn" id="ret-back">Orqaga</button>
          <button type="button" class="btn primary" id="ret-confirm">Qaytarishni tasdiqlash</button></div>` : `
        <div class="actions sale-actions">
          ${s.can_manage && s.status === "paid" ? `
            <button type="button" class="btn danger-text" id="sale-cancel">Bekor qilish</button>
            ${s.items.some((i) => i.qty > i.returned_qty) ? `<button type="button" class="btn" id="sale-return">↩️ Qaytarish</button>` : ""}` : ""}
          <button type="button" class="btn primary" id="sale-print">🖨 Chek chiqarish</button>
        </div>`}
    </div>`;
  const mount = (returnMode) => {
    openModal(render(returnMode), (m) => {
      if (!returnMode) {
        $("#sale-print", m).addEventListener("click", () => printReceipt(s));
        const ret = $("#sale-return", m);
        if (ret) ret.addEventListener("click", () => mount(true));
        const cancel = $("#sale-cancel", m);
        if (cancel) cancel.addEventListener("click", safe(async () => {
          const reason = prompt(`Chek #${s.id} to'liq bekor qilinadi — ${money(net)} ${s.payment_method === "debt"
            ? "qarzdan olib tashlanadi" : "kassadan qaytariladi"}.\nChek o'chirilmaydi, tarixda qoladi.\n\nSababini yozing:`);
          if (reason === null) return;
          await api("POST", `/api/finance/sales/${s.id}/cancel`, { reason });
          toast("Chek bekor qilindi");
          if (onChange) await onChange();
          saleModal(s.id, onChange);
        }));
        return;
      }
      const preview = () => {
        let value = 0;
        $$(".ret-qty", m).forEach((i) => { value += (+i.value || 0) * +i.dataset.price; });
        const approx = s.subtotal ? Math.round(value * s.total / s.subtotal) : 0;
        $("#ret-preview", m).innerHTML = value
          ? `Qaytariladigan summa: <b>${money(Math.min(approx, net))}</b>${s.discount || s.service ? " <small class='muted'>(chegirma/xizmat haqi ulushi bilan)</small>" : ""}`
          : `<span class="muted">Qaytariladigan taom sonini kiriting</span>`;
      };
      $$(".ret-qty", m).forEach((i) => i.addEventListener("input", preview));
      preview();
      $("#ret-back", m).addEventListener("click", () => mount(false));
      $("#ret-confirm", m).addEventListener("click", safe(async () => {
        const items = $$(".ret-qty", m).filter((i) => +i.value > 0).map((i) => ({ item_id: +i.dataset.item, qty: +i.value }));
        const res = await api("POST", `/api/sales/${s.id}/return`, { items, reason: $("#ret-reason", m).value });
        toast(`Qaytarildi: ${money(res.amount)} ✅`);
        if (onChange) await onChange();
        saleModal(s.id, onChange);
      }));
    });
  };
  mount(false);
}

async function viewSales() {
  const hash = location.hash.split("?")[0];
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const q = new URLSearchParams();
  ["from", "to", "status", "method", "q"].forEach((k) => { if (params.get(k)) q.set(k, params.get(k)); });
  const d = await api("GET", "/api/sales?" + q.toString());
  const sel = (name, options, value) => `<select name="${name}" style="width:auto">${options.map(([k, v]) =>
    `<option value="${k}" ${k === (value || "") ? "selected" : ""}>${v}</option>`).join("")}</select>`;
  const view = layout(`
    <div class="toolbar"><h2>Savdolar</h2><span class="muted">${d.sales.length} ta chek</span></div>
    <form class="filters" id="filters">
      <input type="date" name="from" value="${d.from}" style="width:auto">
      <input type="date" name="to" value="${d.to}" style="width:auto">
      ${sel("status", [["", "Barcha cheklar"], ["paid", "Sotilgan"], ["returned", "Qaytarish bo'lgan"], ["refunded", "Bekor qilingan"]], params.get("status"))}
      ${sel("method", [["", "Barcha to'lovlar"], ...Object.keys(METHOD_NAMES).map((k) => [k, METHOD_NAMES[k]])], params.get("method"))}
      <input type="search" name="q" placeholder="Chek # yoki mijoz" value="${esc(params.get("q") || "")}" style="width:180px">
      <button class="btn primary">Ko'rsatish</button>
    </form>
    <div class="finance-totals">
      <div><span class="muted">Cheklar</span><b>${d.totals.count}</b></div>
      <div><span class="muted">Tushum</span><b class="amount-in">${money(d.totals.revenue)}</b></div>
      <div><span class="muted">Qaytarilgan</span><b class="amount-out">${money(d.totals.returned)}</b></div>
      <div><span class="muted">Bekor qilingan</span><b class="amount-out">${money(d.totals.refunded)}</b></div>
    </div>
    <div class="panel">
      ${d.sales.length ? `<div class="table-scroll"><table class="list sales-table">
        <thead><tr><th>Chek</th><th>Vaqt</th><th>Joy</th><th>Mijoz</th><th>Kassir</th><th>To'lov</th>
          <th class="right">Summa</th><th>Holati</th></tr></thead>
        <tbody>${d.sales.map((s) => `
          <tr class="clickable ${s.status === "refunded" ? "cancelled" : ""}" data-id="${s.id}">
            <td><b>#${s.id}</b></td>
            <td class="nowrap">${esc(s.closed_at.slice(0, 16))}</td>
            <td>${esc(place(s))}</td>
            <td>${esc(s.customer_name || "")}</td>
            <td class="muted">${esc(s.cashier_name || "")}</td>
            <td>${METHOD_NAMES[s.payment_method] || ""}</td>
            <td class="right nowrap"><b>${money(s.total - s.returned)}</b>${s.returned ? `<br><small class="muted"><s>${money(s.total)}</s></small>` : ""}</td>
            <td>${saleStatusBadge(s)}</td>
          </tr>`).join("")}</tbody>
      </table></div>` : `<p class="muted">Tanlangan davrda savdolar yo'q</p>`}
    </div>`);
  $("#filters", view).addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new URLSearchParams();
    new FormData(e.target).forEach((v, k) => { if (v) f.set(k, v); });
    go(hash + "?" + f.toString());
  });
  $$("tr[data-id]", view).forEach((tr) => tr.addEventListener("click", () =>
    saleModal(tr.dataset.id, () => viewSales()).catch((e) => toast(e.message, true))));
}

// ------------------------------------------------------------ bosh sahifa

const PERIODS = [["today", "Bugun"], ["week", "Joriy hafta"], ["month", "Joriy oy"], ["year", "Joriy yil"]];
const METHOD_ICONS = { cash: "cash", card: "card", payme: "phone", click: "phone" };

// Grafik o'qi uchun "chiroyli" yuqori chegara: 1, 2, 2.5, 5 × 10^n
function niceMax(value) {
  if (value <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(value)));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * pow >= value);
  return step * pow;
}

function compact(n) {
  if (n >= 1e9) return (n / 1e9).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " mlrd";
  if (n >= 1e6) return (n / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " mln";
  if (n >= 1e3) return Math.round(n / 1e3) + " ming";
  return String(n);
}

// Bitta qatorli ustunli grafik (SVG): ingichka ustunlar, 4px yumaloq uchi, hover'da qiymat
function columnChart(series, width) {
  // SVG konteyner kengligida chiziladi - yozuvlar kattalashib/kichrayib ketmasin
  const W = Math.max(280, Math.round(width || 640)), H = W < 500 ? 180 : 240;
  const padL = 70, padB = 26, padT = 12;
  const max = niceMax(Math.max(0, ...series.map((d) => d.value)));
  const plotW = W - padL, plotH = H - padB - padT;
  const slot = plotW / series.length;
  const barW = Math.min(24, slot * 0.62);
  const every = Math.ceil(series.length / Math.max(4, Math.floor(plotW / 44)));  // yorliqlar ustma-ust tushmasin
  const y = (v) => padT + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const grid = ticks.map((t) => `
    <line x1="${padL}" x2="${W}" y1="${y(t)}" y2="${y(t)}" class="grid"/>
    <text x="${padL - 8}" y="${y(t) + 4}" class="tick" text-anchor="end">${compact(t)}</text>`).join("");
  const bars = series.map((d, i) => {
    const x = padL + i * slot + (slot - barW) / 2;
    const h = (d.value / max) * plotH;
    const top = y(d.value), r = Math.min(4, h, barW / 2);
    const path = h > 0 ? `M${x},${padT + plotH} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r}
      Q${x + barW},${top} ${x + barW},${top + r} V${padT + plotH} Z` : "";
    return `
      <g class="col" data-i="${i}">
        <rect class="hit" x="${padL + i * slot}" y="${padT}" width="${slot}" height="${plotH}"/>
        ${path ? `<path d="${path}" class="bar"/>` : ""}
        ${i % every === 0 ? `<text x="${x + barW / 2}" y="${H - 8}" class="tick" text-anchor="middle">${esc(d.label)}</text>` : ""}
      </g>`;
  }).join("");
  return `
    <div class="chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart" role="img" aria-label="Tushum grafigi">${grid}${bars}</svg>
      <div class="chart-tip hidden"></div>
      ${series.every((d) => !d.value) ? `<div class="chart-empty">Bu davrda savdo yo'q</div>` : ""}
    </div>`;
}

function bindChart(root, series, formatLabel) {
  const wrap = $(".chart-wrap", root);
  const tip = $(".chart-tip", wrap);
  $$(".col", wrap).forEach((g) => {
    const show = () => {
      const d = series[+g.dataset.i];
      $$(".col.hover", wrap).forEach((x) => x.classList.remove("hover"));
      g.classList.add("hover");
      tip.innerHTML = `<b>${money(d.value)}</b><span>${esc(formatLabel(d.label))}</span>`;
      tip.classList.remove("hidden");
      const box = g.querySelector(".hit").getBoundingClientRect();
      const wb = wrap.getBoundingClientRect();
      const left = Math.min(Math.max(box.left - wb.left + box.width / 2, 70), wb.width - 70);
      tip.style.left = left + "px";
    };
    g.addEventListener("mouseenter", show);
    g.addEventListener("click", show);
  });
  wrap.addEventListener("mouseleave", () => {
    tip.classList.add("hidden");
    $$(".col.hover", wrap).forEach((x) => x.classList.remove("hover"));
  });
}

async function viewDashboard() {
  let period = "month";
  try { period = localStorage.getItem("dash-period") || "month"; } catch { /* ruxsat yo'q */ }
  const view = layout(`<div id="dash"><p class="muted">Yuklanmoqda...</p></div>`);

  async function render() {
    const d = await api("GET", "/api/dashboard?period=" + period);
    // grafik paneli to'liq kenglikda: panel ichki chekinishlarini ayiramiz
    const chartWidth = $("#dash", view).clientWidth - 34;
    const s = d.summary;
    const periodName = PERIODS.find((p) => p[0] === period)[1];
    const methodTotal = d.by_method.reduce((a, m) => a + m.revenue, 0);
    const labelFor = (label) => period === "today" ? `${label}:00 — ${label}:59`
      : period === "month" ? `${label}-kun` : label;
    const tile = (ic, label, value, sub) => `
      <div class="kpi">
        <div class="kpi-head"><span class="kpi-icon">${icon(ic)}</span>${label}</div>
        <div class="kpi-value">${value}</div>
        <div class="kpi-sub">${sub}</div>
      </div>`;
    $("#dash", view).innerHTML = `
      <div class="kpi-row">
        ${tile("cash", "Kunlik savdo", money(d.today.revenue), `${d.today.orders} ta chek`)}
        ${tile("reports", "Oylik savdo", money(d.month.revenue), `${d.month.orders} ta chek`)}
        ${tile("check", "Bugungi cheklar soni", d.today.orders, "to'langan buyurtmalar")}
        ${tile("sales", "O'rtacha chek", money(s.average), periodName.toLowerCase())}
      </div>
      <div class="segmented">${PERIODS.map(([k, name]) =>
        `<button type="button" data-period="${k}" class="${k === period ? "active" : ""}">${name}</button>`).join("")}</div>
      <div class="dash-grid">
        <section class="panel dash-chart">
          <div class="panel-head"><div><span class="muted">Tushum · ${periodName.toLowerCase()}</span>
            <div class="panel-total">${money(s.revenue)}</div></div></div>
          ${columnChart(d.series, chartWidth)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><span class="muted">To'lov usuli</span>
            <div class="panel-total">${money(methodTotal)}</div></div></div>
          ${d.by_method.map((m) => {
            const share = methodTotal ? m.revenue / methodTotal : 0;
            return `
              <div class="method-row">
                <span class="method-icon">${icon(METHOD_ICONS[m.method] || "card")}</span>
                <div class="method-body">
                  <div class="method-line"><span>${METHOD_NAMES[m.method] || esc(m.method)}</span>
                    <b>${money(m.revenue)}</b></div>
                  <div class="meter"><div style="width:${(share * 100).toFixed(1)}%"></div></div>
                </div>
              </div>`;
          }).join("")}
        </section>
        <section class="panel">
          <div class="panel-head"><div><span class="muted">Tranzaksiyalar</span>
            <div class="panel-total">${s.orders} ta</div></div></div>
          ${[["box", "Sotilgan taomlar", s.items], ["check", "Sotuvlar (cheklar)", s.orders],
             ["cancel", "Bekor qilinganlar", s.cancelled], ["clock", "Hozir ochiq buyurtmalar", s.open]]
            .map(([ic, name, n]) => `
              <div class="tx-row"><span class="method-icon">${icon(ic)}</span><span>${name}</span><b>${n} ta</b></div>`).join("")}
        </section>
      </div>`;
    $$("[data-period]", view).forEach((b) => b.addEventListener("click", () => {
      period = b.dataset.period;
      try { localStorage.setItem("dash-period", period); } catch { /* ruxsat yo'q */ }
      render().catch((e) => toast(e.message, true));
    }));
    bindChart(view, d.series, labelFor);
  }
  await render();
}

// ------------------------------------------------------------ CRM: mijozlar va qarzlar

const GENDERS = { m: "Erkak", f: "Ayol" };
const DEBT_METHODS = [
  ["cash", "Naqd", "cash", "Naqd kassaga"],
  ["click", "Click", "phone", "Kartaga"],
  ["terminal", "Terminal", "terminal", "Hisob raqamga"],
  ["transfer", "Pul ko'chirish", "transfer", "Hisob raqamga"],
];

function dateAfter(days) {
  const d = new Date(Date.now() + days * 86400000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function formatPhone(p) {
  const m = /^\+998(\d{2})(\d{3})(\d{2})(\d{2})$/.exec(p || "");
  return m ? `+998 ${m[1]} ${m[2]} ${m[3]} ${m[4]}` : (p || "");
}

function dueText(d) {
  if (d.days < 0) return `${-d.days} kun o'tdi`;
  if (d.days === 0) return "bugun";
  return `${d.days} kun qoldi`;
}

// Mijozni qidirib tanlash yoki shu yerning o'zida yangi mijoz yaratish
function customerPicker(root, onSelect) {
  root.innerHTML = `
    <div class="picker">
      <div class="picker-search">${icon("search")}<input placeholder="Ism yoki telefon bo'yicha qidirish" autocomplete="off"></div>
      <div class="picker-selected hidden"></div>
      <div class="picker-results"></div>
      <button type="button" class="btn small picker-new">+ Yangi mijoz</button>
      <div class="picker-form hidden">
        <input name="p-name" placeholder="Ismi">
        <input name="p-phone" type="tel" placeholder="+998 90 123 45 67">
        <div class="gender-pick">${Object.entries(GENDERS).map(([k, v], i) =>
          `<label><input type="radio" name="p-gender" value="${k}" ${i ? "" : "checked"}><span>${v}</span></label>`).join("")}</div>
        <button type="button" class="btn primary small picker-save">Saqlash va tanlash</button>
      </div>
    </div>`;
  const input = $(".picker-search input", root);
  const results = $(".picker-results", root);
  const selected = $(".picker-selected", root);
  const choose = (c) => {
    onSelect(c);
    selected.innerHTML = `<b>${esc(c.name)}</b> · ${esc(formatPhone(c.phone))}
      <button type="button" class="icon-btn" title="Boshqasini tanlash">✕</button>`;
    selected.classList.remove("hidden");
    results.innerHTML = "";
    $(".picker-search", root).classList.add("hidden");
    $(".picker-form", root).classList.add("hidden");
    $(".picker-new", root).classList.add("hidden");
    $("button", selected).addEventListener("click", () => {
      onSelect(null);
      selected.classList.add("hidden");
      $(".picker-search", root).classList.remove("hidden");
      $(".picker-new", root).classList.remove("hidden");
      input.focus();
    });
  };
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(safe(async () => {
      const q = input.value.trim();
      if (!q) { results.innerHTML = ""; return; }
      const list = await api("GET", "/api/customers?q=" + encodeURIComponent(q));
      results.innerHTML = list.slice(0, 6).map((c) => `
        <button type="button" data-id="${c.id}"><b>${esc(c.name)}${c.telegram_chat_id ? ` <span class="tg-mark" title="Telegram botga ulangan">${icon("telegram")}</span>` : ""}</b><span>${esc(formatPhone(c.phone))}</span>
          ${c.debt > 0 ? `<em>qarz ${money(c.debt)}</em>` : ""}</button>`).join("") || `<p class="muted">Topilmadi</p>`;
      $$("[data-id]", results).forEach((b) => b.addEventListener("click", () => choose(list.find((c) => c.id === +b.dataset.id))));
    }), 250);
  });
  $(".picker-new", root).addEventListener("click", () => $(".picker-form", root).classList.toggle("hidden"));
  $(".picker-save", root).addEventListener("click", safe(async () => {
    const c = await api("POST", "/api/customers", {
      name: $("[name=p-name]", root).value, phone: $("[name=p-phone]", root).value,
      gender: $("[name=p-gender]:checked", root).value,
    });
    toast("Mijoz qo'shildi");
    choose(c);
  }));
}

function customerForm(c, onSaved) {
  const isNew = !c;
  c = c || { gender: "m" };
  return `
    <form class="panel customer-form" id="customer-form">
      <h2>${isNew ? "Yangi mijoz" : "Ma'lumotlarni tahrirlash"}</h2>
      <label><span>Telefon raqami *</span><input name="phone" type="tel" required value="${esc(formatPhone(c.phone))}" placeholder="+998 90 123 45 67"></label>
      <label><span>Ismi *</span><input name="name" required value="${esc(c.name || "")}" placeholder="Masalan: Ali Valiyev"></label>
      <span class="field-label">Jinsi *</span>
      <div class="gender-pick">${Object.entries(GENDERS).map(([k, v]) =>
        `<label><input type="radio" name="gender" value="${k}" ${c.gender === k ? "checked" : ""}><span>${v}</span></label>`).join("")}</div>
      <div class="actions">${isNew ? "" : `<button type="button" class="btn" id="form-cancel">Bekor</button>`}
        <button class="btn primary">${isNew ? "Mijozni yaratish" : "Saqlash"}</button></div>
    </form>`;
}

async function viewCustomers() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const selectedId = +params.get("id") || null;
  const mode = params.get("mode");  // new / edit
  const q = params.get("q") || "";
  const [list, detail] = await Promise.all([
    api("GET", "/api/customers" + (q ? "?q=" + encodeURIComponent(q) : "")),
    selectedId ? api("GET", "/api/customers/" + selectedId) : Promise.resolve(null),
  ]);
  const open = (id, extra = "") => go(`#/crm/customers?id=${id}${extra}${q ? "&q=" + encodeURIComponent(q) : ""}`);

  let right;
  if (mode === "new" || (!detail && !list.length)) {
    right = customerForm(null);
  } else if (detail && mode === "edit") {
    right = customerForm(detail);
  } else if (detail) {
    const d = detail;
    right = `
      <div class="panel customer-card">
        <div class="customer-head">
          <div class="avatar big gender-${d.gender}">${esc(d.name[0] || "?").toUpperCase()}</div>
          <div><h2>${esc(d.name)}</h2>
            <div class="muted">${esc(formatPhone(d.phone))} · ${GENDERS[d.gender]} · ${esc(d.created_at.slice(0, 10))} dan mijoz</div>
            ${d.telegram_chat_id ? `<span class="badge tg-badge">${icon("telegram")} Telegram botga ulangan</span>` : ""}</div>
          <button class="btn small" id="edit-customer">✏️ Tahrirlash</button>
          ${d.telegram_chat_id ? `<button class="btn small" id="msg-customer" title="Telegram bot orqali xabar">${icon("telegram")} Xabar</button>` : ""}
        </div>
        <div class="debt-summary">
          <div><span class="muted">Jami qarz</span><b>${money(d.total_debt)}</b></div>
          <div><span class="muted">To'langan</span><b class="amount-in">${money(d.total_paid)}</b></div>
          <div><span class="muted">Qoldiq</span><b class="${d.remaining > 0 ? "amount-out" : ""}">${money(d.remaining)}</b></div>
        </div>
        <div class="toolbar"><h3 style="margin:0;flex:1">Qarzlar</h3>
          <button class="btn small primary" id="add-debt">+ Qarz qo'shish</button></div>
        ${d.debts.length ? `<table class="list">
          <thead><tr><th>Sana</th><th>Izoh</th><th>Muddat</th><th class="right">Summa</th><th class="right">Qoldiq</th><th></th></tr></thead>
          <tbody>${d.debts.map((x) => `
            <tr><td class="nowrap">${esc(x.created_at.slice(0, 10))}</td><td class="muted">${esc(x.comment || "")}</td>
              <td class="nowrap"><span class="due-badge due-${x.bucket}">${esc(x.due_date)} · ${x.bucket === "closed" ? "yopilgan" : dueText(x)}</span></td>
              <td class="right">${money(x.amount)}</td><td class="right"><b>${money(x.remaining)}</b></td>
              <td class="right">${x.remaining > 0 ? `<button class="btn small primary" data-pay="${x.id}">To'lash</button>` : ""}</td></tr>`).join("")}
          </tbody></table>` : `<p class="muted">Qarzi yo'q</p>`}
        ${d.payments.length ? `<h3>To'lovlar tarixi</h3>
          <table class="list finance-table"><tbody>${d.payments.map((p) => `
            <tr class="${p.status === "cancelled" ? "cancelled" : ""}"><td class="nowrap">${esc(p.created_at.slice(0, 16))}</td>
              <td>${(DEBT_METHODS.find((m) => m[0] === p.method) || [0, p.method])[1]} → ${accountName(p.account)}</td>
              <td class="muted">${esc(p.user_name || "")}</td>
              <td class="right">${signedMoney("in", p.amount)}</td>
              <td>${p.status === "cancelled" ? `<span class="badge off">Bekor qilingan</span>` : `<span class="badge ok">Bajarildi</span>`}</td></tr>`).join("")}
          </tbody></table>` : ""}
      </div>`;
  } else {
    right = `<div class="panel empty-state">${icon("crm")}<p>Ma'lumotlarini ko'rish uchun chapdan mijozni tanlang</p>
      <button class="btn primary" id="new-customer-2">+ Yangi mijoz</button></div>`;
  }

  const view = layout(`
    <div class="crm-layout">
      <div class="panel customer-list">
        <div class="toolbar"><h2 style="flex:1">Mijozlar</h2>
          <button class="btn small" id="message-customers" title="Mijozlarga Telegram xabar">${icon("telegram")}</button>
          <button class="btn small" id="import-customers" title="Import">${icon("upload")}</button>
          <button class="btn primary small" id="new-customer">+ Yangi</button></div>
        <form id="search" class="picker-search">${icon("search")}<input name="q" value="${esc(q)}" placeholder="Ism yoki telefon"></form>
        <div class="customer-items">
          ${list.map((c) => `
            <button class="customer-item ${c.id === selectedId ? "active" : ""}" data-id="${c.id}">
              <span class="avatar gender-${c.gender}">${esc(c.name[0] || "?").toUpperCase()}</span>
              <span class="ci-body"><b>${esc(c.name)}${c.telegram_chat_id ? ` <span class="tg-mark">${icon("telegram")}</span>` : ""}</b><small>${esc(formatPhone(c.phone))}</small></span>
              ${c.debt > 0 ? `<em class="amount-out">${money(c.debt)}</em>` : ""}
            </button>`).join("") || `<p class="muted">Mijozlar yo'q</p>`}
        </div>
        <p class="muted small-note">Jami: ${list.length} ta mijoz</p>
      </div>
      <div class="customer-detail">${right}</div>
    </div>`);

  $$(".customer-item", view).forEach((b) => b.addEventListener("click", () => open(b.dataset.id)));
  $("#search", view).addEventListener("submit", (e) => {
    e.preventDefault();
    const v = e.target.q.value.trim();
    go("#/crm/customers" + (v ? "?q=" + encodeURIComponent(v) : ""));
  });
  const newBtn = () => go("#/crm/customers?mode=new" + (q ? "&q=" + encodeURIComponent(q) : ""));
  $("#new-customer", view).addEventListener("click", newBtn);
  $("#message-customers", view).addEventListener("click", safe(() => messageModal()));
  $("#import-customers", view).addEventListener("click", () => importModal("customers", "Mijozlarni import qilish", () => router()));
  const n2 = $("#new-customer-2", view);
  if (n2) n2.addEventListener("click", newBtn);
  const form = $("#customer-form", view);
  if (form) {
    const cancel = $("#form-cancel", view);
    if (cancel) cancel.addEventListener("click", () => open(selectedId));
    form.addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const data = formData(e.target);
      const saved = mode === "edit" && detail
        ? await api("PUT", "/api/customers/" + detail.id, data)
        : await api("POST", "/api/customers", data);
      toast("Saqlandi ✅");
      open(saved.id);
    }));
  }
  if (detail && mode !== "edit") {
    $("#edit-customer", view).addEventListener("click", () => open(detail.id, "&mode=edit"));
    const msgBtn = $("#msg-customer", view);
    if (msgBtn) msgBtn.addEventListener("click", safe(() => messageModal([detail])));
    $("#add-debt", view).addEventListener("click", () => addDebtModal(detail, () => router()));
    $$("[data-pay]", view).forEach((b) => b.addEventListener("click", () =>
      payDebtModal(detail.debts.find((x) => x.id === +b.dataset.pay), detail, () => router())));
  }
}

function addDebtModal(customer, onSaved) {
  openModal(`
    <form id="f">
      <div class="modal-head"><h2>Qarz qo'shish · ${esc(customer.name)}</h2>
        <button type="button" class="icon-btn" data-close>✕</button></div>
      <label><span>Summa (so'm)</span><input name="amount" type="number" min="1" required></label>
      <label><span>To'lov muddati</span><input name="due_date" type="date" required value="${dateAfter(7)}"></label>
      <label><span>Izoh <i>(ixtiyoriy)</i></span><input name="comment" maxlength="200"></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    await api("POST", `/api/customers/${customer.id}/debts`, formData(e.target));
    closeModal();
    toast("Qarz qo'shildi");
    onSaved();
  })));
}

function payDebtModal(debt, customer, onPaid) {
  const name = customer ? customer.name : debt.customer_name;
  let method = "cash";
  openModal(`
    <form id="f">
      <div class="modal-head"><h2>Qarzni to'lash · ${esc(name)}</h2>
        <button type="button" class="icon-btn" data-close>✕</button></div>
      <p class="muted">Qarz: ${money(debt.amount)} · to'langan: ${money(debt.paid)} ·
        <b>qoldiq: ${money(debt.remaining)}</b> · muddat ${esc(debt.due_date)} (${dueText(debt)})</p>
      <span class="field-label">To'lov usuli</span>
      <div class="pay4">${DEBT_METHODS.map(([k, v, ic, to]) => `
        <button type="button" class="pay4-btn ${k === method ? "active" : ""}" data-m="${k}">
          ${icon(ic)}<b>${v}</b><small>→ ${to}</small></button>`).join("")}</div>
      <label><span>Summa (so'm)</span><input name="amount" type="number" min="1" max="${debt.remaining}" value="${debt.remaining}" required></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">To'lash</button></div>
    </form>`, (m) => {
    $$("[data-m]", m).forEach((b) => b.addEventListener("click", () => {
      method = b.dataset.m;
      $$("[data-m]", m).forEach((x) => x.classList.toggle("active", x === b));
    }));
    $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const res = await api("POST", `/api/debts/${debt.id}/pay`, { method, amount: e.target.amount.value });
      closeModal();
      toast(`To'lov qabul qilindi → ${accountName(res.account)}` + (res.remaining ? ` · qoldiq ${money(res.remaining)}` : " · qarz yopildi ✅"));
      onPaid();
    }));
  });
}

async function viewDebts() {
  const data = await api("GET", "/api/debts");
  const columns = [
    ["overdue", "Muddati o'tgan", "To'lov vaqti o'tib ketgan"],
    ["due", "To'lov vaqti keldi", `Bugun va ${data.due_soon_days} kun ichida`],
    ["later", "Muddati bor", `${data.due_soon_days} kundan keyin`],
  ];
  const view = layout(`
    <div class="toolbar"><h2>Mijozlar qarzi</h2></div>
    <div class="debt-board">
      ${columns.map(([k, title, sub]) => {
        const items = data.debts.filter((d) => d.bucket === k);
        return `
          <section class="debt-col col-${k}">
            <header><div><b>${title}</b><small>${sub}</small></div>
              <div class="right"><b>${money(data.totals[k])}</b><small>${items.length} ta</small></div></header>
            ${items.map((d) => `
              <article class="debt-card">
                <div class="dc-top"><b>${esc(d.customer_name)}</b><span class="due-badge due-${d.bucket}">${dueText(d)}</span></div>
                <div class="muted">${esc(formatPhone(d.customer_phone))}${d.comment ? " · " + esc(d.comment) : ""}</div>
                <div class="dc-bottom"><div><small class="muted">Muddat: ${esc(d.due_date)}</small>
                  <div class="dc-amount">${money(d.remaining)}${d.paid ? `<small class="muted"> / ${money(d.amount)}</small>` : ""}</div></div>
                  <div class="dc-actions"><a class="btn small" href="#/crm/customers?id=${d.customer_id}">Mijoz</a>
                    <button class="btn small primary" data-pay="${d.id}">To'lash</button></div></div>
              </article>`).join("") || `<p class="muted empty-col">Yo'q</p>`}
          </section>`;
      }).join("")}
    </div>`);
  $$("[data-pay]", view).forEach((b) => b.addEventListener("click", () =>
    payDebtModal(data.debts.find((d) => d.id === +b.dataset.pay), null, () => viewDebts())));
}

// ------------------------------------------------------------ import (Excel shablon orqali)

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("Faylni o'qib bo'lmadi"));
    r.readAsDataURL(file);
  });
}

function importModal(kind, title, onDone) {
  openModal(`
    <div class="modal-head"><h2>${title}</h2><button type="button" class="icon-btn" data-close>✕</button></div>
    <ol class="import-steps">
      <li><b>Shablonni yuklab oling</b> va Excel'da oching
        <a class="btn small" href="/api/import/${kind}/template" download>${icon("download")} Shablon (.xlsx)</a></li>
      <li><b>To'ldiring</b> — har bir qator bitta ${kind === "products" ? "mahsulot" : "mijoz"}, namuna qatorlarni o'chiring</li>
      <li><b>Saqlab, shu yerga yuklang</b> (.xlsx yoki .csv)</li>
    </ol>
    <label class="file-drop" id="drop">
      ${icon("upload")}<b>Faylni tanlang</b><small>yoki shu yerga tashlang</small>
      <input type="file" id="import-file" accept=".xlsx,.csv" hidden>
    </label>
    <div id="import-result"></div>
    <div class="actions"><button class="btn" data-close>Yopish</button></div>`, (m) => {
    const input = $("#import-file", m);
    const drop = $("#drop", m);
    const upload = safe(async (file) => {
      if (!file) return;
      const box = $("#import-result", m);
      box.innerHTML = `<p class="muted">Yuklanmoqda: ${esc(file.name)}...</p>`;
      let res;
      try {
        res = await api("POST", `/api/import/${kind}`, { file_name: file.name, data: await readFileAsDataURL(file) });
      } catch (e) {
        box.innerHTML = `<p class="error">${esc(e.message)}</p>`;
        return;
      }
      box.innerHTML = `
        <div class="import-summary">
          <div><b class="amount-in">${res.created}</b><span>yangi qo'shildi</span></div>
          <div><b>${res.updated}</b><span>yangilandi</span></div>
          <div><b class="${res.errors.length ? "amount-out" : ""}">${res.errors.length}</b><span>xato qator</span></div>
        </div>
        ${res.errors.length ? `<div class="import-errors"><table class="list">
          <thead><tr><th>Qator</th><th>Xato</th></tr></thead>
          <tbody>${res.errors.map((e) => `<tr><td>${e.row}</td><td>${esc(e.message)}</td></tr>`).join("")}</tbody>
        </table></div><p class="muted small-note">Xato qatorlarni tuzatib, faylni qayta yuklang — qo'shilganlari takrorlanmaydi.</p>` : ""}`;
      input.value = "";
      if (res.created || res.updated) onDone();
    });
    input.addEventListener("change", () => upload(input.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files[0]); });
  });
}

// ------------------------------------------------------------ balansni o'rnatish

function setBalanceModal({ title, current, hint, url, body, allowNegative, withDue, onDone }) {
  openModal(`
    <form id="f">
      <div class="modal-head"><h2>${title}</h2><button type="button" class="icon-btn" data-close>✕</button></div>
      <p class="muted">${hint}</p>
      <div class="balance-now"><span class="muted">Hozirgi balans</span><b>${money(current)}</b></div>
      <label><span>Yangi balans (so'm)</span><input name="balance" type="number" ${allowNegative ? "" : 'min="0"'} step="1" required value="${current}"></label>
      <div id="diff" class="muted"></div>
      ${withDue ? `<label id="due-box" class="hidden"><span>Qarz to'lov muddati</span><input name="due_date" type="date" value="${dateAfter(30)}"></label>` : ""}
      <label><span>Izoh <i>(ixtiyoriy)</i></span><input name="comment" maxlength="200" placeholder="Masalan: boshlang'ich qoldiq"></label>
      <p class="muted small-note">Eski yozuvlar o'zgarmaydi — farq tuzatish sifatida tarixga yoziladi.</p>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">O'rnatish</button></div>
    </form>`, (m) => {
    const input = $("input[name=balance]", m);
    const sync = () => {
      const diff = (+input.value || 0) - current;
      $("#diff", m).innerHTML = diff ? `Farq: <b class="${diff > 0 ? "amount-in" : "amount-out"}">${diff > 0 ? "+" : "−"}${money(Math.abs(diff))}</b>` : "";
      const due = $("#due-box", m);
      if (due) due.classList.toggle("hidden", diff <= 0);
    };
    input.addEventListener("input", sync);
    input.select();
    sync();
    $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      await api("POST", url, Object.assign({}, body, formData(e.target)));
      closeModal();
      toast("Balans o'rnatildi ✅");
      onDone();
    }));
  });
}

async function viewBalances() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const tab = params.get("tab") || "accounts";
  const b = await api("GET", "/api/balances");
  const tabs = [["accounts", "Kassa", "cashier"], ["customers", "Mijozlar", "users"], ["suppliers", "Ta'minotchilar", "box"]];
  const targetName = (h) => h.target === "account" ? `Kassa · ${accountName(h.account)}`
    : `${h.target === "customer" ? "Mijoz" : "Ta'minotchi"} · ${esc(h.target_name || "")}`;
  const row = (name, sub, balance, attrs, cls = "") => `
    <tr><td><b>${name}</b>${sub ? `<div class="muted small-note">${sub}</div>` : ""}</td>
      <td class="right nowrap"><b class="${cls}">${money(balance)}</b></td>
      <td class="right"><button class="btn small" ${attrs}>O'zgartirish</button></td></tr>`;
  let body;
  if (tab === "accounts") {
    body = `<table class="list"><thead><tr><th>Hisob</th><th class="right">Balans</th><th></th></tr></thead><tbody>
      ${b.accounts.accounts.map((a) => row(accountName(a.account), "", a.balance, `data-account="${a.account}"`, a.balance < 0 ? "amount-out" : "")).join("")}
      <tr class="total-row"><td><b>Umumiy balans</b></td><td class="right"><b>${money(b.accounts.total)}</b></td><td></td></tr>
    </tbody></table>`;
  } else if (tab === "customers") {
    body = `
      <div class="picker-search" style="margin-bottom:10px">${icon("search")}<input id="filter" placeholder="Ism yoki telefon bo'yicha"></div>
      <table class="list" id="bal-table"><thead><tr><th>Mijoz</th><th class="right">Qarzi (balans)</th><th></th></tr></thead><tbody>
      ${b.customers.map((c) => row(esc(c.name), esc(formatPhone(c.phone)), c.balance, `data-customer="${c.id}"`, c.balance > 0 ? "amount-out" : ""))
        .join("") || `<tr><td colspan="3" class="muted">Mijozlar yo'q — CRM › Mijozlar bo'limida qo'shing</td></tr>`}
      </tbody></table>`;
  } else {
    body = `
      <form class="inline-form" id="new-supplier">
        <input name="name" placeholder="Ta'minotchi nomi" required>
        <input name="phone" type="tel" placeholder="Telefon (ixtiyoriy)">
        <button class="btn primary">+ Qo'shish</button>
      </form>
      <table class="list"><thead><tr><th>Ta'minotchi</th><th class="right">Balans (biz qarzdormiz)</th><th></th></tr></thead><tbody>
      ${b.suppliers.map((x) => row(esc(x.name), esc(formatPhone(x.phone || "")), x.balance, `data-supplier="${x.id}"`, x.balance > 0 ? "amount-out" : ""))
        .join("") || `<tr><td colspan="3" class="muted">Ta'minotchilar yo'q</td></tr>`}
      </tbody></table>
      <p class="muted small-note">"Ta'minotchiga pul berish" chiqimida ta'minotchi tanlansa, uning balansidan avtomatik ayiriladi.</p>`;
  }
  const view = layout(`
    <div class="toolbar"><h2>Balansni o'rnatish</h2></div>
    <div class="segmented">${tabs.map(([k, name, ic]) =>
      `<button type="button" data-tab="${k}" class="${k === tab ? "active" : ""}">${icon(ic)} ${name}</button>`).join("")}</div>
    <div class="two-col balances-layout">
      <div class="panel">${body}</div>
      <div class="panel">
        <h3>O'zgarishlar tarixi</h3>
        ${b.history.length ? `<table class="list"><tbody>${b.history.map((h) => {
          const diff = h.new_balance - h.old_balance;
          return `<tr><td>${targetName(h)}<div class="muted small-note">${esc(h.created_at.slice(0, 16))} · ${esc(h.user_name || "")}
            ${h.comment ? " · " + esc(h.comment) : ""}</div></td>
            <td class="right nowrap">${money(h.old_balance)} → <b>${money(h.new_balance)}</b>
              <div class="${diff > 0 ? "amount-in" : "amount-out"} small-note">${diff > 0 ? "+" : "−"}${money(Math.abs(diff))}</div></td></tr>`;
        }).join("")}</tbody></table>` : `<p class="muted">Hali o'zgartirilmagan</p>`}
      </div>
    </div>`);
  const reload = () => router();
  $$("[data-tab]", view).forEach((t) => t.addEventListener("click", () => go("#/finance/balances?tab=" + t.dataset.tab)));
  $$("[data-account]", view).forEach((btn) => btn.addEventListener("click", () => {
    const a = b.accounts.accounts.find((x) => x.account === btn.dataset.account);
    setBalanceModal({ title: `Kassa balansi · ${accountName(a.account)}`, current: a.balance, allowNegative: true,
      hint: "Kassadagi haqiqiy pulni kiriting — farq kirim yoki chiqim tuzatishi bo'lib yoziladi.",
      url: "/api/balances/account", body: { account: a.account }, onDone: reload });
  }));
  $$("[data-customer]", view).forEach((btn) => btn.addEventListener("click", () => {
    const c = b.customers.find((x) => x.id === +btn.dataset.customer);
    setBalanceModal({ title: `Mijoz balansi · ${esc(c.name)}`, current: c.balance, withDue: true,
      hint: "Mijozning bizdan qarzi. Ko'paytirilsa yangi qarz yoziladi, kamaytirilsa eski qarzlardan ayiriladi (kassaga pul tushmaydi).",
      url: "/api/balances/customer", body: { customer_id: c.id }, onDone: reload });
  }));
  $$("[data-supplier]", view).forEach((btn) => btn.addEventListener("click", () => {
    const x = b.suppliers.find((y) => y.id === +btn.dataset.supplier);
    setBalanceModal({ title: `Ta'minotchi balansi · ${esc(x.name)}`, current: x.balance, allowNegative: true,
      hint: "Biz ta'minotchiga qancha qarzdormiz. Manfiy son — ta'minotchi bizga qarzdor.",
      url: "/api/balances/supplier", body: { supplier_id: x.id }, onDone: reload });
  }));
  const filter = $("#filter", view);
  if (filter) filter.addEventListener("input", () => {
    const q = filter.value.toLowerCase().replace(/\s/g, "");
    $$("#bal-table tbody tr", view).forEach((tr) => {
      tr.style.display = tr.textContent.toLowerCase().replace(/\s/g, "").includes(q) ? "" : "none";
    });
  });
  const sup = $("#new-supplier", view);
  if (sup) sup.addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    await api("POST", "/api/suppliers", formData(e.target));
    toast("Ta'minotchi qo'shildi");
    reload();
  }));
}

// ------------------------------------------------------------ moliya

const ACCOUNTS = [["cash", "Naqd", "cash"], ["card", "Karta", "card"], ["payme", "Payme", "phone"], ["click", "Click", "phone"],
  ["bank", "Hisob raqam", "bank"]];
const DIRECTION_NAMES = { in: "Kirim", out: "Chiqim" };
const accountName = (a) => (ACCOUNTS.find((x) => x[0] === a) || [a, a])[1];

function signedMoney(direction, amount) {
  return `<b class="amount-${direction}">${direction === "in" ? "+" : "−"}${money(amount)}</b>`;
}

function entryModal(direction, balance, onSaved) {
  Promise.all([api("GET", "/api/finance/types"), direction === "out" ? api("GET", "/api/suppliers") : []]).then(([types, suppliers]) => {
    const list = types.filter((t) => t.direction === direction && !t.is_adjust);
    const balances = {};
    balance.accounts.forEach((a) => { balances[a.account] = a.balance; });
    openModal(`
      <form id="f">
        <div class="modal-head"><h2>${direction === "in" ? "➕ Kirim" : "➖ Chiqim"}</h2>
          <button type="button" class="icon-btn" data-close aria-label="Yopish">✕</button></div>
        <label><span>Tranzaksiya</span>
          <select name="type_id" required>
            ${list.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("")}
          </select>
          ${list.length ? "" : `<small class="error">Bu turda tranzaksiya yo'q — "Tranzaksiya yaratish" bo'limida yarating</small>`}
        </label>
        <span class="field-label">Hisob</span>
        <div class="account-pick">
          ${ACCOUNTS.map(([k, name, ic], i) => `
            <label class="account-option"><input type="radio" name="account" value="${k}" ${i === 0 ? "checked" : ""}>
              <span>${icon(ic)}<b>${name}</b><small>${money(balances[k] || 0)}</small></span></label>`).join("")}
        </div>
        <label><span>Summa (so'm)</span><input name="amount" type="number" min="1" step="1" required inputmode="numeric"></label>
        ${direction === "out" ? `<label><span>Ta'minotchi <i>(ixtiyoriy — tanlansa uning balansidan ayiriladi)</i></span>
          <select name="supplier_id"><option value="">— Tanlanmagan —</option>
            ${suppliers.map((x) => `<option value="${x.id}">${esc(x.name)} · ${money(x.balance)}</option>`).join("")}</select></label>` : ""}
        <label><span>Izoh <i>(ixtiyoriy)</i></span><input name="comment" maxlength="200" placeholder="Masalan: mijoz ismi yoki ta'minotchi"></label>
        <p class="muted small-note">Saqlangan tranzaksiyani o'zgartirib bo'lmaydi, faqat bekor qilish mumkin.</p>
        <div class="actions"><button type="button" class="btn" data-close>Bekor</button>
          <button class="btn primary" ${list.length ? "" : "disabled"}>Saqlash</button></div>
      </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const btn = $("button.primary", e.target);
      btn.disabled = true;
      try {
        await api("POST", "/api/finance/entries", formData(e.target));
      } finally {
        btn.disabled = false;
      }
      closeModal();
      toast((direction === "in" ? "Kirim" : "Chiqim") + " saqlandi ✅");
      onSaved();
    })));
  }).catch((e) => toast(e.message, true));
}

function entriesTable(entries, withCancel) {
  if (!entries.length) return `<p class="muted">Tranzaksiyalar yo'q</p>`;
  return `
    <div class="table-scroll"><table class="list finance-table">
      <thead><tr><th>Sana</th><th>Tranzaksiya</th><th>Hisob</th><th>Izoh</th><th>Xodim</th>
        <th class="right">Summa</th><th>Holati</th>${withCancel ? "<th></th>" : ""}</tr></thead>
      <tbody>
        ${entries.map((e) => `
          <tr class="${e.status === "cancelled" ? "cancelled" : ""}">
            <td class="nowrap">${esc(e.created_at.slice(0, 16))}</td>
            <td><span class="dir-dot dir-${e.direction}">${icon(e.direction)}</span>${esc(e.type_name)}
              ${e.source === "sale" ? `<span class="badge">avtomatik</span>` : ""}
              ${e.source === "debt" ? `<span class="badge">CRM</span>` : ""}
              ${e.source === "return" ? `<span class="badge">chekdan</span>` : ""}</td>
            <td>${accountName(e.account)}</td>
            <td class="muted">${esc(e.comment || "")}</td>
            <td class="muted">${esc(e.user_name || "")}</td>
            <td class="right nowrap">${signedMoney(e.direction, e.amount)}</td>
            <td>${e.status === "cancelled"
              ? `<span class="badge off" title="${esc((e.cancelled_by_name || "") + (e.cancel_reason ? ": " + e.cancel_reason : ""))}">Bekor qilingan</span>`
              : `<span class="badge ok">Bajarildi</span>`}</td>
            ${withCancel ? `<td class="right">${e.status === "done" && e.source !== "return"
              ? `<button class="btn small danger" data-cancel="${e.id}" data-source="${e.source}"
                  data-direction="${e.direction}" data-amount="${e.amount}">Bekor qilish</button>` : ""}</td>` : ""}
          </tr>`).join("")}
      </tbody>
    </table></div>`;
}

function bindCancel(root, onDone) {
  $$("[data-cancel]", root).forEach((b) => b.addEventListener("click", safe(async () => {
    const sale = b.dataset.source === "sale";
    const effect = b.dataset.direction === "in"
      ? `${money(+b.dataset.amount)} kassadan chiqadi`
      : `${money(+b.dataset.amount)} kassaga qaytadi`;
    const reason = prompt(
      (sale ? `Buyurtma #${b.dataset.cancel} savdosi bekor qilinadi (pul qaytarildi).\n` : "Tranzaksiya bekor qilinadi.\n") +
      `${effect}. Yozuv o'chirilmaydi, tarixda qoladi.\n\nSababini yozing:`);
    if (reason === null) return;
    const url = sale ? `/api/finance/sales/${b.dataset.cancel}/cancel`
      : b.dataset.source === "debt" ? `/api/debt-payments/${b.dataset.cancel}/cancel`
        : `/api/finance/entries/${b.dataset.cancel}/cancel`;
    await api("POST", url, { reason });
    toast(`Bekor qilindi: ${effect}`);
    onDone();
  })));
}

async function viewFinanceCash() {
  const [balance, recent] = await Promise.all([api("GET", "/api/finance/balance"), api("GET", "/api/finance/entries")]);
  const view = layout(`
    <div class="toolbar">
      <h2>Kassa</h2>
      <button class="btn primary" id="add-in">${icon("in")} Kirim</button>
      <button class="btn" id="add-out">${icon("out")} Chiqim</button>
    </div>
    <div class="kpi-row finance-balances">
      <div class="kpi total-kpi">
        <div class="kpi-head"><span class="kpi-icon">${icon("finance")}</span>Umumiy balans</div>
        <div class="kpi-value ${balance.total < 0 ? "negative" : ""}">${money(balance.total)}</div>
        <div class="kpi-sub">barcha hisoblar</div>
      </div>
      ${balance.accounts.map((a) => `
        <div class="kpi">
          <div class="kpi-head"><span class="kpi-icon">${icon((ACCOUNTS.find((x) => x[0] === a.account) || [0, 0, "card"])[2])}</span>
            ${accountName(a.account)}</div>
          <div class="kpi-value ${a.balance < 0 ? "negative" : ""}">${money(a.balance)}</div>
          <div class="kpi-breakdown">
            <span>Savdo</span><b>${money(a.sales)}</b>
            <span>Qarz to'lovi</span><b class="amount-in">+${money(a.debt)}</b>
            <span>Kirim</span><b class="amount-in">+${money(a.in)}</b>
            <span>Chiqim</span><b class="amount-out">−${money(a.out)}</b>
          </div>
        </div>`).join("")}
    </div>
    <div class="panel">
      <div class="toolbar"><h3 style="margin:0;flex:1">So'nggi tranzaksiyalar</h3>
        <a class="btn small" href="#/finance/entries">Hammasi →</a></div>
      ${entriesTable(recent.entries.slice(0, 10), true)}
    </div>`);
  const reload = () => viewFinanceCash().catch((e) => toast(e.message, true));
  $("#add-in", view).addEventListener("click", () => entryModal("in", balance, reload));
  $("#add-out", view).addEventListener("click", () => entryModal("out", balance, reload));
  bindCancel(view, reload);
}

async function viewFinanceEntries() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const q = new URLSearchParams();
  ["from", "to", "direction", "account", "source"].forEach((k) => { if (params.get(k)) q.set(k, params.get(k)); });
  const d = await api("GET", "/api/finance/entries?" + q.toString());
  const sel = (name, options, value) => `<select name="${name}" style="width:auto">${options.map(([k, v]) =>
    `<option value="${k}" ${k === (value || "") ? "selected" : ""}>${v}</option>`).join("")}</select>`;
  const view = layout(`
    <div class="toolbar"><h2>Tranzaksiyalar</h2></div>
    <form class="filters" id="filters">
      <input type="date" name="from" value="${d.from}" style="width:auto">
      <input type="date" name="to" value="${d.to}" style="width:auto">
      ${sel("direction", [["", "Kirim va chiqim"], ["in", "Faqat kirim"], ["out", "Faqat chiqim"]], params.get("direction"))}
      ${sel("account", [["", "Barcha hisoblar"], ...ACCOUNTS.map(([k, v]) => [k, v])], params.get("account"))}
      ${sel("source", [["all", "Hammasi"], ["manual", "Faqat qo'lda kiritilgan"], ["sales", "Faqat savdo"], ["debts", "Faqat qarz to'lovlari"]], params.get("source") || "all")}
      <button class="btn primary">Ko'rsatish</button>
    </form>
    <div class="finance-totals">
      <div><span class="muted">Kirim</span><b class="amount-in">+${money(d.total_in)}</b></div>
      <div><span class="muted">Chiqim</span><b class="amount-out">−${money(d.total_out)}</b></div>
      <div><span class="muted">Farq</span><b>${money(d.total_in - d.total_out)}</b></div>
    </div>
    <div class="panel">${entriesTable(d.entries, true)}</div>`);
  $("#filters", view).addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new URLSearchParams();
    new FormData(e.target).forEach((v, k) => { if (v && v !== "all") f.set(k, v); });
    go("#/finance/entries?" + f.toString());
  });
  bindCancel(view, () => router());
}

async function viewFinanceTypes() {
  const types = await api("GET", "/api/finance/types");
  const view = layout(`
    <div class="two-col">
      <form class="panel" id="f">
        <h2>Tranzaksiya yaratish</h2>
        <label><span>Nomi</span><input name="name" required minlength="2" maxlength="80" placeholder="Masalan: Ijara to'lovi"></label>
        <span class="field-label">Turi</span>
        <div class="kind-switch">
          <label class="kind-option"><input type="radio" name="direction" value="in" checked>
            <span>${icon("in")} Kirim<small>Kassaga pul tushadi</small></span></label>
          <label class="kind-option"><input type="radio" name="direction" value="out">
            <span>${icon("out")} Chiqim<small>Kassadan pul chiqadi</small></span></label>
        </div>
        <p class="muted small-note">Nomi takrorlanmaydi. Yaratilgan tranzaksiyani o'zgartirib bo'lmaydi.</p>
        <div class="actions"><button class="btn primary">Yaratish</button></div>
      </form>
      <div class="panel">
        <h2>Tranzaksiyalar ro'yxati</h2>
        <table class="list">
          <thead><tr><th>Nomi</th><th>Turi</th><th>Ishlatilgan</th><th>Yaratgan</th></tr></thead>
          <tbody>
            ${types.map((t) => `
              <tr><td><b>${esc(t.name)}</b> ${t.is_system ? `<span class="badge">bazaviy</span>` : ""}</td>
                <td><span class="dir-dot dir-${t.direction}">${icon(t.direction)}</span>${DIRECTION_NAMES[t.direction]}</td>
                <td class="muted">${t.used} marta</td>
                <td class="muted">${t.is_system ? "tizim" : esc(t.created_by_name || "")} · ${esc(t.created_at.slice(0, 10))}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`);
  $("#f", view).addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    await api("POST", "/api/finance/types", formData(e.target));
    toast("Tranzaksiya yaratildi ✅");
    viewFinanceTypes();
  }));
}

// ------------------------------------------------------------ hisobot

async function viewReports() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const from = params.get("from") || today();
  const to = params.get("to") || from;
  const r = await api("GET", `/api/reports?from=${from}&to=${to}`);

  const view = layout(`
    <div class="toolbar">
      <h2>Hisobot</h2>
      <input type="date" id="from" value="${from}" style="width:auto">
      <span>—</span>
      <input type="date" id="to" value="${to}" style="width:auto">
      <button class="btn primary" id="apply">Ko'rsatish</button>
      <button class="btn" id="today">Bugun</button>
    </div>
    <div class="stats">
      <div class="stat"><div class="label">Tushum</div><div class="value">${money(r.summary.revenue)}</div></div>
      <div class="stat"><div class="label">Buyurtmalar</div><div class="value">${r.summary.orders}</div></div>
      <div class="stat"><div class="label">O'rtacha chek</div><div class="value">${money(r.summary.average)}</div></div>
      <div class="stat"><div class="label">Tannarx</div><div class="value">${money(r.summary.cost)}</div></div>
      <div class="stat"><div class="label">Foyda</div><div class="value profit">${money(r.summary.profit)}</div></div>
      <div class="stat"><div class="label">Xizmat haqi</div><div class="value">${money(r.summary.service)}</div></div>
      <div class="stat"><div class="label">Chegirmalar</div><div class="value">${money(r.summary.discount)}</div></div>
    </div>
    <div class="report-grid">
      <div class="panel"><h3>To'lov turlari</h3>
        <table class="list">
          ${r.by_method.map((m) => `<tr><td>${METHOD_NAMES[m.method] || m.method}</td><td>${m.orders} ta</td><td class="right">${money(m.revenue)}</td></tr>`).join("")
            || `<tr><td class="muted">Ma'lumot yo'q</td></tr>`}
        </table>
      </div>
      <div class="panel"><h3>Ko'p sotilgan taomlar</h3>
        <table class="list">
          ${r.top_products.map((p) => `<tr><td>${esc(p.name)}</td><td>${p.qty} ta</td><td class="right">${money(p.revenue)}</td>
            <td class="right muted" title="Foyda">+${money(p.profit)}</td></tr>`).join("")
            || `<tr><td class="muted">Ma'lumot yo'q</td></tr>`}
        </table>
      </div>
      <div class="panel"><h3>Ofitsiantlar</h3>
        <table class="list">
          ${r.by_waiter.map((w) => `<tr><td>${esc(w.name)}</td><td>${w.orders} ta</td><td class="right">${money(w.revenue)}</td></tr>`).join("")
            || `<tr><td class="muted">Ma'lumot yo'q</td></tr>`}
        </table>
      </div>
    </div>
    <div class="panel"><h3>Yopilgan buyurtmalar</h3>
      <table class="list">
        <thead><tr><th>#</th><th>Vaqt</th><th>Joy</th><th>Ofitsiant</th><th>To'lov</th><th class="right">Summa</th></tr></thead>
        <tbody>
          ${r.orders.map((o) => `
            <tr class="clickable" data-id="${o.id}">
              <td>${o.id}</td><td>${esc(o.closed_at)}</td><td>${esc(place(o))}</td>
              <td>${esc(o.waiter_name || "-")}</td>
              <td>${METHOD_NAMES[o.payment_method] || ""}</td><td class="right">${money(o.total)}</td>
            </tr>`).join("") || `<tr><td colspan="6" class="muted">Bu davrda buyurtma yo'q</td></tr>`}
        </tbody>
      </table>
    </div>`);

  $("#apply").addEventListener("click", () => go(`#/reports?from=${$("#from").value}&to=${$("#to").value}`));
  $("#today").addEventListener("click", () => go("#/reports"));
  $$("tr[data-id]", view).forEach((tr) =>
    tr.addEventListener("click", () => (location.hash = "#/order/" + tr.dataset.id)));
}

// ------------------------------------------------------------ menyu (admin)

async function viewMenu() {
  await loadMenu();
  const printers = await api("GET", "/api/printers");
  const catName = (id) => (state.categories.find((c) => c.id === id) || {}).name || "—";
  layout(`
    <div class="two-col">
      <div class="panel">
        <div class="toolbar"><h2>Kategoriyalar</h2><button class="btn primary small" id="add-cat">+ Qo'shish</button></div>
        <table class="list">
          ${state.categories.map((c) => `
            <tr><td>${esc(c.name)}</td>
              <td class="right">
                <button class="btn small" data-edit-cat="${c.id}">✏️</button>
                <button class="btn small danger" data-del-cat="${c.id}">🗑</button>
              </td></tr>`).join("") || `<tr><td class="muted">Kategoriya yo'q</td></tr>`}
        </table>
      </div>
      <div class="panel">
        <div class="toolbar"><h2>Taomlar</h2>
          <button class="btn small" id="import-prod">${icon("upload")} Import</button>
          <button class="btn primary small" id="add-prod">+ Taom qo'shish</button></div>
        <table class="list">
          <thead><tr><th></th><th>Nomi</th><th>Kategoriya</th><th>Printer</th>
            <th class="right">Tannarx</th><th class="right">Sotish narxi</th><th class="right">Foyda</th><th></th></tr></thead>
          <tbody>
            ${state.products.map((p) => `
              <tr><td class="thumb-cell">${p.image ? `<img class="thumb" src="/uploads/${encodeURIComponent(p.image)}" alt="">` : `<div class="thumb empty">🍽️</div>`}</td>
                <td>${esc(p.name)}</td><td>${esc(catName(p.category_id))}</td>
                <td>${p.printer_name ? `🖨️ ${esc(p.printer_name)}` : `<span class="muted">—</span>`}</td>
                <td class="right muted">${p.cost ? money(p.cost) : "—"}</td>
                <td class="right">${money(p.price)}</td>
                <td class="right">${p.cost ? money(p.price - p.cost) : "—"}</td>
                <td class="right">
                  <button class="btn small" data-edit-prod="${p.id}">✏️</button>
                  <button class="btn small danger" data-del-prod="${p.id}">🗑</button>
                </td></tr>`).join("") || `<tr><td colspan="8" class="muted">Taom yo'q</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`);

  const catForm = (c = {}) => openModal(`
    <form id="f"><h2>${c.id ? "Kategoriyani tahrirlash" : "Yangi kategoriya"}</h2>
      <label><span>Nomi</span><input name="name" value="${esc(c.name || "")}" required></label>
      <label><span>Tartib raqami</span><input name="sort" type="number" value="${ifNull(c.sort, state.categories.length)}"></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    await api(c.id ? "PUT" : "POST", "/api/categories" + (c.id ? "/" + c.id : ""), formData(e.target));
    closeModal();
    toast("Saqlandi");
    viewMenu();
  })));

  const prodForm = (p = {}) => openModal(`
    <form id="f" class="product-form"><h2>${p.id ? "Taomni tahrirlash" : "Yangi taom"}</h2>
      <label><span>Mahsulot nomi</span><input name="name" value="${esc(p.name || "")}" placeholder="Masalan: Osh" required></label>
      <div class="grid-2">
        <label><span>Tannarxi (so'm)</span><input name="cost" type="number" min="0" value="${p.cost || ""}" placeholder="0"></label>
        <label><span>Sotish narxi (so'm)</span><input name="price" type="number" min="0" value="${ifNull(p.price, "")}" required></label>
      </div>
      <div class="muted" id="margin"></div>
      <div class="image-field">
        <div class="image-preview" id="img-preview"></div>
        <div>
          <span class="field-label">Rasmi</span>
          <label class="btn small file-btn">📷 Rasm tanlash<input type="file" id="img-input" accept="image/*" hidden></label>
          <button type="button" class="btn small danger hidden" id="img-remove">O'chirish</button>
        </div>
      </div>
      <label><span>Kategoriyasi</span>
        <select name="category_id">
          <option value="">— Kategoriyasiz —</option>
          ${state.categories.map((c) => `<option value="${c.id}" ${c.id === p.category_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}
        </select></label>
      <label><span>Qaysi printerdan chiqadi <i>(ixtiyoriy)</i></span>
        <select name="printer_id">
          <option value="">— Printersiz —</option>
          ${printers.map((pr) => `<option value="${pr.id}" ${pr.id === p.printer_id ? "selected" : ""}>🖨️ ${esc(pr.name)}</option>`).join("")}
        </select>
        <small class="muted">${printers.length
          ? "Masalan: osh → Oshxona, salat → Salatxona, ichimlik → Bar"
          : "Printerlar hali qo'shilmagan — \"🖨️ Printerlar\" bo'limida qo'shing"}</small>
      </label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => {
    let image = null;        // yangi tanlangan rasm (data URL)
    let removeImage = false;
    const preview = $("#img-preview", m);
    const showPreview = () => {
      const src = image || (!removeImage && p.image ? "/uploads/" + encodeURIComponent(p.image) : null);
      preview.innerHTML = src ? `<img src="${src}" alt="">` : "🍽️";
      $("#img-remove", m).classList.toggle("hidden", !src);
    };
    const showMargin = () => {
      const cost = +$("input[name=cost]", m).value || 0;
      const price = +$("input[name=price]", m).value || 0;
      $("#margin", m).textContent = cost && price ? `Foyda: ${money(price - cost)} (${Math.round((price - cost) / price * 100)}%)` : "";
    };
    $("#img-input", m).addEventListener("change", safe(async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      image = await resizeImage(file, 600);
      removeImage = false;
      showPreview();
    }));
    $("#img-remove", m).addEventListener("click", () => {
      image = null;
      removeImage = true;
      $("#img-input", m).value = "";
      showPreview();
    });
    $("input[name=cost]", m).addEventListener("input", showMargin);
    $("input[name=price]", m).addEventListener("input", showMargin);
    showPreview();
    showMargin();
    $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const data = formData(e.target);
      if (image) data.image = image;
      if (removeImage) data.remove_image = true;
      const btn = $("button.primary", e.target);
      btn.disabled = true;
      try {
        await api(p.id ? "PUT" : "POST", "/api/products" + (p.id ? "/" + p.id : ""), data);
      } finally {
        btn.disabled = false;
      }
      closeModal();
      toast("Saqlandi");
      viewMenu();
    }));
  });

  $("#add-cat").addEventListener("click", () => catForm());
  $("#add-prod").addEventListener("click", () => prodForm());
  $("#import-prod").addEventListener("click", () => importModal("products", "Mahsulotlarni import qilish", viewMenu));
  $$("[data-edit-cat]").forEach((b) => b.addEventListener("click", () =>
    catForm(state.categories.find((c) => c.id === +b.dataset.editCat))));
  $$("[data-edit-prod]").forEach((b) => b.addEventListener("click", () =>
    prodForm(state.products.find((p) => p.id === +b.dataset.editProd))));
  $$("[data-del-cat]").forEach((b) => b.addEventListener("click", safe(async () => {
    if (!confirm("Kategoriyani o'chirasizmi?")) return;
    await api("DELETE", "/api/categories/" + b.dataset.delCat);
    viewMenu();
  })));
  $$("[data-del-prod]").forEach((b) => b.addEventListener("click", safe(async () => {
    if (!confirm("Taomni o'chirasizmi?")) return;
    await api("DELETE", "/api/products/" + b.dataset.delProd);
    viewMenu();
  })));
}

// ------------------------------------------------------------ stollar sozlamasi (admin)

async function viewTablesAdmin() {
  const [halls, tables] = await Promise.all([api("GET", "/api/halls"), api("GET", "/api/tables")]);
  const hallName = (id) => (halls.find((h) => h.id === id) || {}).name || "—";
  layout(`
    <div class="two-col">
      <div class="panel">
        <div class="toolbar"><h2>Zallar</h2><button class="btn primary small" id="add-hall">+ Zal qo'shish</button></div>
        <p class="muted">Masalan: Asosiy zal, Banket zali, Kabinalar, Yozgi terassa</p>
        <table class="list">
          ${halls.map((h) => `
            <tr><td><b>${esc(h.name)}</b><div class="muted">${h.tables} ta stol ·
              xizmat haqi ${h.service_percent === null ? `${percent(state.settings.service_percent)} (umumiy)` : percent(h.service_percent)}</div></td>
              <td class="right" style="white-space:nowrap">
                <button class="btn small" data-edit-hall="${h.id}">✏️</button>
                <button class="btn small danger" data-del-hall="${h.id}">🗑</button>
              </td></tr>`).join("") || `<tr><td class="muted">Zal yo'q</td></tr>`}
        </table>
      </div>
      <div class="panel">
        <div class="toolbar"><h2>Stollar va kabinalar</h2><button class="btn primary small" id="add">+ Stol qo'shish</button></div>
        <table class="list">
          <thead><tr><th>Nomi</th><th>Zal</th><th>O'rinlar</th><th>Holati</th><th></th></tr></thead>
          <tbody>
            ${tables.map((t) => `
              <tr><td>${esc(t.name)}</td><td>${esc(hallName(t.hall_id))}</td><td>${t.seats}</td>
                <td><span class="badge">${t.order ? "Band" : "Bo'sh"}</span></td>
                <td class="right" style="white-space:nowrap">
                  <button class="btn small" data-edit="${t.id}">✏️</button>
                  <button class="btn small danger" data-del="${t.id}">🗑</button>
                </td></tr>`).join("") || `<tr><td colspan="5" class="muted">Stol yo'q</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`);

  const hallForm = (h = {}) => openModal(`
    <form id="f"><h2>${h.id ? "Zalni tahrirlash" : "Yangi zal"}</h2>
      <label><span>Nomi</span><input name="name" value="${esc(h.name || "")}" placeholder="Banket zali" required></label>
      <label><span>Xizmat haqi, % <i>(bo'sh qoldirilsa umumiy: ${percent(state.settings.service_percent)})</i></span>
        <input name="service_percent" type="number" min="0" max="100" step="0.5"
          value="${ifNull(h.service_percent, "")}" placeholder="${state.settings.service_percent}"></label>
      <label><span>Tartib raqami</span><input name="sort" type="number" value="${ifNull(h.sort, halls.length)}"></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    await api(h.id ? "PUT" : "POST", "/api/halls" + (h.id ? "/" + h.id : ""), formData(e.target));
    closeModal();
    toast("Saqlandi");
    viewTablesAdmin();
  })));

  const form = (t = {}) => {
    const hallId = ifNull(t.hall_id, (halls[0] || {}).id);
    const inHall = tables.filter((x) => x.hall_id === hallId).length;
    openModal(`
      <form id="f"><h2>${t.id ? "Stolni tahrirlash" : "Yangi stol"}</h2>
        <label><span>Zal</span>
          <select name="hall_id">
            <option value="">— Zalsiz —</option>
            ${halls.map((h) => `<option value="${h.id}" ${h.id === hallId ? "selected" : ""}>${esc(h.name)}</option>`).join("")}
          </select></label>
        <label><span>Nomi (masalan: Stol 5, Kabina 2)</span><input name="name" value="${esc(t.name || `Stol ${inHall + 1}`)}" required></label>
        <label><span>O'rinlar soni</span><input name="seats" type="number" min="1" value="${ifNull(t.seats, 4)}"></label>
        <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
      </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      await api(t.id ? "PUT" : "POST", "/api/tables" + (t.id ? "/" + t.id : ""), formData(e.target));
      closeModal();
      toast("Saqlandi");
      viewTablesAdmin();
    })));
  };

  $("#add-hall").addEventListener("click", () => hallForm());
  $("#add").addEventListener("click", () => form());
  $$("[data-edit-hall]").forEach((b) => b.addEventListener("click", () =>
    hallForm(halls.find((h) => h.id === +b.dataset.editHall))));
  $$("[data-del-hall]").forEach((b) => b.addEventListener("click", safe(async () => {
    if (!confirm("Zalni o'chirasizmi?")) return;
    await api("DELETE", "/api/halls/" + b.dataset.delHall);
    viewTablesAdmin();
  })));
  $$("[data-edit]").forEach((b) => b.addEventListener("click", () =>
    form(tables.find((t) => t.id === +b.dataset.edit))));
  $$("[data-del]").forEach((b) => b.addEventListener("click", safe(async () => {
    if (!confirm("Stolni o'chirasizmi?")) return;
    await api("DELETE", "/api/tables/" + b.dataset.del);
    viewTablesAdmin();
  })));
}

// ------------------------------------------------------------ xodimlar (admin)

const PERMISSION_LIST = [
  ["tables", "tables", "Stollar va buyurtmalar"],
  ["cashier", "cashier", "Kassa (to'lov, bekor qilish)"],
  ["kitchen", "kitchen", "Oshxona ekrani"],
  ["reports", "reports", "Hisobot"],
  ["menu", "menu", "Menyu"],
  ["crm", "crm", "CRM (mijozlar, qarzlar)"],
  ["finance", "finance", "Moliya (kassa, kirim-chiqim)"],
  ["halls", "halls", "Zallar va stollar"],
  ["printers", "printer", "Printerlar"],
  ["users", "users", "Xodimlar"],
  ["settings", "settings", "Sozlamalar"],
  ["journal", "journal", "Jurnal (barcha amallar)"],
  ["integrations", "plug", "Integratsiyalar (Telegram)"],
];
const ROLE_DEFAULTS = {
  admin: PERMISSION_LIST.map(([k]) => k),
  cashier: ["tables", "cashier", "kitchen", "reports", "crm"],
  waiter: ["tables"],
  cook: ["kitchen"],
};
const ROLE_ICONS = { cashier: "cashier", waiter: "tables", cook: "kitchen", admin: "settings" };

async function viewUsers() {
  const users = await api("GET", "/api/users");
  const active = users.filter((u) => u.active);
  const count = (role) => active.filter((u) => u.role === role).length;
  const initials = (u) => ((u.first_name || "")[0] || "") + ((u.last_name || "")[0] || "");
  const view = layout(`
    <div class="panel">
      <div class="toolbar"><h2>Xodimlar</h2><button class="btn primary" id="add">+ Xodim qo'shish</button></div>
      <table class="list users-table">
        <thead><tr><th>#</th><th>Ismi</th><th>Username</th><th>Rol</th><th>Telefon</th><th>Ruxsatlar</th><th>Holati</th><th></th></tr></thead>
        <tbody>
          ${users.map((u, i) => `
            <tr class="${u.active ? "" : "inactive"}">
              <td class="muted">${i + 1}</td>
              <td><div class="person"><span class="avatar-sm role-${u.role}">${esc(initials(u).toUpperCase())}</span>
                ${esc(u.full_name)}</div></td>
              <td><code>${esc(u.username)}</code></td>
              <td><span class="role-badge role-${u.role}">${ROLE_NAMES[u.role]}</span></td>
              <td>${esc(u.phone || "—")}</td>
              <td class="muted">${u.role === "admin" ? "Hammasi" : `${u.permissions.length} / ${PERMISSION_LIST.length}`}</td>
              <td>${u.active ? `<span class="badge">Faol</span>` : `<span class="badge off">Bloklangan</span>`}</td>
              <td class="right" style="white-space:nowrap">
                <button class="btn small" data-edit="${u.id}">✏️</button>
                ${u.id !== state.user.id && u.active ? `<button class="btn small danger" data-del="${u.id}">🗑</button>` : ""}
              </td>
            </tr>`).join("")}
        </tbody>
      </table>
      <p class="muted users-summary">Jami: ${active.length} ta xodim ·
        ${Object.keys(ROLE_NAMES).map((r) => `${ROLE_NAMES[r]}: ${count(r)}`).join(" · ")}</p>
    </div>`);

  $("#add", view).addEventListener("click", () => userForm());
  $$("[data-edit]", view).forEach((b) => b.addEventListener("click", () =>
    userForm(users.find((u) => u.id === +b.dataset.edit))));
  $$("[data-del]", view).forEach((b) => b.addEventListener("click", safe(async () => {
    const u = users.find((x) => x.id === +b.dataset.del);
    if (!confirm(`${u.full_name} bloklansinmi? U tizimga kira olmaydi (tarixi saqlanadi).`)) return;
    await api("DELETE", "/api/users/" + u.id);
    toast("Xodim bloklandi");
    viewUsers();
  })));
}

function userForm(u = null) {
  const isNew = !u;
  u = u || { role: "cashier", permissions: ROLE_DEFAULTS.cashier, active: 1 };
  const roles = Object.keys(ROLE_NAMES).filter((r) => r !== "admin" || state.user.role === "admin" || u.role === "admin");
  openModal(`
    <form id="f" class="user-form">
      <div class="modal-head"><h2>${isNew ? "Yangi xodim qo'shish" : "Xodimni tahrirlash"}</h2>
        <button type="button" class="icon-btn" data-close aria-label="Yopish">✕</button></div>
      <div class="grid-2">
        <label><span>Ismi *</span><input name="first_name" value="${esc(u.first_name || "")}" required></label>
        <label><span>Familiyasi</span><input name="last_name" value="${esc(u.last_name || "")}"></label>
        <label><span>Username *</span><input name="username" value="${esc(u.username || "")}" ${isNew ? "required" : "disabled"}
          autocomplete="off" autocapitalize="off"></label>
        <label><span>${isNew ? "Parol *" : "Yangi parol"}</span><input name="password" type="password" minlength="4"
          ${isNew ? "required" : `placeholder="O'zgartirmaslik uchun bo'sh"`} autocomplete="new-password"></label>
      </div>
      <div class="grid-2">
        <div>
          <span class="field-label">Rol *</span>
          <div class="role-cards">
            ${roles.map((r) => `
              <label class="role-card"><input type="radio" name="role" value="${r}" ${r === u.role ? "checked" : ""}>
                <span>${icon(ROLE_ICONS[r])}${ROLE_NAMES[r]}</span></label>`).join("")}
          </div>
        </div>
        <label><span>Telefon</span><input name="phone" type="tel" value="${esc(u.phone || "")}" placeholder="+998 90 123 45 67"></label>
      </div>
      <div class="perm-head">
        <span class="field-label">🛡️ Bo'limlarga kirish ruxsati</span>
        <small class="muted">Administrator = barcha bo'limlarga kiradi</small>
      </div>
      <div class="perm-box">
        <div class="perm-tools">
          <button type="button" class="btn small" id="perm-all">✅ Hammasini tanlash</button>
          <button type="button" class="btn small" id="perm-none">✖ Hammasini olib tashlash</button>
        </div>
        <div class="perm-grid">
          ${PERMISSION_LIST.map(([k, ic, name]) => `
            <label class="perm-item"><input type="checkbox" name="perm" value="${k}" ${u.permissions.includes(k) ? "checked" : ""}>
              ${icon(ic)}<span>${name}</span></label>`).join("")}
        </div>
      </div>
      ${isNew ? "" : `<label class="check-line"><input type="checkbox" name="active" ${u.active ? "checked" : ""}> Faol (tizimga kira oladi)</label>`}
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">💾 Saqlash</button></div>
    </form>`, (m) => {
    const boxes = $$("input[name=perm]", m);
    const role = () => $("input[name=role]:checked", m).value;
    const syncAdmin = () => {
      const admin = role() === "admin";
      boxes.forEach((b) => { b.disabled = admin; if (admin) b.checked = true; });
      $("#perm-all", m).disabled = $("#perm-none", m).disabled = admin;
    };
    $$("input[name=role]", m).forEach((r) => r.addEventListener("change", () => {
      // Rol tanlanganda uning standart ruxsatlari belgilanadi, keyin qo'lda o'zgartirish mumkin
      boxes.forEach((b) => { b.checked = ROLE_DEFAULTS[role()].includes(b.value); });
      syncAdmin();
    }));
    $("#perm-all", m).addEventListener("click", () => boxes.forEach((b) => { b.checked = true; }));
    $("#perm-none", m).addEventListener("click", () => boxes.forEach((b) => { b.checked = false; }));
    syncAdmin();

    $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const f = e.target;
      const data = {
        first_name: f.first_name.value, last_name: f.last_name.value, phone: f.phone.value,
        role: role(), permissions: boxes.filter((b) => b.checked).map((b) => b.value),
      };
      if (!data.permissions.length) throw new Error("Kamida bitta bo'limga ruxsat bering");
      if (f.password.value) data.password = f.password.value;
      if (isNew) data.username = f.username.value;
      else data.active = f.active.checked;
      await api(isNew ? "POST" : "PUT", "/api/users" + (isNew ? "" : "/" + u.id), data);
      closeModal();
      toast("Saqlandi ✅");
      if (!isNew && u.id === state.user.id) state.user = await api("GET", "/api/me");
      viewUsers();
    }));
  });
}

function viewNoAccess() {
  layout(`<div class="panel"><h2>Ruxsat yo'q</h2>
    <p class="muted">Sizga hali birorta bo'limga ruxsat berilmagan. Administratorga murojaat qiling.</p></div>`);
}

// ------------------------------------------------------------ printerlar (admin)

async function viewPrinters() {
  const printers = await api("GET", "/api/printers");
  const where = (p) => p.kind === "network" ? `${esc(p.address)}:${p.port}` : esc(p.address);
  layout(`
    <div class="panel" style="max-width:900px">
      <div class="toolbar"><h2>Oshxona printerlari</h2><button class="btn primary small" id="add">+ Printer qo'shish</button></div>
      <p class="muted">Har bir taomga Menyu bo'limida printer biriktiriladi. Buyurtmada "🖨️ Oshxona printeriga"
        bosilganda har bir taom o'z printeridan chiqadi.</p>
      <table class="list">
        <thead><tr><th>Nomi</th><th>Ulanish</th><th>Manzil</th><th>Qog'oz</th><th></th></tr></thead>
        <tbody>
          ${printers.map((p) => `
            <tr><td><b>${esc(p.name)}</b></td><td>${PRINTER_KINDS[p.kind]}</td><td>${where(p)}</td><td>${p.width} mm</td>
              <td class="right" style="white-space:nowrap">
                <button class="btn small" data-test="${p.id}">🧪 Sinov</button>
                <button class="btn small" data-edit="${p.id}">✏️</button>
                <button class="btn small danger" data-del="${p.id}">🗑</button>
              </td></tr>`).join("") || `<tr><td colspan="5" class="muted">Printer yo'q</td></tr>`}
        </tbody>
      </table>
    </div>`);

  const form = (p = { kind: "system", port: 9100, width: 80 }) => openModal(`
    <form id="f"><h2>${p.id ? "Printerni tahrirlash" : "Yangi printer"}</h2>
      <label><span>Nomi (masalan: Oshxona, Salatxona, Bar)</span><input name="name" value="${esc(p.name || "")}" required></label>
      <div class="kind-switch">
        ${["system", "network"].map((k) => `
          <label class="kind-option"><input type="radio" name="kind" value="${k}" ${k === p.kind ? "checked" : ""}>
            <span>${k === "system" ? "🔌 USB / Wi-Fi<small>Kompyuterga o'rnatilgan printer</small>"
                                   : "🌐 Tarmoq (IP)<small>LAN / Wi-Fi termoprinter, 9100-port</small>"}</span></label>`).join("")}
      </div>
      <div id="system-box">
        <span class="field-label">Kompyuterga ulangan printerlar</span>
        <div class="device-list" id="system-list"><p class="muted">Qidirilmoqda...</p></div>
        <button type="button" class="btn small" id="system-refresh">🔄 Yangilash</button>
      </div>
      <div id="network-box">
        <div class="grid-2">
          <label><span>IP manzil</span><input name="ip" value="${p.kind === "network" ? esc(p.address || "") : ""}" placeholder="192.168.1.100"></label>
          <label><span>Port</span><input name="port" type="number" value="${p.port || 9100}"></label>
        </div>
        <button type="button" class="btn small" id="scan-btn">🔍 Tarmoqdan qidirish</button>
        <div class="device-list" id="scan-list"></div>
      </div>
      <label><span>Qog'oz kengligi</span>
        <select name="width">
          <option value="80" ${p.width >= 80 ? "selected" : ""}>80 mm</option>
          <option value="58" ${p.width < 80 ? "selected" : ""}>58 mm</option>
        </select></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => {
    let systemName = p.kind === "system" || p.kind === "windows" ? p.address : "";
    const kind = () => $("input[name=kind]:checked", m).value;
    const sync = () => {
      $("#system-box", m).classList.toggle("hidden", kind() !== "system");
      $("#network-box", m).classList.toggle("hidden", kind() !== "network");
    };

    async function loadSystem() {
      const list = $("#system-list", m);
      list.innerHTML = `<p class="muted">Qidirilmoqda...</p>`;
      let devices;
      try {
        devices = await api("GET", "/api/printers/system");
      } catch (e) {
        list.innerHTML = `<p class="error">${esc(e.message)}</p>`;
        return;
      }
      if (systemName && !devices.some((d) => d.name === systemName)) {
        devices.unshift({ name: systemName, connection: "hozir topilmadi", port: "" });
      }
      list.innerHTML = devices.map((d) => `
        <label class="device ${d.name === systemName ? "selected" : ""}">
          <input type="radio" name="device" value="${esc(d.name)}" ${d.name === systemName ? "checked" : ""}>
          <span><b>${esc(d.name)}</b><small>${esc(d.connection)}${d.port && d.port !== d.connection ? " · " + esc(d.port) : ""}</small></span>
        </label>`).join("") || `<p class="muted">Printer topilmadi. Printerni USB yoki Wi-Fi orqali ulab, Windows'da o'rnating
          (drayverini o'rnating), so'ng "Yangilash" ni bosing.</p>`;
      $$("input[name=device]", list).forEach((r) => r.addEventListener("change", () => {
        systemName = r.value;
        $$(".device", list).forEach((d) => d.classList.toggle("selected", d.contains(r)));
      }));
    }

    $$("input[name=kind]", m).forEach((r) => r.addEventListener("change", sync));
    $("#system-refresh", m).addEventListener("click", loadSystem);
    $("#scan-btn", m).addEventListener("click", safe(async () => {
      const btn = $("#scan-btn", m);
      const list = $("#scan-list", m);
      btn.disabled = true;
      list.innerHTML = `<p class="muted">Tarmoq tekshirilmoqda (bir necha soniya)...</p>`;
      try {
        const found = await api("GET", "/api/printers/scan");
        list.innerHTML = found.map((d) => `
          <button type="button" class="device" data-ip="${esc(d.address)}"><b>${esc(d.address)}</b><small>9100-port ochiq</small></button>`).join("")
          || `<p class="muted">Tarmoqda printer topilmadi. Printer va kompyuter bitta Wi-Fi/tarmoqda ekanini tekshiring.</p>`;
        $$("[data-ip]", list).forEach((b) => b.addEventListener("click", () => {
          $("input[name=ip]", m).value = b.dataset.ip;
          $$(".device", list).forEach((d) => d.classList.toggle("selected", d === b));
        }));
      } finally {
        btn.disabled = false;
      }
    }));
    sync();
    loadSystem();

    $("#f", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const f = formData(e.target);
      const data = { name: f.name, kind: kind(), width: f.width, port: f.port };
      data.address = data.kind === "system" ? systemName : f.ip.trim();
      if (!data.address) throw new Error(data.kind === "system" ? "Ro'yxatdan printerni tanlang" : "IP manzilni kiriting");
      await api(p.id ? "PUT" : "POST", "/api/printers" + (p.id ? "/" + p.id : ""), data);
      closeModal();
      toast("Saqlandi");
      viewPrinters();
    }));
  });

  $("#add").addEventListener("click", () => form());
  $$("[data-edit]").forEach((b) => b.addEventListener("click", () =>
    form((({ kind, ...rest }) => ({ ...rest, kind: kind === "windows" ? "system" : kind }))(printers.find((p) => p.id === +b.dataset.edit)))));
  $$("[data-test]").forEach((b) => b.addEventListener("click", safe(async () => {
    b.disabled = true;
    try {
      await api("POST", `/api/printers/${b.dataset.test}/test`);
      toast("Sinov cheki yuborildi ✅");
    } finally {
      b.disabled = false;
    }
  })));
  $$("[data-del]").forEach((b) => b.addEventListener("click", safe(async () => {
    if (!confirm("Printerni o'chirasizmi? Unga biriktirilgan taomlar printersiz qoladi.")) return;
    await api("DELETE", "/api/printers/" + b.dataset.del);
    viewPrinters();
  })));
}

// ------------------------------------------------------------ oshxona ekrani

let kitchenTimer = null;

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    osc.frequency.value = 880;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
  } catch { /* ovoz ishlamasa ham ekran ishlayveradi */ }
}

function minutesAgo(s) {
  const t = new Date(s.replace(" ", "T"));
  return Math.max(0, Math.floor((Date.now() - t) / 60000));
}

async function viewKitchen() {
  const printers = await api("GET", "/api/printers");
  let station = "";
  try { station = localStorage.getItem("kitchen-station") || ""; } catch { /* ruxsat yo'q */ }
  let known = null;

  const view = layout(`
    <div class="toolbar">
      <h2>🍳 Oshxona</h2>
      <select id="station" style="width:auto">
        <option value="">Barcha bo'limlar</option>
        ${printers.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}
        <option value="none">Printersiz taomlar</option>
      </select>
    </div>
    <div class="kitchen-grid" id="tickets"></div>`);
  const select = $("#station", view);
  select.value = station;
  if (select.value !== station) station = "";
  select.addEventListener("change", () => {
    station = select.value;
    try { localStorage.setItem("kitchen-station", station); } catch { /* ruxsat yo'q */ }
    known = null;
    refresh();
  });

  async function refresh() {
    if (!location.hash.startsWith("#/kitchen") || !document.body.contains(view)) return stop();
    let tickets;
    try {
      tickets = await api("GET", "/api/kitchen" + (station ? "?printer_id=" + station : ""));
    } catch {
      return;
    }
    if (!document.body.contains(view)) return;
    if (known && tickets.some((t) => !known.has(t.id))) beep();
    known = new Set(tickets.map((t) => t.id));
    $("#tickets", view).innerHTML = tickets.map((t) => {
      const mins = minutesAgo(t.created_at);
      return `
        <div class="ticket ${mins >= 15 ? "late" : ""}">
          <div class="ticket-head">
            <b>${t.type === "takeaway" ? "🥡" : "🪑"} ${esc(place(t))} · #${t.order_id}</b>
            <span>${mins} daq</span>
          </div>
          <div class="muted">${esc(t.waiter_name || "")}${t.printer_name ? " · " + esc(t.printer_name) : ""}</div>
          <ul>${t.lines.map((l) => l.qty > 0
            ? `<li><b>${l.qty} ×</b> ${esc(l.name)}</li>`
            : `<li class="cancel"><b>BEKOR ${-l.qty} ×</b> ${esc(l.name)}</li>`).join("")}</ul>
          <button class="btn primary big" data-ready="${t.id}">✅ Tayyor</button>
        </div>`;
    }).join("") || `<p class="muted">Hozircha yangi buyurtma yo'q</p>`;
    $$("[data-ready]", view).forEach((b) => b.addEventListener("click", safe(async () => {
      b.disabled = true;
      await api("POST", `/api/kitchen/${b.dataset.ready}/ready`);
      refresh();
    })));
  }

  function stop() {
    clearInterval(kitchenTimer);
    kitchenTimer = null;
  }

  stop();
  kitchenTimer = setInterval(refresh, 5000);
  refresh();
}

// ------------------------------------------------------------ sozlamalar (admin)

async function viewSettings() {
  await loadSettings();
  const [halls, network] = await Promise.all([api("GET", "/api/halls"), api("GET", "/api/network")]);
  const s = state.settings;
  layout(`
    <form class="panel settings" id="f" style="max-width:640px">
      <h2>⚙️ Sozlamalar</h2>
      <label><span>Kafe nomi (chekda chiqadi)</span><input name="cafe_name" value="${esc(s.cafe_name)}" required></label>
      <label><span>Xizmat haqi, % — stolda o'tirganlarga umumiy summadan qo'shiladi</span>
        <input name="service_percent" type="number" min="0" max="100" step="0.5" value="${s.service_percent}"></label>
      <p class="muted">Olib ketish buyurtmalariga xizmat haqi qo'shilmaydi. 0 qo'yilsa xizmat haqi olinmaydi.</p>
      <div id="example" class="example"></div>
      ${halls.length ? `
        <h3>Zallar bo'yicha</h3>
        <table class="list">
          ${halls.map((h) => `<tr><td>${esc(h.name)}</td><td class="right">${h.service_percent === null
            ? `<span class="muted">umumiy</span>` : percent(h.service_percent)}</td></tr>`).join("")}
        </table>
        <p class="muted">Kabina yoki banket zali uchun boshqa foiz kerak bo'lsa, "🏛️ Zallar" bo'limida zalni tahrirlang.</p>` : ""}
      <div class="actions"><button class="btn primary">Saqlash</button></div>
    </form>
    <div class="panel network-panel" style="max-width:640px">
      <h3>📶 Telefon, planshet va boshqa kompyuterlardan kirish</h3>
      <p class="muted">Qurilma shu kompyuter bilan <b>bitta Wi-Fi / tarmoqda</b> bo'lsin. Brauzerda quyidagi manzilni oching:</p>
      ${network.main ? `<div class="lan-url"><code>${esc(network.main)}</code>
        <button type="button" class="btn small" data-copy="${esc(network.main)}">Nusxa olish</button></div>`
        : `<p class="error">Kompyuter Wi-Fi yoki tarmoqqa ulanmagan ko'rinadi.</p>`}
      ${network.others.length ? `<details class="muted"><summary>Boshqa manzillar (VPN, virtual adapterlar — odatda kerak emas)</summary>
        ${network.others.map((u) => `<div><code>${esc(u)}</code></div>`).join("")}</details>` : ""}
      <p><button type="button" class="btn" id="install-help">📲 Telefonga ilova qilib o'rnatish — yo'riqnoma</button></p>
      <h4>Ochilmasa:</h4>
      <ol class="muted help-list">
        <li>Dastur papkasidagi <b>TARMOQQA_RUXSAT.bat</b> ni ishga tushiring va administrator ruxsatiga <b>"Да"</b> bosing.
          U fayervolda ${network.port}-portni ochadi va Python uchun qo'yilgan taqiqni olib tashlaydi.</li>
        <li>Telefonda <b>mobil internet emas, Wi-Fi</b> yoqilganini va aynan shu Wi-Fi'ga ulanganini tekshiring.</li>
        <li>Mehmonlar (Guest) Wi-Fi'da qurilmalar bir-birini ko'rmaydi — asosiy Wi-Fi'ga ulaning.</li>
        <li>Manzil boshida <b>http://</b> bo'lsin (https emas) va oxirida <b>:${network.port}</b> bo'lsin.</li>
      </ol>
    </div>`);

  $("#install-help").addEventListener("click", showInstallHelp);
  $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      toast("Nusxa olindi");
    } catch {
      toast(b.dataset.copy);
    }
  }));
  const input = $("input[name=service_percent]");
  const example = () => {
    const p = +input.value || 0;
    $("#example").innerHTML = p
      ? `Misol: buyurtma ${money(100000)} → xizmat haqi <b>${money(100000 * p / 100)}</b> → jami <b>${money(100000 + 100000 * p / 100)}</b>`
      : "";
  };
  input.addEventListener("input", example);
  example();
  $("#f").addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    state.settings = await api("PUT", "/api/settings", formData(e.target));
    toast("Saqlandi ✅");
    viewSettings();
  }));
}

// ------------------------------------------------------------ jurnal (barcha amallar tarixi)

const JOURNAL_ICONS = { sales: "sales", orders: "tables", finance: "finance", crm: "crm", menu: "box",
  users: "users", settings: "settings", auth: "logout" };
// So'rov maydonlari nomlari (batafsil oynada)
const FIELD_NAMES = {
  name: "Nomi", price: "Narxi", cost: "Tannarxi", amount: "Summa", method: "Usul", account: "Hisob", phone: "Telefon",
  comment: "Izoh", discount: "Chegirma", reason: "Sabab", qty: "Soni", gender: "Jinsi", due_date: "To'lov muddati",
  balance: "Yangi balans", category_id: "Kategoriya ID", printer_id: "Printer ID", product_id: "Mahsulot ID",
  customer_id: "Mijoz ID", supplier_id: "Ta'minotchi ID", type_id: "Tranzaksiya turi ID", direction: "Yo'nalish",
  first_name: "Ism", last_name: "Familiya", full_name: "F.I.Sh", username: "Login", role: "Lavozim",
  permissions: "Ruxsatlar", active: "Faol", password: "Parol", image: "Rasm", file_name: "Fayl", data: "Fayl",
  cafe_name: "Kafe nomi", service_percent: "Xizmat haqi, %", seats: "O'rinlar", hall_id: "Zal ID", sort: "Tartib",
  kind: "Turi", address: "Manzil", port: "Port", width: "Qog'oz kengligi", enabled: "Yoqilgan", token: "Token",
  chats: "Chatlar", categories: "Bo'limlar", table_id: "Stol ID", type: "Turi", remove_image: "Rasmni olib tashlash",
};

function requestValue(v) {
  if (v === true) return "ha";
  if (v === false) return "yo'q";
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.map((x) => typeof x === "object" ? (x.title || x.name || x.id) : x).join(", ") || "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function journalDetail(id) {
  return api("GET", `/api/journal/${id}`).then((j) => {
    const d = j.details || {};
    const request = Object.keys(d.request || {});
    const orderId = (j.entity || "").indexOf("order:") === 0 ? j.entity.slice(6) : null;
    openModal(`
      <div class="journal-detail">
        <div class="modal-head"><h2><span class="jr-icon cat-${esc(j.category)}">${icon(JOURNAL_ICONS[j.category] || "list")}</span>
          ${esc(j.title)}</h2><button type="button" class="icon-btn" data-close>✕</button></div>
        <div class="jd-meta">
          <div><small>Vaqti</small><b>${esc(j.created_at)}</b></div>
          <div><small>Kim qildi</small><b>${esc(j.user_name || "—")}</b>
            ${j.username ? `<span class="muted">${esc(j.username)} · ${ROLE_NAMES[j.role] || ""}</span>` : ""}</div>
          <div><small>Bo'lim</small><b>${esc(j.category_name)}</b></div>
        </div>
        ${j.summary ? `<p class="jd-summary">${esc(j.summary)}</p>` : ""}
        ${(d.fields || []).length ? `
          <table class="list jd-fields">${d.fields.map(([k, v]) =>
            `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</table>` : ""}
        ${(d.items || []).length ? `
          <h3>Taomlar</h3>
          <table class="list jd-items"><thead><tr><th>Nomi</th><th class="right">Soni</th>
            ${d.items[0].price !== undefined ? `<th class="right">Narxi</th><th class="right">Summa</th>` : ""}</tr></thead>
            <tbody>${d.items.map((i) => `<tr><td>${esc(i.name)}</td><td class="right">${i.qty}</td>
              ${i.price !== undefined ? `<td class="right">${money(i.price)}</td><td class="right">${money(i.price * i.qty)}</td>` : ""}</tr>`).join("")}
            </tbody></table>` : ""}
        ${request.length ? `
          <details class="jd-request"><summary>Kiritilgan ma'lumotlar</summary>
            <table class="list">${request.map((k) => `<tr><th>${esc(FIELD_NAMES[k] || k)}</th>
              <td>${esc(requestValue(d.request[k]))}</td></tr>`).join("")}</table>
          </details>` : ""}
        <div class="actions">
          ${orderId && can("tables", "cashier", "reports") ? `<a class="btn" href="#/order/${orderId}">Buyurtmani ochish</a>` : ""}
          <button type="button" class="btn primary" data-close>Yopish</button>
        </div>
      </div>`);
  });
}

async function viewJournal() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const q = new URLSearchParams();
  ["from", "to", "user_id", "category", "q"].forEach((k) => { if (params.get(k)) q.set(k, params.get(k)); });
  const d = await api("GET", "/api/journal?" + q.toString());
  const sel = (name, options, value) => `<select name="${name}" style="width:auto">${options.map(([k, v]) =>
    `<option value="${k}" ${String(k) === (value || "") ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  const cats = Object.keys(d.categories).map((k) => [k, d.categories[k]]);
  const rowHtml = (i) => `
    <tr class="clickable" data-id="${i.id}">
      <td class="nowrap">${esc(i.created_at.slice(0, 10))}<br><b>${esc(i.created_at.slice(11, 16))}</b></td>
      <td>${esc(i.user_name || "—")}</td>
      <td><span class="jr-cat cat-${esc(i.category)}">${icon(JOURNAL_ICONS[i.category] || "list")}${esc(d.categories[i.category] || i.category)}</span></td>
      <td><b>${esc(i.title)}</b><div class="muted jr-summary">${esc(i.summary || "")}</div></td>
      <td class="right jr-more">${icon("chevron")}</td>
    </tr>`;
  const view = layout(`
    <div class="toolbar"><h2>Jurnal</h2><span class="muted">${d.total} ta amal</span></div>
    <form class="filters" id="filters">
      <input type="date" name="from" value="${d.from}" style="width:auto">
      <input type="date" name="to" value="${d.to}" style="width:auto">
      ${sel("user_id", [["", "Barcha xodimlar"], ...d.users.map((u) => [u.id, u.full_name])], params.get("user_id"))}
      ${sel("category", [["", "Barcha bo'limlar"], ...cats], params.get("category"))}
      <input type="search" name="q" placeholder="Qidirish: taom, mijoz, summa..." value="${esc(params.get("q") || "")}" style="width:220px">
      <button class="btn primary">Ko'rsatish</button>
    </form>
    <div class="panel">
      ${d.items.length ? `
        <div class="table-scroll"><table class="list journal-table">
          <thead><tr><th>Vaqt</th><th>Xodim</th><th>Bo'lim</th><th>Amal</th><th></th></tr></thead>
          <tbody id="jr-body">${d.items.map(rowHtml).join("")}</tbody>
        </table></div>
        ${d.total > d.items.length ? `<p style="text-align:center"><button class="btn" id="more">Yana ko'rsatish</button></p>` : ""}`
        : `<p class="muted">Tanlangan davrda amallar yo'q</p>`}
    </div>`);
  $("#filters", view).addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new URLSearchParams();
    new FormData(e.target).forEach((v, k) => { if (v) f.set(k, v); });
    go("#/journal?" + f.toString());
  });
  view.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) journalDetail(tr.dataset.id).catch((err) => toast(err.message, true));
  });
  let offset = d.items.length;
  const more = $("#more", view);
  if (more) more.addEventListener("click", safe(async () => {
    q.set("offset", offset);
    const next = await api("GET", "/api/journal?" + q.toString());
    $("#jr-body", view).insertAdjacentHTML("beforeend", next.items.map(rowHtml).join(""));
    offset += next.items.length;
    if (offset >= next.total || !next.items.length) more.remove();
  }));
}

// ------------------------------------------------------------ integratsiyalar

// Yangi integratsiya qo'shish: shu ro'yxatga yozuv va (tayyor bo'lsa) sahifa
const INTEGRATIONS = [
  { key: "telegram", icon: "telegram", name: "Telegram bot", href: "#/integrations/telegram",
    text: "Xodimlar uchun: sotuv, kirim-chiqim, qarz va boshqa amallar haqida Telegram'ga xabar keladi." },
  { key: "customer_bot", icon: "crm", name: "Mijozlar boti", href: "#/integrations/customer-bot",
    text: "Mijozlar uchun: xarid cheki, qarz balansi va siz yuborgan xabarlar Telegram'da." },
];

async function viewIntegrations() {
  const list = await api("GET", "/api/integrations");
  const status = {};
  list.forEach((i) => { status[i.key] = i; });
  layout(`
    <div class="toolbar"><h2>Integratsiyalar</h2></div>
    <div class="integration-grid">
      ${INTEGRATIONS.map((it) => {
        const s = status[it.key];
        const badge = it.soon ? `<span class="badge">Tez orada</span>`
          : s && s.enabled ? `<span class="badge ok">Ulangan</span>` : `<span class="badge">Ulanmagan</span>`;
        const tag = it.soon ? "div" : "a";
        return `
          <${tag} class="integration-card ${it.soon ? "soon" : ""}" ${it.soon ? "" : `href="${it.href}"`}>
            <div class="ic-top"><span class="ic-logo ic-${it.key}">${icon(it.icon)}</span>${badge}</div>
            <b>${esc(it.name)}</b>
            <p class="muted">${esc(it.text)}</p>
            ${it.soon ? "" : `<span class="ic-link">Sozlash ${icon("chevron")}</span>`}
          </${tag}>`;
      }).join("")}
    </div>`);
}

async function viewTelegram(cfgArg) {
  if (state.leaveHook) { state.leaveHook(); state.leaveHook = null; }  // oldingi kutish taymeri
  const cfg = cfgArg || await api("GET", "/api/integrations/telegram");
  const botLink = cfg.bot ? `https://t.me/${cfg.bot.username}` : "";
  let body;
  if (!cfg.token_set) {
    // 1-qadam: faqat token
    body = `
      <section class="panel tg-card">
        <div class="tg-hero">${icon("telegram")}</div>
        <h3>Telegram botni ulash</h3>
        <p class="muted">Telegram'da <a href="https://t.me/BotFather" target="_blank" rel="noopener"><b>@BotFather</b></a> ni oching,
          <b>/newbot</b> yozing va bot nomini bering. U bergan <b>tokenni</b> shu yerga qo'ying.</p>
        <form id="tg-connect" class="row-input">
          <input id="tg-token" autocomplete="off" spellcheck="false" placeholder="123456789:AAH..." required>
          <button class="btn primary">Ulash</button>
        </form>
      </section>`;
  } else if (!cfg.chats.length) {
    // 2-qadam: botga /start
    body = `
      <section class="panel tg-card">
        <div class="tg-hero">${icon("telegram")}</div>
        <h3>@${esc(cfg.bot ? cfg.bot.username : "bot")} ulandi. Endi botga /start yozing</h3>
        <p class="muted">Xabarlar kimga borishi kerak bo'lsa, o'sha odam botni ochib <b>Start</b> ni bossin.
          Guruhga kelishi kerak bo'lsa — botni guruhga qo'shing va guruhda biror narsa yozing.</p>
        ${botLink ? `<a class="btn primary big" href="${esc(botLink)}" target="_blank" rel="noopener">${icon("telegram")} Botni ochish</a>` : ""}
        <p class="tg-wait"><span class="spinner"></span> /start kutilmoqda...</p>
        <button type="button" class="btn small" id="tg-disconnect">Boshqa bot ulash</button>
      </section>`;
  } else {
    // Ulangan
    body = `
      <section class="panel tg-card">
        <div class="tg-connected">
          <span class="tg-hero small">${icon("telegram")}</span>
          <div><b>@${esc(cfg.bot ? cfg.bot.username : "bot")}</b>
            <small class="muted">${cfg.enabled ? "Xabarlar yuborilmoqda" : "To'xtatilgan"}</small></div>
          <label class="tg-switch"><input type="checkbox" id="tg-enabled" ${cfg.enabled ? "checked" : ""}><span class="slider"></span></label>
        </div>
        ${cfg.status.last_error ? `<div class="notice error-notice">⚠️ ${esc(cfg.status.last_error)}</div>` : ""}
        <h4>Xabar boradigan chatlar</h4>
        <div class="tg-chat-list">${cfg.chats.map((c) => `
          <div class="tg-chat">${icon(String(c.id).charAt(0) === "-" ? "users" : "crm")}
            <div><b>${esc(c.title || c.id)}</b></div>
            <button type="button" class="icon-btn" data-remove="${esc(c.id)}" title="Olib tashlash">✕</button></div>`).join("")}
        </div>
        <p class="muted small-note">Yana qo'shish: ${botLink ? `<a href="${esc(botLink)}" target="_blank" rel="noopener">botni oching</a>` : "botni oching"}
          va /start bosing — chat o'zi qo'shiladi.</p>
        <details class="tg-more">
          <summary>Qaysi xabarlar boradi</summary>
          <div class="perm-grid">${Object.keys(cfg.all_categories).map((k) => `
            <label class="perm-item"><input type="checkbox" name="tg-cat" value="${k}" ${cfg.categories.indexOf(k) >= 0 ? "checked" : ""}>
              ${icon(JOURNAL_ICONS[k] || "list")}<span>${esc(cfg.all_categories[k])}</span></label>`).join("")}</div>
        </details>
        <div class="actions">
          <button type="button" class="btn danger-text" id="tg-disconnect">Uzish</button>
          <button type="button" class="btn" id="tg-test">Sinov xabari</button>
        </div>
      </section>`;
  }
  const view = layout(`
    <div class="toolbar"><a class="btn small" href="#/integrations">← Integratsiyalar</a><h2>Telegram bot</h2></div>
    ${body}`);

  const save = async (patch) => {
    const next = await api("PUT", "/api/integrations/telegram", Object.assign({
      enabled: cfg.enabled, chats: cfg.chats, categories: cfg.categories }, patch));
    viewTelegram(next);
  };
  const connect = $("#tg-connect", view);
  if (connect) connect.addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    const btn = $("button", connect);
    btn.disabled = true;
    try {
      const next = await api("POST", "/api/integrations/telegram/connect", { token: $("#tg-token", view).value.trim() });
      toast("Bot ulandi ✅");
      viewTelegram(next);
    } finally { btn.disabled = false; }
  }));
  if (cfg.token_set && !cfg.chats.length) {
    // /start yozilishini kutamiz - har 3 soniyada tekshiriladi
    const timer = setInterval(async () => {
      try {
        const next = await api("POST", "/api/integrations/telegram/link", {});
        if (next.added.length) {
          clearInterval(timer);
          toast("Ulandi: " + next.added.join(", ") + " ✅");
          viewTelegram(next);
        }
      } catch (e) { /* internet yo'q - keyinroq yana urinamiz */ }
    }, 3000);
    state.leaveHook = () => clearInterval(timer);
  }
  if (cfg.chats.length && cfg.token_set) {
    // ulangan sahifada ham yangi /start yozganlar o'zi qo'shiladi (bir marta tekshiriladi)
    api("POST", "/api/integrations/telegram/link", {}).then((next) => {
      if (next.added.length && location.hash === "#/integrations/telegram") {
        toast("Yangi chat qo'shildi: " + next.added.join(", "));
        viewTelegram(next);
      }
    }).catch(() => {});
  }
  const enabled = $("#tg-enabled", view);
  if (enabled) enabled.addEventListener("change", safe(() => save({ enabled: enabled.checked })));
  $$("[data-remove]", view).forEach((b) => b.addEventListener("click", safe(() => {
    if (!confirm("Bu chatga xabar yuborish to'xtatilsinmi?")) return;
    const chats = cfg.chats.filter((c) => String(c.id) !== b.dataset.remove);
    return save({ chats, enabled: cfg.enabled && chats.length > 0 });
  })));
  $$("input[name=tg-cat]", view).forEach((i) => i.addEventListener("change", safe(async () => {
    const categories = $$("input[name=tg-cat]:checked", view).map((x) => x.value);
    cfg.categories = categories;
    await api("PUT", "/api/integrations/telegram", { enabled: cfg.enabled, chats: cfg.chats, categories });
    toast("Saqlandi");
  })));
  const test = $("#tg-test", view);
  if (test) test.addEventListener("click", safe(async () => {
    await api("POST", "/api/integrations/telegram/test", {});
    toast("Sinov xabari yuborildi — Telegram'ni tekshiring ✅");
  }));
  const disc = $("#tg-disconnect", view);
  if (disc) disc.addEventListener("click", safe(async () => {
    if (cfg.chats.length && !confirm("Telegram bot uzilsinmi? Xabarlar boshqa yuborilmaydi.")) return;
    viewTelegram(await api("DELETE", "/api/integrations/telegram"));
  }));
}

// Mijozlarga Telegram bot orqali xabar yuborish: hammaga yoki tanlanganlarga
async function messageModal(preselected) {
  const linked = await api("GET", "/api/customers?telegram=1");
  if (!linked.length) {
    toast("Hali birorta mijoz botga ulanmagan. Mijoz botni ochib telefon raqamini yuborishi kerak", true);
    return;
  }
  const chosen = {};
  (preselected || []).forEach((c) => { chosen[c.id] = true; });
  const single = preselected && preselected.length === 1;
  openModal(`
    <form id="msg-form" class="message-form">
      <div class="modal-head"><h2>${icon("telegram")} ${single ? "Xabar: " + esc(preselected[0].name) : "Mijozlarga xabar"}</h2>
        <button type="button" class="icon-btn" data-close>✕</button></div>
      ${single ? "" : `
        <div class="target-pick">
          <label><input type="radio" name="target" value="all" ${preselected ? "" : "checked"}><span>Barcha ulangan mijozlar (${linked.length} ta)</span></label>
          <label><input type="radio" name="target" value="some" ${preselected ? "checked" : ""}><span>Tanlanganlar</span></label>
        </div>
        <div id="pick-box" class="${preselected ? "" : "hidden"}">
          <div class="picker-search">${icon("search")}<input id="pick-q" placeholder="Qidirish" autocomplete="off"></div>
          <div class="pick-list">${linked.map((c) => `
            <label class="pick-row" data-q="${esc((c.name + " " + c.phone).toLowerCase())}">
              <input type="checkbox" value="${c.id}" ${chosen[c.id] ? "checked" : ""}>
              <span><b>${esc(c.name)}</b> <small class="muted">${esc(formatPhone(c.phone))}</small></span></label>`).join("")}</div>
        </div>`}
      <label><span>Xabar matni</span><textarea name="text" rows="5" maxlength="3500" required
        placeholder="Masalan: Hurmatli mijoz! Bugun barcha ichimliklarga 20% chegirma 🎉"></textarea></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button>
        <button class="btn primary">${icon("telegram")} Yuborish</button></div>
    </form>`, (m) => {
    const box = $("#pick-box", m);
    $$("input[name=target]", m).forEach((r) => r.addEventListener("change", () =>
      box.classList.toggle("hidden", $("input[name=target]:checked", m).value !== "some")));
    const q = $("#pick-q", m);
    if (q) q.addEventListener("input", () => {
      const v = q.value.trim().toLowerCase();
      $$(".pick-row", m).forEach((row) => row.classList.toggle("hidden", v && row.dataset.q.indexOf(v) < 0));
    });
    $("textarea", m).focus();
    $("#msg-form", m).addEventListener("submit", safe(async (e) => {
      e.preventDefault();
      const body = { text: e.target.text.value };
      if (single) body.customer_ids = [preselected[0].id];
      else if ($("input[name=target]:checked", m).value === "all") body.all = true;
      else body.customer_ids = $$(".pick-row input:checked", m).map((i) => +i.value);
      const res = await api("POST", "/api/customers/message", body);
      closeModal();
      toast(`Xabar ${res.sent} ta mijozga yuborildi ✅`);
    }));
  });
}

async function viewCustomerBot(cfgArg) {
  const cfg = cfgArg || await api("GET", "/api/integrations/customer-bot");
  const botLink = cfg.bot ? `https://t.me/${cfg.bot.username}` : "";
  const body = !cfg.token_set ? `
      <section class="panel tg-card">
        <div class="tg-hero">${icon("crm")}</div>
        <h3>Mijozlar botini ulash</h3>
        <p class="muted">Bu — mijozlaringiz uchun <b>alohida</b> bot. Telegram'da
          <a href="https://t.me/BotFather" target="_blank" rel="noopener"><b>@BotFather</b></a> → <b>/newbot</b>
          (masalan "Kafe mijozlari" nomi bilan) va bergan tokenni shu yerga qo'ying.</p>
        <form id="cb-connect" class="row-input">
          <input id="cb-token" autocomplete="off" spellcheck="false" placeholder="123456789:AAH..." required>
          <button class="btn primary">Ulash</button>
        </form>
        <h4>Mijoz botda nima ko'radi</h4>
        <ul class="muted help-list">
          <li>🧾 Savdoda mijoz tanlansa — xarid cheki darhol botga keladi</li>
          <li>💰 <b>Balans</b> tugmasi — qancha qarzi borligi va to'lov muddatlari</li>
          <li>✅ Qarz to'laganda — to'lov qabul qilingani haqida xabar</li>
          <li>📢 Siz yuborgan xabarlar (aksiya, yangilik)</li>
        </ul>
      </section>` : `
      <section class="panel tg-card">
        <div class="tg-connected">
          <span class="tg-hero small">${icon("crm")}</span>
          <div><b>@${esc(cfg.bot ? cfg.bot.username : "bot")}</b>
            <small class="muted">${cfg.enabled ? "Ishlamoqda" : "To'xtatilgan"}</small></div>
          <label class="tg-switch"><input type="checkbox" id="cb-enabled" ${cfg.enabled ? "checked" : ""}><span class="slider"></span></label>
        </div>
        ${cfg.status.last_error ? `<div class="notice error-notice">⚠️ ${esc(cfg.status.last_error)}</div>` : ""}
        <div class="cb-stats">
          <div><b>${cfg.linked}</b><small class="muted">ulangan mijoz</small></div>
          <div><b>${cfg.customers}</b><small class="muted">jami mijoz</small></div>
          <div><b>${cfg.status.sent}</b><small class="muted">yuborilgan xabar</small></div>
        </div>
        <h4>Mijoz qanday ulanadi</h4>
        <p class="muted">Mijoz botni ochadi → <b>Start</b> → <b>📱 Telefon raqamni yuborish</b>. Raqami CRM'dagi mijozga mos kelsa, o'zi ulanadi.
          Havolani mijozlarga yuboring yoki kassaga QR qilib qo'ying:</p>
        <div class="lan-url"><code>${esc(botLink)}</code>
          <button type="button" class="btn small" data-copy="${esc(botLink)}">Nusxa olish</button></div>
        <h4>Nimalar yuboriladi</h4>
        <label class="perm-item"><input type="checkbox" id="cb-sales" ${cfg.notify_sales ? "checked" : ""}>
          ${icon("sales")}<span>Xarid cheki (savdoda mijoz tanlanganda)</span></label>
        <label class="perm-item" style="margin-top:6px"><input type="checkbox" id="cb-payments" ${cfg.notify_payments ? "checked" : ""}>
          ${icon("debt")}<span>Qarz to'lovi qabul qilinganda</span></label>
        <div class="actions">
          <button type="button" class="btn danger-text" id="cb-disconnect">Uzish</button>
          <button type="button" class="btn primary" id="cb-message">${icon("telegram")} Mijozlarga xabar yuborish</button>
        </div>
      </section>`;
  const view = layout(`
    <div class="toolbar"><a class="btn small" href="#/integrations">← Integratsiyalar</a><h2>Mijozlar boti</h2></div>
    ${body}`);
  const connect = $("#cb-connect", view);
  if (connect) connect.addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    const btn = $("button", connect);
    btn.disabled = true;
    try {
      const next = await api("POST", "/api/integrations/customer-bot/connect", { token: $("#cb-token", view).value.trim() });
      toast("Mijozlar boti ulandi ✅");
      viewCustomerBot(next);
    } finally { btn.disabled = false; }
  }));
  const save = (patch) => api("PUT", "/api/integrations/customer-bot", patch).then((next) => {
    toast("Saqlandi");
    viewCustomerBot(next);
  });
  const en = $("#cb-enabled", view);
  if (en) en.addEventListener("change", safe(() => save({ enabled: en.checked })));
  [["#cb-sales", "notify_sales"], ["#cb-payments", "notify_payments"]].forEach(([sel, key]) => {
    const el = $(sel, view);
    if (el) el.addEventListener("change", safe(() => { const p = {}; p[key] = el.checked; return save(p); }));
  });
  $$("[data-copy]", view).forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Nusxa olindi"); } catch { toast(b.dataset.copy); }
  }));
  const msg = $("#cb-message", view);
  if (msg) msg.addEventListener("click", () => messageModal());
  const disc = $("#cb-disconnect", view);
  if (disc) disc.addEventListener("click", safe(async () => {
    if (!confirm("Mijozlar boti uzilsinmi? Mijozlarga xabarlar boshqa bormaydi.")) return;
    viewCustomerBot(await api("DELETE", "/api/integrations/customer-bot"));
  }));
}

// ------------------------------------------------------------ router

const routes = [
  [/^#\/dashboard$/, viewDashboard, ["reports"]],
  [/^#\/crm\/customers$/, viewCustomers, ["crm"]],
  [/^#\/crm\/debts$/, viewDebts, ["crm"]],
  [/^#\/finance$/, viewFinanceCash, ["finance"]],
  [/^#\/finance\/entries$/, viewFinanceEntries, ["finance"]],
  [/^#\/finance\/types$/, viewFinanceTypes, ["finance"]],
  [/^#\/finance\/balances$/, viewBalances, ["finance"]],
  [/^#\/tables$/, viewTables, ["tables"]],
  [/^#\/order\/(\d+)$/, viewOrder, ["tables", "cashier", "reports"]],
  [/^#\/kitchen$/, viewKitchen, ["kitchen"]],
  [/^#\/printers$/, viewPrinters, ["printers"]],
  [/^#\/reports$/, viewReports, ["reports"]],
  [/^#\/reports\/sales$/, viewSales, ["reports"]],
  [/^#\/finance\/sales$/, viewSales, ["finance"]],
  [/^#\/menu$/, viewMenu, ["menu"]],
  [/^#\/tables-admin$/, viewTablesAdmin, ["halls"]],
  [/^#\/users$/, viewUsers, ["users"]],
  [/^#\/settings$/, viewSettings, ["settings"]],
  [/^#\/journal$/, viewJournal, ["journal"]],
  [/^#\/integrations$/, viewIntegrations, ["integrations"]],
  [/^#\/integrations\/telegram$/, viewTelegram, ["integrations"]],
  [/^#\/integrations\/customer-bot$/, viewCustomerBot, ["integrations"]],
  [/^#\/none$/, viewNoAccess],
];

async function router() {
  if (!state.user) return renderLogin();
  closeModal();
  if (state.leaveHook) {  // oldingi sahifa chiqishda tozalash qilishi kerak bo'lsa
    const hook = state.leaveHook;
    state.leaveHook = null;
    hook();
  }
  const hash = location.hash.split("?")[0];
  for (const [re, view, perms] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    if (perms && !can(...perms)) break;  // ruxsat yo'q - o'zining birinchi bo'limiga
    try {
      await view(...m.slice(1));
    } catch (e) {
      if (state.user) toast(e.message, true);
    }
    return;
  }
  go(defaultRoute());
}

window.addEventListener("hashchange", router);

// Yon panel holati: saqlangan bo'lsa shu, bo'lmasa kichik ekranda yig'ilgan
(function initSidebar() {
  let saved = null;
  try { saved = localStorage.getItem("side-collapsed"); } catch { /* ruxsat yo'q */ }
  const collapsed = saved === null ? window.innerWidth <= 1150 : saved === "1";
  document.body.classList.toggle("side-collapsed", collapsed);
})();

(async function start() {
  try {
    state.user = await api("GET", "/api/me");
    await loadSettings();
  } catch {
    state.user = null;
  }
  router();
})();

window.__cafeposLoaded = true;
