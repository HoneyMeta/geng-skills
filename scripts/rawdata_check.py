# -*- coding: utf-8 -*-
"""Raw data files behind the paper (.csv / .tsv / .xlsx), zero dependencies.

Raw data hold far more numbers than a paper's tables, so the classic fabrication tests get real
power here, but they also contain things tables do not: ID columns, Likert answers, set-points.
The checks are therefore narrower than for tables:

- terminal digits per column, only when the column's range spans at least 50 steps of its last
  digit (ages 18-65 in whole years would fail uniformity for natural reasons);
- Benford across the sheet (only if the values span two orders of magnitude);
- identical rows, only for rows with at least 4 values of which at least one is non-integer
  (two respondents giving the same Likert answers is normal);
- identical columns (at least 10 values, not constant);
- repeated runs of values, only in continuous columns (at least 3 significant digits).
ID-like columns (named id / no / 编号 …, or unique increasing integers) are left out.
"""

import csv
import io
import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

RAW_SUFFIXES = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")
ID_HEADER_RE = re.compile(r"^\s*(?:id|no\.?|num|index|idx|#|subject|subj|participant|case|sample\s*id|编号|序号|样本号|被试编号|被试|学号)\s*$", re.I)
SS_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------


def _decode(data):
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_delimited(path):
    with open(path, "rb") as f:
        text = _decode(f.read())
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if path.lower().endswith(".tsv") or sample.count("\t") > sample.count(",") else ","
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [("sheet1", rows)]


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref or "")
    if not letters:
        return None
    idx = 0
    for ch in letters.group(0):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _clean_float_text(raw):
    """Excel stores 0.1+0.2 as 0.30000000000000004; keep what a person would have typed."""
    try:
        value = float(raw)
    except ValueError:
        return raw
    if not math.isfinite(value):
        return raw
    text = format(value, ".15g")
    if "e" in text or "E" in text:
        return raw
    return text


