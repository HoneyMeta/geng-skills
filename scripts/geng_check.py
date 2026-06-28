#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geng_check.py — geng-skills 论文诚信自检工具（数据取证 + 图像抽取 + 报告生成）

灵感来自 "耿同学" 的学术打假方法：从【数值数据异常】和【图片 PS 痕迹】两个方向，
在投稿前帮作者做一次科研诚信自检（self-check），而不是事后逃避检测。

设计原则（与 ai-check 一致）：
  - 确定性脚本只负责【筛信号】：抽数据、跑统计、抽图片、算哈希；不下"造假"结论。
  - 是否真的有问题，由【模型（含视觉能力）判读】，以避免误报。
  - 零硬依赖：docx 用标准库 zipfile 解析（不需要 python-docx）；PDF 缺 reportlab 时降级出 HTML。

子命令：
  data    从 .docx / .tex / 目录抽取数值表并运行取证统计 -> findings.json
  images  从 .docx / .tex / 目录抽取所有图片 + 生成清单 manifest.json（可选缩略图拼版）
  grim    GRIM 自洽检验：给定 均值/样本量/小数位，判断报告均值在数学上是否可能
  report  合并 findings.json + 模型给出的 verdicts.json -> report.pdf（缺 reportlab 则 report.html）

用法示例：
  python geng_check.py data   --input paper.docx --out findings.json
  python geng_check.py data   --input paper.tex  --out findings.json
  python geng_check.py images --input paper.docx --out-dir extracted_images --contact-sheet
  python geng_check.py grim   --mean 3.45 --n 20 --decimals 2
  python geng_check.py report --findings findings.json --verdicts verdicts.json --out report.pdf
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime

# Windows 控制台默认可能是 GBK/cp1252，强制 UTF-8 输出，避免中文打印报错。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# 数字解析
# ---------------------------------------------------------------------------

# 匹配：可选符号、千分位、整数/小数、科学计数。保留原始字符串以便分析"末位数字"。
NUM_RE = re.compile(r"(?<![\w.])([-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+\.\d+|[-+]?\.\d+|[-+]?\d+)(?:[eE][-+]?\d+)?(?![\w])")


def parse_number(token):
    """把原始 token 解析成 (float值, 清洗后的数字串)；失败返回 None。"""
    raw = token.strip()
    cleaned = raw.replace(",", "")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return val, cleaned


def extract_numbers_from_text(text):
    """返回 [(float, cleaned_str)]。"""
    out = []
    for m in NUM_RE.finditer(text):
        parsed = parse_number(m.group(0))
        if parsed is not None:
            out.append(parsed)
    return out


def last_significant_digit(cleaned):
    """报告数字的最后一位数字字符（用于末位数字分布检验）。"""
    s = cleaned.lstrip("+-")
    # 去掉科学计数后缀
    s = re.split(r"[eE]", s)[0]
    digits = [c for c in s if c.isdigit()]
    if not digits:
        return None
    return digits[-1]


def first_significant_digit(cleaned):
    """首位非零数字（用于 Benford）。"""
    s = cleaned.lstrip("+-")
    s = re.split(r"[eE]", s)[0]
    for c in s:
        if c.isdigit() and c != "0":
            return c
    return None


def decimal_places(cleaned):
    s = re.split(r"[eE]", cleaned)[0]
    if "." in s:
        return len(s.split(".", 1)[1])
    return 0


# ---------------------------------------------------------------------------
# 卡方 p 值（无 scipy，自带正则化不完全 Gamma）
# ---------------------------------------------------------------------------

def _gammainc_lower_regularized(s, x):
    """正则化下不完全 Gamma P(s, x)，s>0, x>=0。级数 + 连分式。"""
    if x < 0 or s <= 0:
        return 0.0
    if x == 0:
        return 0.0
    if x < s + 1.0:
        # 级数展开
        term = 1.0 / s
        total = term
        n = s
        for _ in range(1000):
            n += 1.0
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    else:
        # 连分式（计算上不完全，再取补）
        tiny = 1e-300
        b = x + 1.0 - s
        c = 1.0 / tiny
        d = 1.0 / b
        h = d
        for i in range(1, 1000):
            an = -i * (i - s)
            b += 2.0
            d = an * d + b
            if abs(d) < tiny:
                d = tiny
            c = b + an / c
            if abs(c) < tiny:
                c = tiny
            d = 1.0 / d
            delta = d * c
            h *= delta
            if abs(delta - 1.0) < 1e-15:
                break
        q = math.exp(-x + s * math.log(x) - math.lgamma(s)) * h
        return 1.0 - q


def chi_square_pvalue(chi2, dof):
    """卡方分布上尾 p 值 = 1 - P(dof/2, chi2/2)。"""
    if chi2 <= 0 or dof <= 0:
        return 1.0
    return 1.0 - _gammainc_lower_regularized(dof / 2.0, chi2 / 2.0)


def chi_square_test(observed_counts, expected_props):
    """observed_counts: list[int]; expected_props: list[float] (sum=1)。返回 (chi2, dof, p)。"""
    n = sum(observed_counts)
    chi2 = 0.0
    for obs, p in zip(observed_counts, expected_props):
        exp = n * p
        if exp > 0:
            chi2 += (obs - exp) ** 2 / exp
    dof = len(observed_counts) - 1
    return chi2, dof, chi_square_pvalue(chi2, dof)


