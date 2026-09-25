// CafePOS service worker: ilova sifatida o'rnatish uchun.
// Ma'lumotlar keshlanmaydi - hamma narsa doim serverdan olinadi (narx va buyurtmalar eskirmasin).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(() => new Response(
      `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>CafePOS</title>
      <body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#1f1a17;color:#fff;font-family:system-ui;text-align:center;padding:24px">
      <div><h2>Serverga ulanib bo'lmadi</h2>
      <p style="color:#b9aea4">Kassadagi kompyuterda CafePOS ishlayotganini va telefon<br>shu Wi-Fi'ga ulanganini tekshiring.</p>
      <button onclick="location.reload()" style="margin-top:12px;padding:12px 24px;border:0;border-radius:10px;background:#d08a4c;color:#fff;font-size:16px">Qayta urinish</button></div>`,
      { headers: { "Content-Type": "text/html; charset=utf-8" } },
    )),
  );
});
