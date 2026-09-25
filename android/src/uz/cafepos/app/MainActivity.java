package uz.cafepos.app;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * CafePOS Android ilovasi: kassadagi kompyuterdagi CafePOS serverini
 * brauzer panelisiz, to'liq ekranda ochadi.
 */
public class MainActivity extends Activity {

    private static final int BG = Color.rgb(31, 26, 23);
    private static final int ACCENT = Color.rgb(208, 138, 76);
    private static final int MUTED = Color.rgb(185, 174, 164);
    private static final int DEFAULT_PORT = 8000;
    private static final int FILE_REQUEST = 1;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private WebView web;
    private ScrollView setupView;
    private LinearLayout foundList;
    private TextView status;
    private EditText addressInput;
    private ValueCallback<Uri[]> fileCallback;
    private boolean scanning;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("cafepos", MODE_PRIVATE);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        web = createWebView();
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setupView = createSetupView();
        root.addView(setupView, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        String server = prefs.getString("server", "");
        if (server.isEmpty()) {
            showSetup(null);
            scan();
        } else {
            openServer(server);
        }
    }

    // ------------------------------------------------------------ WebView

    private WebView createWebView() {
        WebView w = new WebView(this);
        w.setBackgroundColor(BG);
        WebSettings s = w.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false); // oshxona ekranidagi ovozli signal
        s.setTextZoom(100);
        s.setAllowFileAccess(false);
        CookieManager.getInstance().setAcceptCookie(true);
        w.addJavascriptInterface(new Bridge(), "CafePOSApp");