# ---------------------------------------------------------------------------
# docx / latex 解析 -> 表格 + 文本
# ---------------------------------------------------------------------------

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx(path):
    """返回 (full_text, tables) ；tables 是 list[ list[ list[str] ] ]（表 -> 行 -> 单元格）。零依赖。"""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)

    def cell_text(tc):
        parts = []
        for t in tc.iter(W_NS + "t"):
            parts.append(t.text or "")
        return "".join(parts).strip()

    def para_text(p):
        parts = []
        for t in p.iter(W_NS + "t"):
            parts.append(t.text or "")
        return "".join(parts).strip()

    tables = []
    for tbl in root.iter(W_NS + "tbl"):
        rows = []
        for tr in tbl.iter(W_NS + "tr"):
            cells = [cell_text(tc) for tc in tr.findall(W_NS + "tc")]
            rows.append(cells)
        if rows:
            tables.append(rows)

    # 全文（含段落与表格文本）
    texts = []
    body = root.find(W_NS + "body")
    if body is not None:
        for p in body.iter(W_NS + "p"):
            txt = para_text(p)
            if txt:
                texts.append(txt)
    full_text = "\n".join(texts)
    return full_text, tables


LATEX_TABULAR_RE = re.compile(r"\\begin\{(?:tabular|tabularx|longtable|array)\}.*?\\end\{(?:tabular|tabularx|longtable|array)\}", re.DOTALL)


