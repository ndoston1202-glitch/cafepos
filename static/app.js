// CafePOS - interfeys (kutubxonasiz, oddiy JavaScript)
"use strict";

const state = { user: null, categories: [], products: [] };

const ROLE_NAMES = { admin: "Administrator", cashier: "Kassir", waiter: "Ofitsiant", cook: "Oshpaz" };
const PRINTER_KINDS = {
  system: "Kompyuterga ulangan (USB / Wi-Fi)",
  network: "Tarmoq termoprinteri (IP manzil)",
  windows: "Ulashilgan printer (eski)",
};
const METHOD_NAMES = { cash: "Naqd", card: "Karta", payme: "Payme", click: "Click" };

// ------------------------------------------------------------ yordamchilar

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
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

function can(...roles) {
  return state.user && roles.includes(state.user.role);
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
  return Object.fromEntries(new FormData(form).entries());
}

// ------------------------------------------------------------ login

function renderLogin() {
  $("#app").innerHTML = `
    <div class="login-wrap">
      <form class="login-box" id="login-form">
        <div class="logo">☕</div>
        <h1>CafePOS</h1>
        <label><span>Login</span><input name="username" autocomplete="username" required></label>
        <label><span>Parol</span><input name="password" type="password" autocomplete="current-password" required></label>
        <div class="error" id="login-error"></div>
        <button class="btn primary big">Kirish</button>
      </form>
    </div>`;
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      state.user = await api("POST", "/api/login", formData(e.target));
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

function navItems() {
  if (can("cook")) return [["#/kitchen", "🍳 Oshxona"]];
  const items = [["#/tables", "🪑 Stollar"]];
  if (can("admin", "cashier")) items.push(["#/cashier", "💰 Kassa"], ["#/kitchen", "🍳 Oshxona"], ["#/reports", "📊 Hisobot"]);
  if (can("admin")) items.push(
    ["#/menu", "🍽️ Menyu"], ["#/tables-admin", "⚙️ Zallar va stollar"], ["#/printers", "🖨️ Printerlar"], ["#/users", "👥 Xodimlar"]);
  return items;
}

function defaultRoute() {
  return can("cook") ? "#/kitchen" : "#/tables";
}

function layout(content) {
  const hash = location.hash.split("?")[0];
  $("#app").innerHTML = `
    <header class="topbar">
      <div class="brand">☕ CafePOS</div>
      <nav>${navItems().map(([href, name]) =>
        `<a href="${href}" class="${hash === href || (href === "#/tables" && hash.startsWith("#/order/")) ? "active" : ""}">${name}</a>`).join("")}</nav>
      <div class="user">
        <span>${esc(state.user.full_name)} <span class="muted">· ${ROLE_NAMES[state.user.role]}</span></span>
        <button class="btn small" id="logout-btn">Chiqish</button>
      </div>
    </header>
    <main id="view">${content}</main>`;
  $("#logout-btn").addEventListener("click", logout);
  return $("#view");
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
        ${o ? `<div class="sum">${money(Math.max(o.subtotal - o.discount, 0))}</div>` : ""}
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

  $("#back-btn").addEventListener("click", () => history.length > 1 ? history.back() : (location.hash = "#/tables"));

  function renderProducts() {
    const cats = [{ id: "all", name: "Hammasi" }, ...state.categories];
    $("#cat-tabs").innerHTML = cats.map((c) =>
      `<button class="btn small ${String(c.id) === String(activeCat) ? "active" : ""}" data-cat="${c.id}">${esc(c.name)}</button>`).join("");
    $$("#cat-tabs [data-cat]").forEach((b) => b.addEventListener("click", () => {
      activeCat = b.dataset.cat;
      renderProducts();
    }));

    const list = state.products.filter((p) => activeCat === "all" || String(p.category_id) === String(activeCat));
    const closed = order.status !== "open";
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
    const statusText = { paid: "✅ To'langan", cancelled: "❌ Bekor qilingan" }[order.status];
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
        ${order.discount ? `<div><span>Summa</span><span>${money(order.subtotal)}</span></div>
          <div><span>Chegirma</span><span>−${money(order.discount)}</span></div>` : ""}
        <div class="grand"><span>Jami</span><span>${money(order.total)}</span></div>
        ${order.payment_method ? `<div class="muted"><span>To'lov</span><span>${METHOD_NAMES[order.payment_method]}</span></div>` : ""}
      </div>
      <div class="cart-actions">
        ${open && can("admin", "cashier") ? `<button class="btn primary big" id="pay-btn" ${order.items.length ? "" : "disabled"}>💰 To'lash</button>` : ""}
        <div class="row">
          <button class="btn" id="print-btn" ${order.items.length ? "" : "disabled"}>🖨️ Chek</button>
          ${open && can("admin", "cashier") ? `<button class="btn danger" id="cancel-btn">Bekor qilish</button>` : ""}
        </div>
        ${open ? `
        <div class="kitchen-actions">
          <button class="btn kitchen" id="kitchen-print-btn" ${order.pending_print ? "" : "disabled"}>
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
        toast("Chiqarildi: " + order.printed.join(", "));
        if (order.errors.length) setTimeout(() => toast(order.errors.join("; "), true), 2600);
      } finally {
        renderCart();
      }
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
  openModal(`
    <h2>To'lov · #${order.id}</h2>
    <div class="pay-methods">
      ${Object.entries(METHOD_NAMES).map(([k, v]) =>
        `<button type="button" class="btn ${k === method ? "active" : ""}" data-method="${k}">${v}</button>`).join("")}
    </div>
    <label><span>Chegirma (so'm)</span><input id="discount" type="number" min="0" value="0"></label>
    <div class="totals"><div class="grand"><span>To'lanadi</span><span id="to-pay"></span></div></div>
    <label id="cash-box"><span>Mijoz bergan pul</span><input id="given" type="number" min="0" placeholder="Qaytim hisoblash uchun"></label>
    <div class="change" id="change"></div>
    <label><input type="checkbox" id="print-after" checked style="width:auto"> To'lovdan so'ng chek chiqarish</label>
    <div class="actions">
      <button class="btn" data-close>Bekor</button>
      <button class="btn primary" id="confirm-pay">Tasdiqlash</button>
    </div>`, (modal) => {
    const total = () => Math.max(order.subtotal - (+$("#discount", modal).value || 0), 0);
    const update = () => {
      $("#to-pay", modal).textContent = money(total());
      $("#cash-box", modal).classList.toggle("hidden", method !== "cash");
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
    $("#confirm-pay", modal).addEventListener("click", safe(async () => {
      const paid = await api("POST", `/api/orders/${order.id}/pay`, {
        method, discount: +$("#discount", modal).value || 0,
      });
      const shouldPrint = $("#print-after", modal).checked;
      closeModal();
      toast("To'lov qabul qilindi ✅");
      onPaid(paid);
      if (shouldPrint) printReceipt(paid);
    }));
    update();
  });
}

function printReceipt(order) {
  $("#print-area").innerHTML = `
    <h3>☕ CafePOS</h3>
    <div class="c">Buyurtma #${order.id} · ${esc(place(order))}</div>
    <div class="c">${esc(order.closed_at || order.created_at)}</div>
    <div class="c">Ofitsiant: ${esc(order.waiter_name || "-")}</div>
    <hr>
    <table>
      ${order.items.map((i) => `
        <tr><td colspan="2">${esc(i.name)}</td></tr>
        <tr><td>${i.qty} x ${money(i.price)}</td><td style="text-align:right">${money(i.qty * i.price)}</td></tr>`).join("")}
    </table>
    <hr>
    <table>
      ${order.discount ? `<tr><td>Summa</td><td style="text-align:right">${money(order.subtotal)}</td></tr>
        <tr><td>Chegirma</td><td style="text-align:right">-${money(order.discount)}</td></tr>` : ""}
      <tr><td><b>JAMI</b></td><td style="text-align:right"><b>${money(order.total)}</b></td></tr>
      ${order.payment_method ? `<tr><td>To'lov</td><td style="text-align:right">${METHOD_NAMES[order.payment_method]}</td></tr>` : ""}
    </table>
    <hr>
    <div class="c">${order.status === "paid" ? "Xaridingiz uchun rahmat!" : "Hisob (to'lanmagan)"}</div>`;
  window.print();
}

// ------------------------------------------------------------ kassa

async function viewCashier() {
  const orders = await api("GET", "/api/orders?status=open");
  const view = layout(`
    <div class="toolbar"><h2>Kassa · ochiq buyurtmalar</h2></div>
    <div class="panel">
      <table class="list">
        <thead><tr><th>#</th><th>Joy</th><th>Ofitsiant</th><th>Vaqt</th><th class="right">Summa</th></tr></thead>
        <tbody>
          ${orders.map((o) => `
            <tr class="clickable" data-id="${o.id}">
              <td>${o.id}</td>
              <td>${esc(place(o))}</td>
              <td>${esc(o.waiter_name || "-")}</td>
              <td>${time(o.created_at)}</td>
              <td class="right"><b>${money(Math.max(o.subtotal - o.discount, 0))}</b></td>
            </tr>`).join("") || `<tr><td colspan="5" class="muted">Ochiq buyurtmalar yo'q</td></tr>`}
        </tbody>
      </table>
    </div>`);
  $$("tr[data-id]", view).forEach((tr) =>
    tr.addEventListener("click", () => (location.hash = "#/order/" + tr.dataset.id)));
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
        <div class="toolbar"><h2>Taomlar</h2><button class="btn primary small" id="add-prod">+ Taom qo'shish</button></div>
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
      <label><span>Tartib raqami</span><input name="sort" type="number" value="${c.sort ?? state.categories.length}"></label>
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
        <label><span>Sotish narxi (so'm)</span><input name="price" type="number" min="0" value="${p.price ?? ""}" required></label>
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
            <tr><td><b>${esc(h.name)}</b><div class="muted">${h.tables} ta stol</div></td>
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
      <label><span>Tartib raqami</span><input name="sort" type="number" value="${h.sort ?? halls.length}"></label>
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    await api(h.id ? "PUT" : "POST", "/api/halls" + (h.id ? "/" + h.id : ""), formData(e.target));
    closeModal();
    toast("Saqlandi");
    viewTablesAdmin();
  })));

  const form = (t = {}) => {
    const hallId = t.hall_id ?? (halls[0] || {}).id;
    const inHall = tables.filter((x) => x.hall_id === hallId).length;
    openModal(`
      <form id="f"><h2>${t.id ? "Stolni tahrirlash" : "Yangi stol"}</h2>
        <label><span>Zal</span>
          <select name="hall_id">
            <option value="">— Zalsiz —</option>
            ${halls.map((h) => `<option value="${h.id}" ${h.id === hallId ? "selected" : ""}>${esc(h.name)}</option>`).join("")}
          </select></label>
        <label><span>Nomi (masalan: Stol 5, Kabina 2)</span><input name="name" value="${esc(t.name || `Stol ${inHall + 1}`)}" required></label>
        <label><span>O'rinlar soni</span><input name="seats" type="number" min="1" value="${t.seats ?? 4}"></label>
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

async function viewUsers() {
  const users = await api("GET", "/api/users");
  layout(`
    <div class="panel" style="max-width:800px">
      <div class="toolbar"><h2>Xodimlar</h2><button class="btn primary small" id="add">+ Xodim qo'shish</button></div>
      <table class="list">
        <thead><tr><th>F.I.Sh.</th><th>Login</th><th>Rol</th><th>Holati</th><th></th></tr></thead>
        <tbody>
          ${users.map((u) => `
            <tr><td>${esc(u.full_name)}</td><td>${esc(u.username)}</td><td>${ROLE_NAMES[u.role]}</td>
              <td>${u.active ? `<span class="badge">Faol</span>` : `<span class="badge off">O'chirilgan</span>`}</td>
              <td class="right"><button class="btn small" data-edit="${u.id}">✏️</button></td></tr>`).join("")}
        </tbody>
      </table>
    </div>`);

  const form = (u = {}) => openModal(`
    <form id="f"><h2>${u.id ? "Xodimni tahrirlash" : "Yangi xodim"}</h2>
      <label><span>F.I.Sh.</span><input name="full_name" value="${esc(u.full_name || "")}" required></label>
      ${u.id ? "" : `<label><span>Login</span><input name="username" required></label>`}
      <label><span>Rol</span>
        <select name="role">${Object.entries(ROLE_NAMES).map(([k, v]) =>
          `<option value="${k}" ${k === (u.role || "waiter") ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label><span>${u.id ? "Yangi parol (o'zgartirmaslik uchun bo'sh qoldiring)" : "Parol"}</span>
        <input name="password" type="password" minlength="4" ${u.id ? "" : "required"}></label>
      ${u.id ? `<label><input type="checkbox" name="active" ${u.active ? "checked" : ""} style="width:auto"> Faol</label>` : ""}
      <div class="actions"><button type="button" class="btn" data-close>Bekor</button><button class="btn primary">Saqlash</button></div>
    </form>`, (m) => $("#f", m).addEventListener("submit", safe(async (e) => {
    e.preventDefault();
    const data = formData(e.target);
    if (u.id) data.active = e.target.active.checked;
    await api(u.id ? "PUT" : "POST", "/api/users" + (u.id ? "/" + u.id : ""), data);
    closeModal();
    toast("Saqlandi");
    viewUsers();
  })));

  $("#add").addEventListener("click", () => form());
  $$("[data-edit]").forEach((b) => b.addEventListener("click", () =>
    form(users.find((u) => u.id === +b.dataset.edit))));
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

// ------------------------------------------------------------ router

const routes = [
  [/^#\/tables$/, viewTables, ["admin", "cashier", "waiter"]],
  [/^#\/order\/(\d+)$/, viewOrder, ["admin", "cashier", "waiter"]],
  [/^#\/kitchen$/, viewKitchen, ["admin", "cashier", "cook"]],
  [/^#\/printers$/, viewPrinters, ["admin"]],
  [/^#\/cashier$/, viewCashier, ["admin", "cashier"]],
  [/^#\/reports$/, viewReports, ["admin", "cashier"]],
  [/^#\/menu$/, viewMenu, ["admin"]],
  [/^#\/tables-admin$/, viewTablesAdmin, ["admin"]],
  [/^#\/users$/, viewUsers, ["admin"]],
];

async function router() {
  if (!state.user) return renderLogin();
  closeModal();
  const hash = location.hash.split("?")[0];
  for (const [re, view, roles] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    if (roles && !can(...roles)) break;
    try {
      await view(...m.slice(1));
    } catch (e) {
      if (state.user) toast(e.message, true);
    }
    return;
  }
  location.hash = defaultRoute();
}

window.addEventListener("hashchange", router);

(async function start() {
  try {
    state.user = await api("GET", "/api/me");
  } catch {
    state.user = null;
  }
  router();
})();
