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
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    icon = os.path.join(BASE_DIR, "static", "img", "cafepos.ico")
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
