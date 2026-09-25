"""CafePOS - kompyuterda alohida oyna (desktop) bo'lib ochish.

1. Server ishlamayotgan bo'lsa, uni qora oynasiz orqa fonda ishga tushiradi
2. Chrome yoki Edge'ni "ilova rejimida" ochadi: manzil qatori va tablarsiz, o'z oynasi va ikonkasi bilan

Oynani yopish serverni to'xtatmaydi - telefon va planshetlar ishlashda davom etadi.
Serverni to'xtatish: TOXTATISH.bat
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("CAFEPOS_PORT", "8000"))
URL = f"http://localhost:{PORT}/"
LOG_PATH = os.path.join(BASE_DIR, "cafepos.log")


# Kompyuterdagi proksi sozlamalari localhost'ga xalaqit bermasin
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def server_running():
    try:
        with _opener.open(URL + "api/me", timeout=1.5):
            return True
    except urllib.error.HTTPError as e:
        return e.code == 401  # server javob berdi, faqat hali kirilmagan
    except OSError:
        return False


def start_server():
    # Server chiqishlari log faylga yoziladi, qora oyna ochilmaydi
    log = open(LOG_PATH, "a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "server.py"), "--no-browser"],
        cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=flags, close_fds=True,
    )
    for _ in range(60):
        if server_running():
            return True
        time.sleep(0.25)
    return False


def find_browser():
    """Ilova rejimini qo'llaydigan brauzer: Chrome, keyin Edge (Windows'da doim bor)."""
    candidates = []
    if os.name == "nt":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if root:
                candidates.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if root:
                candidates.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            path = shutil.which(name)
            if path:
                candidates.append(path)
    return next((c for c in candidates if os.path.isfile(c)), None)


def profile_dir():
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    path = os.path.join(root, "CafePOS", "window")
    os.makedirs(path, exist_ok=True)
    return path


def open_window():
    browser = find_browser()
    if not browser:
        webbrowser.open(URL)
        return
    subprocess.Popen([
        browser,
        f"--app={URL}",
        # Alohida profil: oddiy Chrome oynalariga aralashmaydi, o'z ikonkasi bilan ochiladi
        f"--user-data-dir={profile_dir()}",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ])
    brand_window()


APP_ID = "CafePOS.Desktop"
ICON_PATH = os.path.join(BASE_DIR, "static", "img", "cafepos.ico")


def pythonw_path():
    path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return path if os.path.isfile(path) else sys.executable


def brand_window(timeout=25):
    """Windows vazifalar panelida (taskbar) Chrome ikonkasi o'rniga CafePOS ikonkasi.

    Oynaga o'z AppUserModelID'sini beramiz - Windows uni Chrome'dan alohida guruhlaydi
    va ikonka, "pin" qilinganda ishga tushirish buyrug'ini bizniki qiladi.
    """
    if os.name != "nt":
        return
    try:
        _brand_window(timeout)
    except Exception:  # ikonka qo'yilmasa ham dastur ishlayveradi
        pass


def _brand_window(timeout):
    import ctypes
    from ctypes import wintypes

    user32, shell32, ole32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.ole32

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]

    class PROPVARIANT(ctypes.Structure):  # faqat VT_LPWSTR uchun
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    def guid(text):
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    fmtid = guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}")  # System.AppUserModel.*
    iid_store = guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")  # IPropertyStore
    props = [  # (pid, qiymat) - relaunch xususiyatlari ID'dan oldin qo'yiladi
        (2, f'"{pythonw_path()}" "{os.path.join(BASE_DIR, "desktop.py")}"'),  # RelaunchCommand
        (4, "CafePOS"),  # RelaunchDisplayNameResource
        (3, f"{ICON_PATH},0"),  # RelaunchIconResource
        (5, APP_ID),  # ID
    ]

    shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    big = user32.LoadImageW(None, ICON_PATH, 1, 48, 48, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
    small = user32.LoadImageW(None, ICON_PATH, 1, 16, 16, 0x10)

    def brand(hwnd):
        store = ctypes.c_void_p()
        if shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid_store), ctypes.byref(store)) != 0:
            return
        vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                       ctypes.POINTER(PROPVARIANT))(vtbl[6])
        commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
        try:
            for pid, value in props:
                key = PROPERTYKEY(fmtid, pid)
                pv = PROPVARIANT(31, 0, 0, 0, value, None)  # VT_LPWSTR
                set_value(store, ctypes.byref(key), ctypes.byref(pv))
            commit(store)
        finally:
            release(store)
        if big:
            user32.SendMessageW(hwnd, 0x0080, 1, big)  # WM_SETICON, ICON_BIG
        if small:
            user32.SendMessageW(hwnd, 0x0080, 0, small)  # ICON_SMALL

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    done, found_at = set(), None
    deadline = time.time() + timeout
    while time.time() < deadline:
        windows = []

        def collect(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                user32.GetClassNameW(hwnd, cls, 256)
                # Ilova rejimidagi oyna sarlavhasi faqat sahifa nomi: "CafePOS"
                if title.value == "CafePOS" and cls.value.startswith("Chrome_WidgetWin"):
                    windows.append(hwnd)
            return True

        user32.EnumWindows(enum_proc(collect), 0)
        for hwnd in windows:
            if hwnd not in done:
                brand(hwnd)
                done.add(hwnd)
                found_at = found_at or time.time()
        # Chrome ikonkani yangilab yuborsa, bir necha soniya qayta qo'yib turamiz
        if found_at and time.time() - found_at > 4:
            for hwnd in done:
                brand(hwnd)
            return
        time.sleep(0.3)


def show_error(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "CafePOS", 0x10)
    else:
        print(message, file=sys.stderr)


def install_shortcuts():
    """Ish stoli va Pusk menyusida "CafePOS" yorlig'ini yaratadi (Windows)."""
    if os.name != "nt":
        print("Yorliq faqat Windows'da yaratiladi")
        return
    pythonw = pythonw_path()
    icon = ICON_PATH
    script = os.path.join(BASE_DIR, "desktop.py")

    def ps(value):  # PowerShell satri uchun
        return "'" + value.replace("'", "''") + "'"

    places = ["[Environment]::GetFolderPath('Desktop')", "[Environment]::GetFolderPath('Programs')"]
    commands = ["$sh = New-Object -ComObject WScript.Shell"]
    for place in places:
        commands += [
            f"$lnk = $sh.CreateShortcut((Join-Path {place} 'CafePOS.lnk'))",
            f"$lnk.TargetPath = {ps(pythonw)}",
            f"$lnk.Arguments = {ps(chr(34) + script + chr(34))}",
            f"$lnk.WorkingDirectory = {ps(BASE_DIR)}",
            f"$lnk.IconLocation = {ps(icon)}",
            "$lnk.Description = 'CafePOS - kafe uchun ERP dasturi'",
            "$lnk.Save()",
        ]
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "; ".join(commands)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Yorliq yaratib bo'lmadi:", result.stderr.strip())
        sys.exit(1)
    print("Tayyor! Ish stolida va Pusk menyusida 'CafePOS' yorlig'i paydo bo'ldi.")


def main():
    if "--install" in sys.argv:
        return install_shortcuts()
    if not server_running() and not start_server():
        tail = ""
        try:
            with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
                tail = "".join(f.readlines()[-12:])
        except OSError:
            pass
        show_error(f"CafePOS serverini ishga tushirib bo'lmadi.\n\n{tail}\nBatafsil: {LOG_PATH}")
        sys.exit(1)
    open_window()


if __name__ == "__main__":
    main()
