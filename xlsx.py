"""Excel (.xlsx) va CSV fayllar bilan ishlash - faqat Python standart kutubxonasi.

write_xlsx() - import shabloni uchun oddiy jadval yaratadi
read_table() - yuklangan .xlsx yoki .csv faylni qatorlar ro'yxatiga aylantiradi
"""

import csv
import io
import re
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class TableError(Exception):
    pass


def _col_letter(index):
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def write_xlsx(headers, rows, widths=None, sheet="Shablon", notes=None):
    """headers - sarlavhalar, rows - namuna qatorlar, notes - pastdagi izoh qatorlari."""
    def cell(ref, value, style=0):
        s = f' s="{style}"' if style else ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"{s}><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'

    lines = []
    all_rows = [(headers, 1)] + [(r, 0) for r in rows]
    for r_i, (values, style) in enumerate(all_rows, start=1):
        cells = "".join(cell(f"{_col_letter(c_i)}{r_i}", v, style) for c_i, v in enumerate(values) if v != "")
        lines.append(f'<row r="{r_i}">{cells}</row>')
    for n_i, note in enumerate(notes or [], start=len(all_rows) + 2):
        lines.append(f'<row r="{n_i}">{cell(f"A{n_i}", note, 2)}</row>')
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths or [])
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        + (f"<cols>{cols}</cols>" if cols else "")
        + f'<sheetData>{"".join(lines)}</sheetData></worksheet>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><i/><sz val="10"/><color rgb="FF8A7F76"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF8B5A2B"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": sheet_xml,
        "xl/styles.xml": styles,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_xlsx(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TableError("Fayl Excel (.xlsx) emas yoki buzilgan")
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    # birinchi varaq
    sheet_path = "xl/worksheets/sheet1.xml"
    try:
        wb = ElementTree.fromstring(z.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        first = wb.find("m:sheets/m:sheet", NS)
        target = next(
            r.get("Target") for r in rels if r.get("Id") == first.get(REL_NS)
        )
        target = target.lstrip("/")
        sheet_path = target if target.startswith("xl/") else "xl/" + target
    except (KeyError, StopIteration, AttributeError):
        pass
    if sheet_path not in names:
        raise TableError("Excel faylda varaq topilmadi")
    root = ElementTree.fromstring(z.read(sheet_path))
    table = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        values = {}
        for c in row.findall("m:c", NS):
            ref = c.get("r", "")
            letters = re.match(r"[A-Z]+", ref)
            col = 0
            if letters:
                for ch in letters.group(0):
                    col = col * 26 + (ord(ch) - 64)
                col -= 1
            else:
                col = len(values)
            kind = c.get("t")
            v = c.find("m:v", NS)
            if kind == "s" and v is not None:
                text = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            elif v is not None and v.text is not None:
                text = v.text
                if kind != "str":
                    try:
                        text = _cell_text(float(text))
                    except ValueError:
                        pass
            else:
                text = ""
            values[col] = text.strip()
        if values:
            row_list = [""] * (max(values) + 1)
            for k, v in values.items():
                row_list[k] = v
            table.append(row_list)
    return table


def _read_csv(data):
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise TableError("CSV fayl kodirovkasini o'qib bo'lmadi")
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except csv.Error:
        pass
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]


def read_table(data, filename=""):
    """Birinchi qator - sarlavha. Natija: (sarlavhalar, [(qator_raqami, qiymatlar), ...])"""
    if filename.lower().endswith(".csv") or not data.startswith(b"PK"):
        table = _read_csv(data)
    else:
        table = _read_xlsx(data)
    # bo'sh qatorlar va "#" bilan boshlanadigan izohlar o'tkazib yuboriladi
    table = [r for r in table if any(c.strip() for c in r) and not (r and r[0].strip().startswith("#"))]
    if not table:
        raise TableError("Fayl bo'sh")
    headers = [h.strip().lower().rstrip("*").strip() for h in table[0]]
    return headers, [(i + 2, row + [""] * (len(headers) - len(row))) for i, row in enumerate(table[1:])]