def read_xlsx(path):
    sheets = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(SS_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(SS_NS + "t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(PKG_REL_NS + "Relationship"):
                rels[rel.get("Id")] = rel.get("Target")
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        for sheet in workbook.iter(SS_NS + "sheet"):
            target = rels.get(sheet.get(REL_NS + "id"), "")
            target = target.lstrip("/")
            part = target if target.startswith("xl/") else "xl/" + target
            if part not in names:
                continue
            root = ET.fromstring(z.read(part))
            rows = []
            for row in root.iter(SS_NS + "row"):
                cells = {}
                for c in row.findall(SS_NS + "c"):
                    idx = _col_index(c.get("r"))
                    kind = c.get("t")
                    v = c.find(SS_NS + "v")
                    if kind == "s" and v is not None:
                        try:
                            value = shared[int(v.text)]
                        except (ValueError, IndexError):
                            value = ""
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(SS_NS + "t"))
                    elif v is not None and v.text is not None:
                        value = v.text if kind in ("str", "b", "e") else _clean_float_text(v.text)
                    else:
                        value = ""
                    if idx is None:
                        idx = len(cells)
                    cells[idx] = value
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
            sheets.append((sheet.get("name") or f"sheet{len(sheets) + 1}", rows))
    return sheets


def read_raw_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    if ext in (".csv", ".tsv", ".txt"):
        return read_delimited(path)
    raise SystemExit(f"不支持的原始数据文件: {path}（支持 .csv / .tsv / .txt / .xlsx）；.xls 请先另存为 .xlsx")


# ---------------------------------------------------------------------------
# table model
# ---------------------------------------------------------------------------

NUMERIC_CELL_RE = re.compile(r"^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*%?\s*$")


def _numeric(cell):
    text = (cell or "").strip().replace(",", "") if re.match(r"^\s*[-+]?\d{1,3}(,\d{3})+(\.\d+)?\s*$", cell or "") else (cell or "").strip()
    if not NUMERIC_CELL_RE.match(text):
        return None
    cleaned = text.rstrip("%").strip()
    try:
        return float(cleaned), cleaned
    except ValueError:
        return None


def column_letter(idx):
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _decimals(cleaned):
    s = re.split(r"[eE]", cleaned)[0]
    return len(s.split(".", 1)[1]) if "." in s else 0


def sheet_columns(rows):
    """Header names and numeric columns: {col: [(row_index, value, cleaned)]}.

    The first row is a header when most of its non-empty cells are not numbers. Values in a column
    are padded to the column's largest number of decimals: spreadsheets drop trailing zeros (3.40 is
    stored as 3.4), which would otherwise starve the terminal-digit test of zeros.
    """
    header = []
    start = 0
    if rows:
        first = [c for c in rows[0] if (c or "").strip()]
        if first and sum(1 for c in first if _numeric(c) is None) > len(first) / 2:
            header = rows[0]
            start = 1
    columns = defaultdict(list)
    for ri in range(start, len(rows)):
        for ci, cell in enumerate(rows[ri]):
            parsed = _numeric(cell)
            if parsed is not None:
                columns[ci].append((ri, parsed[0], parsed[1]))
    for ci, values in columns.items():
        width = max((_decimals(c) for _r, _v, c in values), default=0)
        if width:
            columns[ci] = [(r, v, c if _decimals(c) == width else f"{v:.{width}f}") for r, v, c in values]
    names = {ci: (header[ci].strip() if ci < len(header) and header[ci] else "") for ci in columns}
    return names, dict(columns), start


def is_id_column(name, values):
    if name and ID_HEADER_RE.match(name):
        return True
    nums = [v for _r, v, _c in values]
    if len(nums) < 5 or any(v != int(v) for v in nums):
        return False
    return len(set(nums)) == len(nums) and all(b > a for a, b in zip(nums, nums[1:]))


def _sig_digits(cleaned):
    return len(re.split(r"[eE]", cleaned.lstrip("+-"))[0].replace(".", "").lstrip("0"))


def is_continuous(values):
    if not values:
        return False
    non_integer = sum(1 for _r, v, c in values if "." in c and v != int(v))
    sig = sorted(_sig_digits(c) for _r, _v, c in values)
    return non_integer >= 0.8 * len(values) and sig[len(sig) // 2] >= 3


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def run_raw_data_checks(path, helpers):
    """``helpers`` carries the table checks of geng_check (terminal digits, Benford, duplicate blocks)."""
    label = os.path.basename(path)
    findings = []
    sheets_info = []
    for sheet_name, rows in read_raw_file(path):
        names, columns, start = sheet_columns(rows)
        where = lambda ci: f"{label}#{sheet_name}!{column_letter(ci)}" + (f"({names[ci]})" if names.get(ci) else "")
        data_cols = {ci: vals for ci, vals in columns.items() if not is_id_column(names.get(ci, ""), vals)}
        sheets_info.append({"sheet": sheet_name, "rows": max(0, len(rows) - start), "numericColumns": len(data_cols),
                            "idColumnsSkipped": [column_letter(ci) for ci in columns if ci not in data_cols]})

        # terminal digits per column, only when the range covers >= 50 steps of the last digit
        for ci, vals in sorted(data_cols.items()):
            if len(vals) < 30:
                continue
            width = max(_decimals(c) for _r, _v, c in vals)
            nums = [v for _r, v, _c in vals]
            if (max(nums) - min(nums)) * 10 ** width < 50:
                continue
            f = helpers["terminal"]([(v, c) for _r, v, c in vals], where(ci), min_n=30)
            if f:
                f["stat"]["source"] = "raw data"
                findings.append(f)

        all_numbers = [(v, c) for vals in data_cols.values() for _r, v, c in vals]
        f = helpers["benford"](all_numbers, f"{label}#{sheet_name}")
        if f:
            findings.append(f)

        # identical columns
        duplicate_of = {}
        seq = {ci: tuple(c for _r, _v, c in vals) for ci, vals in data_cols.items() if len(vals) >= 10 and len(set(c for _r, _v, c in vals)) > 1}
        groups = defaultdict(list)
        for ci, key in seq.items():
            groups[key].append(ci)
        for cols in groups.values():
            if len(cols) < 2:
                continue
            for other in cols[1:]:
                duplicate_of[other] = cols[0]
            findings.append({
                "type": "duplicate_column",
                "severity": "medium",
                "location": "; ".join(where(ci) for ci in cols),
                "stat": {"length": len(seq[cols[0]]), "columns": [column_letter(ci) for ci in cols]},
                "explanation": "原始数据里有两列（或多列）数值逐行完全相同。可能是同一变量导出了两次，"
                               "也可能是把一列复制成了另一个变量；请核对这两列是否应当是不同的测量。",
            })

        # identical rows (continuous values only)
        row_values = defaultdict(list)
        for ci, vals in data_cols.items():
            if ci in duplicate_of:
                continue
            for r, v, c in vals:
                row_values[r].append((ci, v, c))
        rowmap = defaultdict(list)
        for r, cells in row_values.items():
            if len(cells) < 4 or all(v == int(v) for _ci, v, _c in cells):
                continue
            rowmap[tuple((ci, c) for ci, _v, c in sorted(cells))].append(r)
        for key, rows_hit in rowmap.items():
            if len(rows_hit) < 2:
                continue
            findings.append({
                "type": "duplicate_row",
                "severity": "high",
                "location": f"{label}#{sheet_name} 第 " + "、".join(str(r + 1) for r in rows_hit[:8]) + " 行",
                "stat": {"row": [c for _ci, c in key], "occurrences": len(rows_hit), "source": "raw data"},
                "explanation": f"原始数据中有 {len(rows_hit)} 行的 {len(key)} 个数值（含小数）逐位相同。"
                               "不同样本/被试的连续测量值完全一致几乎不可能是巧合，常见于复制粘贴行；请核对是否为重复录入。",
            })

        # repeated runs inside continuous columns
        series = [(where(ci), [(v, c) for _r, v, c in vals]) for ci, vals in sorted(data_cols.items())
                  if ci not in duplicate_of and is_continuous(vals)]
        for f in helpers["blocks"](series, min_block=4):
            f["stat"]["source"] = "raw data"
            findings.append(f)
    return findings, {"file": label, "sheets": sheets_info}