        w.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return openExternalIfForeign(request.getUrl());
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openExternalIfForeign(Uri.parse(url));
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showSetup("Serverga ulanib bo'lmadi. Kompyuterda CafePOS ishlayotganini va telefon shu Wi-Fi'ga ulanganini tekshiring.");
                }
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (Build.VERSION.SDK_INT < 23) {
                    showSetup("Serverga ulanib bo'lmadi. Kompyuterda CafePOS ishlayotganini tekshiring.");
                }
            }
        });

        // Taom rasmini tanlash (Menyu bo'limi)
        w.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_REQUEST);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        return w;
    }

    private boolean openExternalIfForeign(Uri uri) {
        String server = prefs.getString("server", "");
        String host = uri.getHost() == null ? "" : uri.getHost();
        if (!server.isEmpty() && server.split(":")[0].equals(host)) return false;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception ignored) {
        }
        return true;
    }

    /** Sahifadagi JavaScript uchun: window.CafePOSApp.changeServer() */
    private class Bridge {
        @JavascriptInterface
        public void changeServer() {
            ui.post(() -> showSetup(null));
        }

        @JavascriptInterface
        public String server() {
            return prefs.getString("server", "");
        }
    }

    private void openServer(String server) {
        prefs.edit().putString("server", server).apply();
        setupView.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl("http://" + server + "/");
    }

    // ------------------------------------------------------------ ulanish oynasi

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private GradientDrawable rounded(int color, int strokeColor) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(12));
        if (strokeColor != 0) d.setStroke(dp(1), strokeColor);
        return d;
    }

    private Button button(String text, boolean primary) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(16);
        b.setTextColor(primary ? Color.WHITE : ACCENT);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setBackground(primary ? rounded(ACCENT, 0) : rounded(Color.TRANSPARENT, ACCENT));
        b.setPadding(dp(12), dp(12), dp(12), dp(12));
        return b;
    }

    private LinearLayout.LayoutParams wide(int topMargin) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(topMargin);
        return lp;
    }

    private ScrollView createSetupView() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BG);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        box.setPadding(dp(24), dp(40), dp(24), dp(32));
        scroll.addView(box, new ViewGroup.LayoutParams(-1, -2));

        int logoId = getResources().getIdentifier("logo", "drawable", getPackageName());
        if (logoId != 0) {
            ImageView logo = new ImageView(this);
            logo.setImageResource(logoId);
            logo.setAdjustViewBounds(true);
            box.addView(logo, new LinearLayout.LayoutParams(dp(170), -2));
        }

        TextView title = new TextView(this);
        title.setText("Serverga ulanish");
        title.setTextColor(Color.WHITE);
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        box.addView(title, wide(24));

        TextView hint = new TextView(this);
        hint.setText("Telefon kassadagi kompyuter bilan bitta Wi-Fi'da bo'lsin. "
                + "Manzil kompyuterdagi CafePOS'ning Sozlamalar bo'limida yozilgan.");
        hint.setTextColor(MUTED);
        hint.setTextSize(14);
        hint.setGravity(Gravity.CENTER);
        box.addView(hint, wide(8));

        status = new TextView(this);
        status.setTextColor(Color.rgb(255, 138, 122));
        status.setTextSize(14);
        status.setGravity(Gravity.CENTER);
        status.setVisibility(View.GONE);
        box.addView(status, wide(12));

        addressInput = new EditText(this);
        addressInput.setHint("192.168.1.10:8000");
        addressInput.setHintTextColor(Color.rgb(120, 110, 102));
        addressInput.setTextColor(Color.WHITE);
        addressInput.setTextSize(18);
        addressInput.setSingleLine(true);
        addressInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addressInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        addressInput.setBackground(rounded(Color.rgb(44, 37, 33), Color.rgb(70, 60, 54)));
        addressInput.setPadding(dp(14), dp(12), dp(14), dp(12));
        addressInput.setText(prefs.getString("server", ""));
        addressInput.setOnEditorActionListener((v, actionId, event) -> {
            connectTyped();
            return true;
        });
        box.addView(addressInput, wide(20));

        Button connect = button("Ulanish", true);
        connect.setOnClickListener(v -> connectTyped());
        box.addView(connect, wide(12));

        Button search = button("🔍 Wi-Fi'dan qidirish", false);
        search.setOnClickListener(v -> scan());
        box.addView(search, wide(10));

        foundList = new LinearLayout(this);
        foundList.setOrientation(LinearLayout.VERTICAL);
        box.addView(foundList, wide(12));
        return scroll;
    }

    private void showSetup(String message) {
        addressInput.setText(prefs.getString("server", ""));
        status.setText(message == null ? "" : message);
        status.setTextColor(Color.rgb(255, 138, 122));
        status.setVisibility(message == null ? View.GONE : View.VISIBLE);
        setupView.setVisibility(View.VISIBLE);
    }

    private static String normalize(String input) {
        String s = input.trim().toLowerCase();
        s = s.replaceFirst("^https?://", "");
        int slash = s.indexOf('/');
        if (slash >= 0) s = s.substring(0, slash);
        if (s.isEmpty()) return s;
        if (!s.contains(":")) s += ":" + DEFAULT_PORT;
        return s;
    }

    private void connectTyped() {
        String server = normalize(addressInput.getText().toString());
        if (server.isEmpty()) {
            showSetup("Manzilni kiriting yoki \"Wi-Fi'dan qidirish\" ni bosing");
            return;
        }
        openServer(server);
    }

    // ------------------------------------------------------------ Wi-Fi'dan qidirish

    private void scan() {
        if (scanning) return;
        scanning = true;
        foundList.removeAllViews();
        status.setTextColor(MUTED);
        status.setText("CafePOS serveri qidirilmoqda...");
        status.setVisibility(View.VISIBLE);
        new Thread(() -> {
            List<String> found = findServers();
            ui.post(() -> {
                scanning = false;
                showFound(found);
            });
        }).start();
    }

    private void showFound(List<String> found) {
        foundList.removeAllViews();
        if (found.isEmpty()) {
            status.setTextColor(Color.rgb(255, 138, 122));
            status.setText("Server topilmadi. Kompyuterda CafePOS ochiq ekanini va telefon shu Wi-Fi'da ekanini "
                    + "tekshiring yoki manzilni qo'lda yozing.");
            return;
        }
        if (found.size() == 1 && prefs.getString("server", "").isEmpty()) {
            openServer(found.get(0));  // birinchi ochilishda bitta server topilsa - darhol ulanamiz
            return;
        }
        status.setTextColor(MUTED);
        status.setText("Topildi — tanlang:");
        for (String server : found) {
            Button b = button("☕ " + server, false);
            b.setOnClickListener(v -> openServer(server));
            foundList.addView(b, wide(8));
        }
    }

    private List<String> findServers() {
        List<String> hosts = new ArrayList<>();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress addr : Collections.list(ni.getInetAddresses())) {
                    if (!(addr instanceof Inet4Address) || !addr.isSiteLocalAddress()) continue;
                    String ip = addr.getHostAddress();
                    String prefix = ip.substring(0, ip.lastIndexOf('.') + 1);
                    for (int i = 1; i < 255; i++) {
                        String host = prefix + i;
                        if (!host.equals(ip) && !hosts.contains(host)) hosts.add(host);
                    }
                }
            }
        } catch (Exception ignored) {
        }
        List<String> found = Collections.synchronizedList(new ArrayList<>());
        ExecutorService pool = Executors.newFixedThreadPool(48);
        for (String host : hosts) {
            pool.execute(() -> {
                String server = host + ":" + DEFAULT_PORT;
                if (isCafePOS(server)) found.add(server);
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(20, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
        Collections.sort(found);
        return new ArrayList<>(found);
    }

    private static boolean isCafePOS(String server) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + server + "/manifest.webmanifest").openConnection();
            c.setConnectTimeout(600);
            c.setReadTimeout(1500);
            if (c.getResponseCode() != 200) return false;
            byte[] buf = new byte[2048];
            InputStream in = c.getInputStream();
            int n = in.read(buf);
            return n > 0 && new String(buf, 0, n, "UTF-8").contains("CafePOS");
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    // ------------------------------------------------------------ tizim

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (setupView.getVisibility() == View.VISIBLE && !prefs.getString("server", "").isEmpty()
                    && web.getUrl() != null) {
                setupView.setVisibility(View.GONE);
                return true;
            }
            if (setupView.getVisibility() != View.VISIBLE && web.canGoBack()) {
                web.goBack();
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush(); // tizimga kirish saqlanib qolsin
    }
}