def _strip_latex(cell):
    s = cell
    s = re.sub(r"\\(?:textbf|textit|mathbf|emph|num|SI|si)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)   # 其余命令
    s = s.replace("{", " ").replace("}", " ").replace("\\", " ")
    s = s.replace("~", " ").replace("&nbsp;", " ")
    return s.strip()


def read_latex(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    # 去注释（行内 % 之后，未转义）
    content = re.sub(r"(?<!\\)%.*", "", content)

    tables = []
    for m in LATEX_TABULAR_RE.finditer(content):
        block = m.group(0)
        # 去掉 begin/end 行本身
        inner = re.sub(r"\\begin\{[^}]*\}(?:\[[^\]]*\])?(?:\{[^}]*\})?", "", block, count=1)
        inner = re.sub(r"\\end\{[^}]*\}", "", inner)
        rows = []
        for raw_row in re.split(r"\\\\", inner):
            raw_row = re.sub(r"\\hline|\\toprule|\\midrule|\\bottomrule|\\cline\{[^}]*\}", "", raw_row)
            if not raw_row.strip():
                continue
            cells = [_strip_latex(c) for c in raw_row.split("&")]
            rows.append(cells)
        if rows:
            tables.append(rows)
    return content, tables


def load_source(input_path):
    """支持 .docx / .tex / 目录（递归收集 .tex）。返回 (full_text, tables, source_label)。"""
    tables = []
    texts = []
    if os.path.isdir(input_path):
        tex_files = []
        for dirpath, _, files in os.walk(input_path):
            for fn in files:
                if fn.lower().endswith((".tex",)):
                    tex_files.append(os.path.join(dirpath, fn))
        for tf in sorted(tex_files):
            txt, tbls = read_latex(tf)
            texts.append(txt)
            tables.extend(tbls)
        return "\n".join(texts), tables, f"latex-project:{os.path.basename(os.path.normpath(input_path))}"

    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".docx":
        full_text, tables = read_docx(input_path)
        return full_text, tables, os.path.basename(input_path)
    if ext in (".tex", ".txt"):
        full_text, tables = read_latex(input_path)
        return full_text, tables, os.path.basename(input_path)
    raise SystemExit(f"不支持的输入类型: {input_path}（支持 .docx / .tex / 目录）")


# ---------------------------------------------------------------------------
# 把表格转成数值序列（列、行）
# ---------------------------------------------------------------------------

def table_numeric_series(table):
    """从一张表抽出每列、每行的数值序列。返回 dict: {'columns': [...], 'rows': [...]}，
    每个序列是 [(float, cleaned_str), ...]。"""
    # 规整列数
    ncols = max((len(r) for r in table), default=0)
    columns = [[] for _ in range(ncols)]
    rows = []
    for r in table:
        row_vals = []
        for ci in range(ncols):
            cell = r[ci] if ci < len(r) else ""
            nums = extract_numbers_from_text(cell)
            if len(nums) == 1:
                columns[ci].append(nums[0])
                row_vals.append(nums[0])
        rows.append(row_vals)
    return {"columns": columns, "rows": rows}


# ---------------------------------------------------------------------------
# 取证检查
# ---------------------------------------------------------------------------

def check_terminal_digits(numbers, location, min_n=30):
    """末位数字应近似均匀（耿同学经典：2400 个数据末尾全是 5）。"""
    digits = [last_significant_digit(c) for (_v, c) in numbers]
    digits = [d for d in digits if d is not None]
    if len(digits) < min_n:
        return None
    counts = Counter(digits)
    observed = [counts.get(str(d), 0) for d in range(10)]
    chi2, dof, p = chi_square_test(observed, [0.1] * 10)
    dist = {str(d): counts.get(str(d), 0) for d in range(10)}
    top_digit, top_count = max(dist.items(), key=lambda kv: kv[1])
    severity = "info"
    if p < 1e-6:
        severity = "high"
    elif p < 1e-3:
        severity = "medium"
    elif p < 0.01:
        severity = "low"
    if severity == "info":
        return None
    return {
        "type": "terminal_digit_uniformity",
        "severity": severity,
        "location": location,
        "stat": {"n": len(digits), "chi2": round(chi2, 3), "dof": dof, "pValue": p,
                  "distribution": dist, "dominantDigit": top_digit,
                  "dominantShare": round(top_count / len(digits), 3)},
        "explanation": "末位有效数字偏离均匀分布。真实测量数据的末位数字通常接近均匀；"
                       "强烈偏斜（尤其某位占比过高或呈规律）是数据编造的典型信号。需人工/模型确认是否由单位换算、刻度或四舍五入导致。",
    }


def check_benford(numbers, location, min_n=50):
    """首位数字 Benford 检验（适用于跨多个数量级的数据）。"""
    firsts = [first_significant_digit(c) for (_v, c) in numbers]
    firsts = [d for d in firsts if d is not None]
    if len(firsts) < min_n:
        return None
    # 数量级跨度判断：Benford 仅在跨度足够时有意义
    vals = [abs(v) for (v, _c) in numbers if v != 0]
    if not vals:
        return None
    span = math.log10(max(vals)) - math.log10(min(vals)) if min(vals) > 0 else 0
    if span < 2:
        return None  # 跨度不足，Benford 不适用
    counts = Counter(firsts)
    observed = [counts.get(str(d), 0) for d in range(1, 10)]
    expected = [math.log10(1 + 1.0 / d) for d in range(1, 10)]
    chi2, dof, p = chi_square_test(observed, expected)
    severity = "info"
    if p < 1e-4:
        severity = "medium"
    elif p < 0.01:
        severity = "low"
    if severity == "info":
        return None
    return {
        "type": "benford_first_digit",
        "severity": severity,
        "location": location,
        "stat": {"n": len(firsts), "chi2": round(chi2, 3), "dof": dof, "pValue": p,
                  "orderSpan": round(span, 2),
                  "observed": {str(d): counts.get(str(d), 0) for d in range(1, 10)}},
        "explanation": "首位数字分布偏离 Benford 定律。横跨多个数量级的自然数据通常服从 Benford；"
                       "显著偏离值得关注，但需排除有界量、单位、人为量纲等正当原因。",
    }


def check_arithmetic_progression(series_vals, location, min_len=5, tol=1e-9):
    """等差数列检测：真实数据极少呈现完美等差。"""
    vals = [v for (v, _c) in series_vals]
    if len(vals) < min_len:
        return None
    diffs = [round(vals[i + 1] - vals[i], 12) for i in range(len(vals) - 1)]
    if not diffs:
        return None
    d0 = diffs[0]
    if abs(d0) < tol:
        return None  # 全相等单独由 duplicate 检查覆盖
    max_dev = max(abs(d - d0) for d in diffs)
    scale = max(abs(x) for x in vals) or 1.0
    if max_dev <= tol or max_dev / scale < 1e-6:
        return {
            "type": "arithmetic_progression",
            "severity": "medium",
            "location": location,
            "stat": {"n": len(vals), "commonDifference": d0,
                      "sample": vals[:8]},
            "explanation": "该数值序列是（近乎）完美的等差数列。实验/统计数据极少天然呈现完美线性，"
                           "可能为人为填充。需确认是否为坐标轴、序号、设定值等正当线性量。",
        }
    return None


def check_duplicate_blocks(all_series, min_block=4):
    """在所有数值序列里寻找重复的数据块（>=min_block 个数完全相同的子序列）。"""
    findings = []
    # 规范化为 cleaned 字符串元组
    seqs = []
    for loc, series in all_series:
        cleaned = [c for (_v, c) in series]
        if len(cleaned) >= min_block:
            seqs.append((loc, cleaned))
    seen = defaultdict(list)
    for loc, cleaned in seqs:
        n = len(cleaned)
        for i in range(0, n - min_block + 1):
            block = tuple(cleaned[i:i + min_block])
            # 跳过全相同块（重复值另有检查）
            if len(set(block)) == 1:
                continue
            seen[block].append((loc, i))
    reported = set()
    for block, occ in seen.items():
        if len(occ) >= 2 and block not in reported:
            reported.add(block)
            findings.append({
                "type": "duplicate_data_block",
                "severity": "high",
                "location": "; ".join(sorted({o[0] for o in occ})),
                "stat": {"block": list(block), "occurrences": len(occ),
                          "where": [f"{o[0]}@idx{o[1]}" for o in occ[:6]]},
                "explanation": "同一段数值块在多处重复出现。跨样本/跨实验条件出现相同数据块，"
                               "是数据复制粘贴的强信号，'不小心'难以解释。",
            })
    return findings


def check_duplicate_rows(tables_series, source_label):
    """跨表/表内完全相同的行。"""
    findings = []
    rowmap = defaultdict(list)
    for ti, ts in enumerate(tables_series):
        for ri, row in enumerate(ts["rows"]):
            if len(row) >= 3:
                key = tuple(c for (_v, c) in row)
                rowmap[key].append(f"{source_label}#表{ti + 1}行{ri + 1}")
    for key, locs in rowmap.items():
        if len(locs) >= 2:
            findings.append({
                "type": "duplicate_row",
                "severity": "high",
                "location": "; ".join(locs[:8]),
                "stat": {"row": list(key), "occurrences": len(locs)},
                "explanation": "完全相同的数据行重复出现（≥3 个数值且全部一致）。需确认是否同一行被复制。",
            })
    return findings


def check_decimal_consistency(numbers, location, min_n=10):
    """小数位异常一致 / 异常不一致：同一测量列内精度本应一致，出现多种精度
    可能源自不同数据来源拼接或复制粘贴。仅作弱信号（low），常有正当原因（去尾零）。"""
    dps = [decimal_places(c) for (_v, c) in numbers]
    if len(dps) < min_n:
        return None
    decimal_vals = sum(1 for d in dps if d > 0)
    # 整数为主的列（如计数、序号）不适用
    if decimal_vals < len(dps) * 0.6:
        return None
    counts = Counter(dps)
    distinct = len([k for k in counts if k > 0]) + (1 if counts.get(0, 0) else 0)
    mode_dp, mode_count = counts.most_common(1)[0]
    dominant_share = mode_count / len(dps)
    # 只有当精度种类≥3 且没有一个主导精度时才提示（降低误报）
    if distinct >= 3 and dominant_share < 0.7:
        return {
            "type": "decimal_place_inconsistency",
            "severity": "low",
            "location": location,
            "stat": {"n": len(dps), "distinctPrecisions": distinct,
                      "dominantDecimals": mode_dp, "dominantShare": round(dominant_share, 3),
                      "profile": dict(sorted(counts.items()))},
            "explanation": "同一数值列出现多种小数精度且无主导精度。真实测量通常精度一致；"
                           "混杂精度可能由不同来源拼接或复制粘贴造成。注意：去尾零"
                           "（1.50→1.5）、整数与小数混排等也会导致，需人工确认是否有正当原因。",
        }
    return None


def run_data_forensics(full_text, tables, source_label):
    findings = []
    tables_series = [table_numeric_series(t) for t in tables]

    # 收集"命名"序列：表的每列/每行
    all_series = []
    for ti, ts in enumerate(tables_series):
        for ci, col in enumerate(ts["columns"]):
            if len(col) >= 3:
                all_series.append((f"{source_label}#表{ti + 1}列{ci + 1}", col))
        for ri, row in enumerate(ts["rows"]):
            if len(row) >= 3:
                all_series.append((f"{source_label}#表{ti + 1}行{ri + 1}", row))

    # 全文数字（用于全局末位/Benford）
    text_numbers = extract_numbers_from_text(full_text)
    table_numbers = [pair for ts in tables_series for col in ts["columns"] for pair in col]
    all_numbers = text_numbers + table_numbers

    # 全局末位数字 + Benford
    f = check_terminal_digits(all_numbers, f"{source_label}#全文+表格", min_n=30)
    if f:
        findings.append(f)
    f = check_benford(all_numbers, f"{source_label}#全文+表格")
    if f:
        findings.append(f)

    # 逐表/逐列末位（更细粒度）
    for loc, series in all_series:
        if len(series) >= 30:
            f = check_terminal_digits(series, loc, min_n=30)
            if f:
                findings.append(f)

    # 等差数列
    for loc, series in all_series:
        f = check_arithmetic_progression(series, loc)
        if f:
            findings.append(f)

    # 小数位异常一致（仅对列，行内常混排不同量纲，误报多）
    for ti, ts in enumerate(tables_series):
        for ci, col in enumerate(ts["columns"]):
            f = check_decimal_consistency(col, f"{source_label}#表{ti + 1}列{ci + 1}")
            if f:
                findings.append(f)

    # 重复数据块 / 重复行
    findings.extend(check_duplicate_blocks(all_series))
    findings.extend(check_duplicate_rows(tables_series, source_label))

    # 为每个 finding 附 id
    for i, fd in enumerate(findings, 1):
        fd["id"] = f"data-{i:04d}"

    summary = Counter(fd["severity"] for fd in findings)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "data",
        "source": source_label,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "tableCount": len(tables),
            "numberCountText": len(text_numbers),
            "numberCountTables": len(table_numbers),
            "seriesAnalyzed": len(all_series),
        },
        "severityCounts": dict(summary),
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# 图片抽取
# ---------------------------------------------------------------------------

def sha1_of(data):
    return hashlib.sha1(data).hexdigest()


def extract_images_docx(path, out_dir):
    items = []
    with zipfile.ZipFile(path) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        for n in sorted(media):
            data = z.read(n)
            base = os.path.basename(n)
            out_path = os.path.join(out_dir, base)
            with open(out_path, "wb") as f:
                f.write(data)
            items.append({"file": base, "origin": n, "bytes": len(data), "sha1": sha1_of(data)})
    return items


INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
CAPTION_RE = re.compile(r"\\caption\{")


def extract_images_latex(input_path, out_dir):
    items = []
    if os.path.isdir(input_path):
        roots = []
        for dirpath, _, files in os.walk(input_path):
            for fn in files:
                if fn.lower().endswith(".tex"):
                    roots.append(os.path.join(dirpath, fn))
        base_dirs = [input_path]
    else:
        roots = [input_path]
        base_dirs = [os.path.dirname(os.path.abspath(input_path))]

    exts = ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".tif", ".tiff", ".gif", ".bmp"]
    seen = set()
    for tex in roots:
        with open(tex, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        content_nc = re.sub(r"(?<!\\)%.*", "", content)
        tex_dir = os.path.dirname(os.path.abspath(tex))
        for m in INCLUDEGRAPHICS_RE.finditer(content_nc):
            ref = m.group(1).strip()
            # 找到附近的 caption 作为上下文
            ctx = ""
            tail = content_nc[m.end():m.end() + 400]
            cm = re.search(r"\\caption\{(.+?)\}", tail, re.DOTALL)
            if cm:
                ctx = _strip_latex(cm.group(1))[:160]
            # 解析实际文件路径
            resolved = None
            search_dirs = [tex_dir] + base_dirs
            for d in search_dirs:
                for ext in exts:
                    cand = os.path.join(d, ref + ext)
                    if os.path.isfile(cand):
                        resolved = cand
                        break
                if resolved:
                    break
            if resolved is None:
                items.append({"file": None, "origin": ref, "resolved": False,
                               "refContext": ctx, "bytes": 0, "sha1": None})
                continue
            with open(resolved, "rb") as f:
                data = f.read()
            base = os.path.basename(resolved)
            if base in seen:
                base = f"{len(seen)}_" + base
            seen.add(base)
            out_path = os.path.join(out_dir, base)
            with open(out_path, "wb") as f:
                f.write(data)
            items.append({"file": base, "origin": ref, "resolved": True,
                           "refContext": ctx, "bytes": len(data), "sha1": sha1_of(data)})
    return items


def enrich_dimensions(items, out_dir):
    try:
        from PIL import Image
    except ImportError:
        return
    for it in items:
        if not it.get("file"):
            continue
        p = os.path.join(out_dir, it["file"])
        try:
            with Image.open(p) as im:
                it["width"], it["height"] = im.size
                it["format"] = im.format
        except Exception:
            pass


def build_contact_sheet(items, out_dir, cols=4, thumb=320):
    try:
        from PIL import Image
    except ImportError:
        return None
    imgs = []
    for it in items:
        if not it.get("file"):
            continue
        p = os.path.join(out_dir, it["file"])
        try:
            im = Image.open(p).convert("RGB")
            im.thumbnail((thumb, thumb))
            imgs.append((it["file"], im))
        except Exception:
            continue
    if not imgs:
        return None
    rows = (len(imgs) + cols - 1) // cols
    pad = 12
    cell_w = thumb + pad
    cell_h = thumb + pad + 18
    sheet = Image.new("RGB", (cols * cell_w + pad, rows * cell_h + pad), "white")
    from PIL import ImageDraw
    draw = ImageDraw.Draw(sheet)
    for idx, (name, im) in enumerate(imgs):
        r, c = divmod(idx, cols)
        x = pad + c * cell_w
        y = pad + r * cell_h
        sheet.paste(im, (x, y))
        draw.text((x, y + thumb + 2), name[:40], fill="black")
    out_path = os.path.join(out_dir, "_contact_sheet.png")
    sheet.save(out_path)
    return out_path


def run_image_extraction(input_path, out_dir, contact_sheet=False):
    os.makedirs(out_dir, exist_ok=True)
    ext = "" if os.path.isdir(input_path) else os.path.splitext(input_path)[1].lower()
    if ext == ".docx":
        items = extract_images_docx(input_path, out_dir)
        source_label = os.path.basename(input_path)
    else:
        items = extract_images_latex(input_path, out_dir)
        source_label = (os.path.basename(os.path.normpath(input_path))
                        if os.path.isdir(input_path) else os.path.basename(input_path))
    enrich_dimensions(items, out_dir)

    # 自动检测：完全相同的图片文件（sha1 一致）
    byhash = defaultdict(list)
    for it in items:
        if it.get("sha1"):
            byhash[it["sha1"]].append(it["file"])
    exact_dups = [{"sha1": h, "files": fs} for h, fs in byhash.items() if len(fs) > 1]

    sheet = build_contact_sheet(items, out_dir) if contact_sheet else None

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "images",
        "source": source_label,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "outDir": out_dir,
        "imageCount": sum(1 for it in items if it.get("file")),
        "unresolvedCount": sum(1 for it in items if not it.get("file")),
        "exactDuplicateFiles": exact_dups,
        "contactSheet": sheet,
        "images": items,
    }
    return manifest


