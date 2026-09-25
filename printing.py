"""Printerlar: ESC/POS oshxona cheki, printerga yuborish va printerlarni topish.

Faqat Python standart kutubxonasi:
- Windows: winspool.drv (ctypes) - kompyuterga USB / Wi-Fi orqali o'rnatilgan printerlar
- Linux/macOS: CUPS (lpstat / lp)
- Tarmoq termoprinterlari: to'g'ridan-to'g'ri TCP 9100 port
"""

import concurrent.futures
import ipaddress
import os
import socket
import subprocess
import tempfile
from datetime import datetime


class PrintError(Exception):
    pass


# ---------------------------------------------------------------- ESC/POS chek

ESC_INIT = b"\x1b@"
ESC_CP866 = b"\x1bt\x11"  # kirill harflari uchun kod sahifasi
ESC_CENTER, ESC_LEFT = b"\x1ba\x01", b"\x1ba\x00"
ESC_BOLD_ON, ESC_BOLD_OFF = b"\x1bE\x01", b"\x1bE\x00"
GS_BIG, GS_NORMAL = b"\x1d!\x11", b"\x1d!\x00"
GS_CUT = b"\n\n\n\n\x1dVB\x00"


def esc_text(text):
    for a, b in (("ʻ", "'"), ("ʼ", "'"), ("‘", "'"), ("’", "'"), ("—", "-"), ("№", "N")):
        text = text.replace(a, b)
    return text.encode("cp866", errors="replace")


def kitchen_ticket(printer, order, lines):
    width = 48 if printer["width"] >= 80 else 32
    place = "OLIB KETISH" if order["type"] == "takeaway" else order.get("table_name") or ""
    out = [ESC_INIT, ESC_CP866, ESC_CENTER, ESC_BOLD_ON, esc_text(printer["name"].upper()), b"\n", ESC_BOLD_OFF]
    out += [GS_BIG, esc_text(f"{place}  #{order['id']}"), b"\n", GS_NORMAL]
    if order.get("hall_name"):
        out += [ESC_BOLD_ON, esc_text(order["hall_name"]), b"\n", ESC_BOLD_OFF]
    out += [esc_text(f"Ofitsiant: {order.get('waiter_name') or '-'}"), b"\n"]
    out += [esc_text(datetime.now().strftime("%d.%m.%Y %H:%M")), b"\n", ESC_LEFT, b"-" * width, b"\n"]
    for line in lines:
        if line["qty"] > 0:
            text = f"{line['qty']} x {line['name']}"
        else:
            text = f"BEKOR: {-line['qty']} x {line['name']}"
        out += [GS_BIG, esc_text(text), b"\n", GS_NORMAL]
    out += [b"-" * width, b"\n", GS_CUT]
    return b"".join(out)


# ---------------------------------------------------------------- yuborish


def send(printer, payload):
    """printer: {"name", "kind", "address", "port"}. Xato bo'lsa PrintError."""
    kind = printer["kind"]
    if kind == "network":
        try:
            with socket.create_connection((printer["address"], printer["port"]), timeout=5) as s:
                s.sendall(payload)
        except OSError:
            raise PrintError(
                f"'{printer['name']}' printeriga ulanib bo'lmadi ({printer['address']}). "
                "Printer yoqilgan va tarmoqqa ulanganini tekshiring"
            )
    elif kind == "system":
        if os.name == "nt":
            _windows_raw_print(printer["address"], payload)
        else:
            _cups_raw_print(printer["address"], payload)
    elif kind == "windows":  # eski versiya: ulashilgan (shared) printer
        target = printer["address"]
        _windows_raw_print(target if target.startswith("\\\\") else "\\\\localhost\\" + target, payload)
    else:
        raise PrintError("Noma'lum printer turi")


_winspool = None