# ---------------------------------------------------------------------------
# GRIM
# ---------------------------------------------------------------------------

def grim_check(mean, n, decimals):
    """GRIM：报告均值 mean（小数位 decimals）在样本量 n 下是否在数学上可能。"""
    granularity = 10 ** (-decimals)
    nearest_sum = round(mean * n)
    consistent = False
    matched_sum = None
    for s in (nearest_sum - 1, nearest_sum, nearest_sum + 1):
        recomputed = round(s / n, decimals)
        if abs(recomputed - round(mean, decimals)) < granularity / 2:
            consistent = True
            matched_sum = s
            break
    return {
        "mean": mean, "n": n, "decimals": decimals,
        "consistent": consistent,
        "impliedSum": matched_sum if consistent else nearest_sum,
        "note": ("均值在数学上可由整数总和得到，GRIM 通过。"
                 if consistent else
                 "报告均值无法由任何整数总和在该小数位下还原，GRIM 不一致——可能是笔误或编造。"),
    }


def sprite_lite(mean, sd, n, vmin, vmax, sample=False):
    """SPRITE-lite：给定 均值/标准差/样本量 与取值上下界，判断报告标准差在数学上是否可能。

    有界数据 [vmin, vmax]、均值固定为 mean 时，总体方差的理论上界为 (mean-vmin)(vmax-mean)
    （由两点分布达到）。样本标准差 (除以 n-1) 还需乘 n/(n-1)。
    若报告 SD 超过该上界，则在数学上不可能——SD 过大无法由该区间内的数据产生。
    """
    if not (vmin <= mean <= vmax):
        return {"consistent": False, "reason": "out_of_bounds",
                "note": f"均值 {mean} 不在取值区间 [{vmin}, {vmax}] 内，本身已不可能。"}
    if n < 2:
        return {"consistent": True, "note": "样本量过小，SPRITE 不适用。"}

    pop_var_max = (mean - vmin) * (vmax - mean)        # 总体方差上界
    factor = n / (n - 1) if sample else 1.0
    sd_max = math.sqrt(max(pop_var_max, 0.0) * factor)  # 样本/总体 SD 上界
    consistent = sd <= sd_max + 1e-9
    return {
        "mean": mean, "sd": sd, "n": n, "min": vmin, "max": vmax,
        "sdInterpretedAs": "sample(n-1)" if sample else "population(n)",
        "maxPossibleSd": round(sd_max, 6),
        "consistent": consistent,
        "note": ("报告标准差不超过该区间/均值下的理论上界，SPRITE-lite 通过（不代表数据真实，只代表 SD 可能）。"
                 if consistent else
                 f"报告标准差 {sd} 超过理论上界 {round(sd_max, 6)}——在该取值区间与均值下数学上不可能，"
                 "强烈提示 SD、均值或样本量有误或被编造。"),
    }


# ---------------------------------------------------------------------------
# 报告生成（reportlab，缺失则 HTML）
# ---------------------------------------------------------------------------

SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
SEV_LABEL = {"high": "高", "medium": "中", "low": "低", "info": "提示"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_report_model(findings_path, verdicts_path):
    data = load_json(findings_path) if findings_path and os.path.isfile(findings_path) else {}
    verdicts = load_json(verdicts_path) if verdicts_path and os.path.isfile(verdicts_path) else {}
    return data, verdicts


def generate_html_report(data, verdicts, out_html, template_path=None):
    rows = []
    findings = data.get("findings", [])
    vmap = {v.get("id"): v for v in verdicts.get("verdicts", [])}
    findings_sorted = sorted(findings, key=lambda f: SEV_ORDER.get(f.get("severity"), 9))
    for fd in findings_sorted:
        v = vmap.get(fd.get("id"), {})
        verdict = v.get("verdict", "—")
        comment = v.get("comment", "")
        rows.append(
            f"<tr class='sev-{fd.get('severity')}'>"
            f"<td>{fd.get('id','')}</td>"
            f"<td>{SEV_LABEL.get(fd.get('severity'),'')}</td>"
            f"<td>{fd.get('type','')}</td>"
            f"<td>{_html_escape(fd.get('location',''))}</td>"
            f"<td>{_html_escape(fd.get('explanation',''))}<br><small>{_html_escape(json.dumps(fd.get('stat',{}), ensure_ascii=False))}</small></td>"
            f"<td><b>{_html_escape(str(verdict))}</b><br>{_html_escape(comment)}</td>"
            f"</tr>"
        )
    image_rows = []
    for iv in verdicts.get("imageVerdicts", []):
        image_rows.append(
            f"<tr class='sev-{iv.get('severity','info')}'>"
            f"<td>{_html_escape(iv.get('file',''))}</td>"
            f"<td>{SEV_LABEL.get(iv.get('severity','info'),'')}</td>"
            f"<td>{_html_escape(iv.get('issue',''))}</td>"
            f"<td>{_html_escape(iv.get('comment',''))}</td>"
            f"</tr>"
        )
    sev_counts = data.get("severityCounts", {})
    overall = verdicts.get("overallAssessment", "（待模型填写整体结论）")

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>geng-skills 论文诚信自检报告</title>
<style>
body{{font-family:'Segoe UI',-apple-system,Arial,'Microsoft YaHei',sans-serif;margin:32px;color:#1a1a1a}}
h1{{text-align:center}}
.meta{{background:#f6f7f9;padding:12px 16px;border-radius:8px;margin:16px 0;font-size:14px}}
.cards{{display:flex;gap:12px;margin:16px 0;flex-wrap:wrap}}
.card{{flex:1;min-width:120px;background:#fff;border:1px solid #e4e6eb;border-radius:8px;padding:12px;text-align:center}}
.card .n{{font-size:28px;font-weight:700}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}}
th,td{{border:1px solid #e4e6eb;padding:8px;vertical-align:top;text-align:left}}
th{{background:#f0f2f5}}
.sev-high{{background:#fdecec}}.sev-medium{{background:#fff5e6}}.sev-low{{background:#fffbe6}}
.overall{{background:#eef6ff;border:1px solid #cfe3ff;border-radius:8px;padding:16px;margin:16px 0}}
small{{color:#666}}
.footer{{margin-top:24px;font-size:12px;color:#888;text-align:center}}
</style></head><body>
<h1>geng-skills · 论文诚信自检报告</h1>
<div class="meta">
<b>文档：</b>{_html_escape(str(data.get('source','—')))}
<b>生成时间：</b>{datetime.now().strftime('%Y-%m-%d %H:%M')}
<b>检测类型：</b>数据完整性 + 图像取证（投稿前自检）
</div>
<div class="cards">
<div class="card"><div class="n">{sev_counts.get('high',0)}</div>高风险</div>
<div class="card"><div class="n">{sev_counts.get('medium',0)}</div>中风险</div>
<div class="card"><div class="n">{sev_counts.get('low',0)}</div>低风险</div>
<div class="card"><div class="n">{len(verdicts.get('imageVerdicts',[]))}</div>图像问题</div>
</div>
<div class="overall"><b>整体结论：</b><br>{_html_escape(str(overall))}</div>
<h2>数据异常 + 模型判读</h2>
<table><thead><tr><th>ID</th><th>风险</th><th>类型</th><th>位置</th><th>信号说明 / 统计</th><th>模型判读</th></tr></thead>
<tbody>{''.join(rows) if rows else '<tr><td colspan=6>未检出数据异常信号。</td></tr>'}</tbody></table>
<h2>图像取证（模型视觉判读）</h2>
<table><thead><tr><th>图片</th><th>风险</th><th>问题</th><th>说明</th></tr></thead>
<tbody>{''.join(image_rows) if image_rows else '<tr><td colspan=4>未发现图像问题或未进行图像判读。</td></tr>'}</tbody></table>
<div class="footer">
本报告为投稿前科研诚信【自检】参考，不构成造假结论，亦不对应任何官方检测系统。<br>
geng-skills · 灵感来自"耿同学"的学术打假 · 开源工具
</div>
</body></html>"""
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def generate_pdf_report(data, verdicts, out_pdf):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                        TableStyle)
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return None

    # 中文字体：尝试常见 Windows 字体
    font_name = "Helvetica"
    for fp, fn in [("C:/Windows/Fonts/msyh.ttc", "MSYH"),
                   ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
                   ("C:/Windows/Fonts/simsun.ttc", "SimSun")]:
        if os.path.isfile(fp):
            try:
                pdfmetrics.registerFont(TTFont(fn, fp))
                font_name = fn
                break
            except Exception:
                continue

    styles = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=styles["Normal"], fontName=font_name, fontSize=8, leading=11)
    title = ParagraphStyle("title", parent=styles["Title"], fontName=font_name, fontSize=18)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=font_name, fontSize=12)

    doc = SimpleDocTemplate(out_pdf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=14 * mm, rightMargin=14 * mm)
    story = []
    story.append(Paragraph("geng-skills · 论文诚信自检报告", title))
    story.append(Spacer(1, 6))
    sev_counts = data.get("severityCounts", {})
    meta = (f"文档：{data.get('source','—')}　生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}<br/>"
            f"检测类型：数据完整性 + 图像取证（投稿前自检）<br/>"
            f"风险统计：高 {sev_counts.get('high',0)}　中 {sev_counts.get('medium',0)}　"
            f"低 {sev_counts.get('low',0)}　图像问题 {len(verdicts.get('imageVerdicts',[]))}")
    story.append(Paragraph(meta, base))
    story.append(Spacer(1, 8))
    overall = verdicts.get("overallAssessment", "（待模型填写整体结论）")
    story.append(Paragraph("整体结论", h2))
    story.append(Paragraph(_html_escape(str(overall)), base))
    story.append(Spacer(1, 8))

    # 数据表
    story.append(Paragraph("数据异常 + 模型判读", h2))
    vmap = {v.get("id"): v for v in verdicts.get("verdicts", [])}
    header = ["ID", "风险", "类型", "位置", "信号说明 / 统计", "模型判读"]
    table_data = [[Paragraph(h, base) for h in header]]
    findings_sorted = sorted(data.get("findings", []), key=lambda f: SEV_ORDER.get(f.get("severity"), 9))
    for fd in findings_sorted:
        v = vmap.get(fd.get("id"), {})
        stat = json.dumps(fd.get("stat", {}), ensure_ascii=False)
        cells = [
            fd.get("id", ""), SEV_LABEL.get(fd.get("severity"), ""), fd.get("type", ""),
            fd.get("location", ""),
            fd.get("explanation", "") + "<br/><font size=6 color='#666'>" + _html_escape(stat[:300]) + "</font>",
            "<b>" + _html_escape(str(v.get("verdict", "—"))) + "</b><br/>" + _html_escape(v.get("comment", "")),
        ]
        table_data.append([Paragraph(_html_escape(str(c)) if i < 4 else c, base) for i, c in enumerate(cells)])
    if len(table_data) == 1:
        table_data.append([Paragraph("未检出数据异常信号。", base)] + [Paragraph("", base)] * 5)
    t = Table(table_data, colWidths=[18 * mm, 10 * mm, 24 * mm, 30 * mm, 60 * mm, 40 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d4da")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    # 图像表
    story.append(Paragraph("图像取证（模型视觉判读）", h2))
    iheader = ["图片", "风险", "问题", "说明"]
    idata = [[Paragraph(h, base) for h in iheader]]
    for iv in verdicts.get("imageVerdicts", []):
        idata.append([Paragraph(_html_escape(str(iv.get(k, ""))), base)
                      for k in ("file",)] +
                     [Paragraph(SEV_LABEL.get(iv.get("severity", "info"), ""), base),
                      Paragraph(_html_escape(iv.get("issue", "")), base),
                      Paragraph(_html_escape(iv.get("comment", "")), base)])
    if len(idata) == 1:
        idata.append([Paragraph("未发现图像问题或未进行图像判读。", base)] + [Paragraph("", base)] * 3)
    it = Table(idata, colWidths=[40 * mm, 12 * mm, 50 * mm, 80 * mm], repeatRows=1)
    it.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d4da")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(it)
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "本报告为投稿前科研诚信【自检】参考，不构成造假结论，亦不对应任何官方检测系统。"
        "geng-skills · 灵感来自“耿同学”的学术打假 · 开源工具", base))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.drawRightString(A4[0] - 14 * mm, 10 * mm, f"第 {d.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out_pdf


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_data(args):
    full_text, tables, label = load_source(args.input)
    result = run_data_forensics(full_text, tables, label)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[data] 已分析 {result['stats']['tableCount']} 张表 / "
          f"{result['stats']['numberCountText'] + result['stats']['numberCountTables']} 个数字，"
          f"检出 {len(result['findings'])} 条信号 -> {args.out}")
    sc = result["severityCounts"]
    print(f"       风险：高 {sc.get('high',0)} / 中 {sc.get('medium',0)} / 低 {sc.get('low',0)}")


def cmd_images(args):
    manifest = run_image_extraction(args.input, args.out_dir, contact_sheet=args.contact_sheet)
    out_json = os.path.join(args.out_dir, "manifest.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[images] 抽取 {manifest['imageCount']} 张图片到 {args.out_dir}（清单 {out_json}）")
    if manifest["unresolvedCount"]:
        print(f"         有 {manifest['unresolvedCount']} 个 \\includegraphics 未能定位源文件。")
    if manifest["exactDuplicateFiles"]:
        print(f"         发现 {len(manifest['exactDuplicateFiles'])} 组完全相同的图片文件（sha1 一致）。")
    if manifest.get("contactSheet"):
        print(f"         缩略图拼版：{manifest['contactSheet']}")


def cmd_grim(args):
    res = grim_check(args.mean, args.n, args.decimals)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_sprite(args):
    res = sprite_lite(args.mean, args.sd, args.n, args.min, args.max, sample=not args.population)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_report(args):
    data, verdicts = build_report_model(args.findings, args.verdicts)
    out = args.out
    if out.lower().endswith(".pdf"):
        pdf = generate_pdf_report(data, verdicts, out)
        if pdf:
            print(f"[report] PDF 已生成 -> {pdf}")
            return
        # 降级
        html_out = os.path.splitext(out)[0] + ".html"
        generate_html_report(data, verdicts, html_out, args.template)
        print(f"[report] 未安装 reportlab，已降级生成 HTML -> {html_out}")
    else:
        generate_html_report(data, verdicts, out, args.template)
        print(f"[report] HTML 已生成 -> {out}")


def main():
    ap = argparse.ArgumentParser(description="geng-skills 论文诚信自检工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("data", help="抽取数值并运行取证统计")
    p.add_argument("--input", required=True, help=".docx / .tex / latex 目录")
    p.add_argument("--out", default="findings.json")
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("images", help="抽取图片 + 生成清单")
    p.add_argument("--input", required=True, help=".docx / .tex / latex 目录")
    p.add_argument("--out-dir", default="extracted_images")
    p.add_argument("--contact-sheet", action="store_true", help="生成缩略图拼版（需 Pillow）")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("grim", help="GRIM 均值自洽检验")
    p.add_argument("--mean", type=float, required=True)
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--decimals", type=int, required=True)
    p.set_defaults(func=cmd_grim)

    p = sub.add_parser("sprite", help="SPRITE-lite 标准差可能性检验（需取值上下界）")
    p.add_argument("--mean", type=float, required=True)
    p.add_argument("--sd", type=float, required=True)
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--min", type=float, required=True, help="取值下界（如量表最小值）")
    p.add_argument("--max", type=float, required=True, help="取值上界（如量表最大值）")
    p.add_argument("--population", action="store_true",
                   help="把 SD 当作总体标准差(除以 n)；默认按样本标准差(除以 n-1)")
    p.set_defaults(func=cmd_sprite)

    p = sub.add_parser("report", help="生成 PDF/HTML 报告")
    p.add_argument("--findings", help="data 子命令产出的 findings.json")
    p.add_argument("--verdicts", help="模型判读结果 verdicts.json")
    p.add_argument("--out", default="report.pdf")
    p.add_argument("--template", help="HTML 模板（可选）")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