def _winspool_dll():
    """winspool.drv funksiyalarini bir marta sozlaydi."""
    global _winspool
    if _winspool:
        return _winspool
    import ctypes
    from ctypes import wintypes

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]

    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    H = wintypes.HANDLE
    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(H), ctypes.c_void_p]
    ws.OpenPrinterW.restype = wintypes.BOOL
    ws.StartDocPrinterW.argtypes = [H, wintypes.DWORD, ctypes.POINTER(DOC_INFO_1)]
    ws.StartDocPrinterW.restype = wintypes.DWORD
    ws.WritePrinter.argtypes = [H, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    ws.WritePrinter.restype = wintypes.BOOL
    for fn in (ws.StartPagePrinter, ws.EndPagePrinter, ws.EndDocPrinter, ws.ClosePrinter):
        fn.argtypes = [H]
        fn.restype = wintypes.BOOL
    ws.EnumPrintersW.argtypes = [
        wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ]
    ws.EnumPrintersW.restype = wintypes.BOOL
    ws.DOC_INFO_1 = DOC_INFO_1
    _winspool = ws
    return ws


def _windows_raw_print(name, payload):
    import ctypes
    from ctypes import wintypes

    ws = _winspool_dll()
    handle = wintypes.HANDLE()
    if not ws.OpenPrinterW(name, ctypes.byref(handle), None):
        raise PrintError(f"'{name}' printeri topilmadi. Printer yoqilgan va ulanganini tekshiring")
    try:
        doc = ws.DOC_INFO_1("CafePOS", None, "RAW")
        if not ws.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise PrintError(f"'{name}' printeri chekni qabul qilmadi (xato {ctypes.get_last_error()})")
        written = wintypes.DWORD(0)
        try:
            ws.StartPagePrinter(handle)
            ok = ws.WritePrinter(handle, payload, len(payload), ctypes.byref(written))
            ws.EndPagePrinter(handle)
        finally:
            ws.EndDocPrinter(handle)
        if not ok or written.value != len(payload):
            raise PrintError(f"'{name}' printeriga yozib bo'lmadi (xato {ctypes.get_last_error()})")
    finally:
        ws.ClosePrinter(handle)


def _cups_raw_print(name, payload):
    fd, path = tempfile.mkstemp(suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        result = subprocess.run(["lp", "-d", name, "-o", "raw", path], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PrintError(f"'{name}' printeriga yuborib bo'lmadi: {e}")
    finally:
        os.remove(path)
    if result.returncode != 0:
        raise PrintError(f"'{name}' printeriga chiqmadi: {result.stderr.decode(errors='replace').strip()}")


# ---------------------------------------------------------------- printerlarni topish

# PDF, Fax, OneNote kabi "virtual" printerlar ro'yxatda ko'rinmaydi
VIRTUAL_PORTS = ("PORTPROMPT:", "SHRFAX:", "NUL:", "FILE:", "XPSPORT:")
VIRTUAL_NAMES = ("pdf", "onenote", "fax", "xps", "anydesk")


def connection_type(port):
    p = (port or "").upper()
    if p.startswith("USB") or p.startswith("DOT4"):
        return "USB"
    if p.startswith(("WSD", "IP_", "TCP", "HTTP")) or p.count(".") == 3:
        return "Wi-Fi / tarmoq"
    if p.startswith(("COM", "LPT")):
        return p.rstrip(":")
    if p.startswith("BLUETOOTH") or p.startswith("BTH"):
        return "Bluetooth"
    return port or ""


def list_system_printers():
    """Kompyuterga o'rnatilgan printerlar: [{"name", "port", "connection"}]"""
    printers = _windows_printers() if os.name == "nt" else _cups_printers()
    return [
        p for p in printers
        if p["port"].upper() not in VIRTUAL_PORTS and not any(v in p["name"].lower() for v in VIRTUAL_NAMES)
    ]


def _windows_printers():
    import ctypes
    from ctypes import wintypes

    class PRINTER_INFO_2(ctypes.Structure):
        _fields_ = [
            ("pServerName", wintypes.LPWSTR), ("pPrinterName", wintypes.LPWSTR),
            ("pShareName", wintypes.LPWSTR), ("pPortName", wintypes.LPWSTR),
            ("pDriverName", wintypes.LPWSTR), ("pComment", wintypes.LPWSTR),
            ("pLocation", wintypes.LPWSTR), ("pDevMode", ctypes.c_void_p),
            ("pSepFile", wintypes.LPWSTR), ("pPrintProcessor", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR), ("pParameters", wintypes.LPWSTR),
            ("pSecurityDescriptor", ctypes.c_void_p), ("Attributes", wintypes.DWORD),
            ("Priority", wintypes.DWORD), ("DefaultPriority", wintypes.DWORD),
            ("StartTime", wintypes.DWORD), ("UntilTime", wintypes.DWORD),
            ("Status", wintypes.DWORD), ("cJobs", wintypes.DWORD), ("AveragePPM", wintypes.DWORD),
        ]

    ws = _winspool_dll()
    flags = 0x2 | 0x4  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed, count = wintypes.DWORD(0), wintypes.DWORD(0)
    ws.EnumPrintersW(flags, None, 2, None, 0, ctypes.byref(needed), ctypes.byref(count))
    if not needed.value:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not ws.EnumPrintersW(flags, None, 2, buf, needed.value, ctypes.byref(needed), ctypes.byref(count)):
        raise PrintError(f"Printerlar ro'yxatini olib bo'lmadi (xato {ctypes.get_last_error()})")
    infos = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_2))
    result = []
    for i in range(count.value):
        info = infos[i]
        port = info.pPortName or ""
        result.append({"name": info.pPrinterName, "port": port, "connection": connection_type(port)})
    return result


def _cups_printers():
    try:
        out = subprocess.run(["lpstat", "-v"], capture_output=True, timeout=5, text=True).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    result = []
    for line in out.splitlines():
        # "device for XP80: usb://Xprinter/XP-80"
        if line.startswith("device for ") and ":" in line[11:]:
            name, uri = line[11:].split(":", 1)
            uri = uri.strip()
            kind = "USB" if uri.startswith("usb") else "Wi-Fi / tarmoq" if "://" in uri else uri
            result.append({"name": name.strip(), "port": uri, "connection": kind})
    return result


def primary_ip():
    """Internetga (routerga) chiqadigan asosiy tarmoq kartasining IP manzili."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # paket yuborilmaydi, faqat mahalliy IP aniqlanadi
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith(("127.", "0.")) else ip
    except OSError:
        return None


def local_networks():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # paket yuborilmaydi, faqat mahalliy IP aniqlanadi
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    nets = []
    for ip in sorted(ips):
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets[:4], ips


def scan_network(port=9100, timeout=0.4, hosts=None):
    """Mahalliy tarmoqda 9100-port ochiq qurilmalarni (termoprinterlarni) qidiradi."""
    own = set()
    if hosts is None:
        nets, own = local_networks()
        hosts = [str(h) for net in nets for h in net.hosts()]

    def check(host):
        if host in own:
            return None
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return host
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        found = [h for h in pool.map(check, hosts) if h]
    return [{"address": h, "port": port} for h in found]
