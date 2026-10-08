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
  grim    GRIM 自洽检验：给定 均值/样本量/小数位（可选题目数），判断报告均值在数学上是否可能
  sprite  SPRITE-lite：给定 均值/标准差/样本量/取值上下界，判断报告标准差在数学上是否可能
  report  合并 findings.json + 模型给出的 verdicts.json -> report.pdf（缺 reportlab 则 report.html）

用法示例：
  python geng_check.py data   --input paper.docx --out findings.json
  python geng_check.py data   --input paper.tex  --out findings.json
  python geng_check.py images --input paper.docx --out-dir extracted_images --contact-sheet
  python geng_check.py grim   --mean 3.45 --n 20 --decimals 2
  python geng_check.py sprite --mean 2.0 --sd 3.0 --n 20 --min 1 --max 7
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


def normalize_number_text(text):
    """统一 Word/LaTeX 里常见的数字写法：Unicode 负号、全角数字/小数点。"""
    return (text.replace("−", "-").replace("–", "-")
            .translate(str.maketrans("０１２３４５６７８９．", "0123456789.")))


def extract_numbers_from_text(text):
    """返回 [(float, cleaned_str)]。"""
    out = []
    for m in NUM_RE.finditer(normalize_number_text(text)):
        parsed = parse_number(m.group(0))
        if parsed is not None:
            out.append(parsed)
    return out


# 表格单元格里的 "均值 ± 标准差" / "均值 (标准差)" 写法：拆成 (均值, 标准差) 两个数。
_NUM = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)?\.?\d+(?:[eE][-+]?\d+)?"
MEAN_SD_RE = re.compile(r"^\s*(" + _NUM + r")\s*%?\s*(?:±|\+/-|\+-|\(|（)\s*(" + _NUM + r")\s*%?\s*[)）]?\s*%?\s*$")


def parse_cell_numbers(cell):
    """解析单元格：返回 (主值, 标准差或None)；不是单值/均值±SD 形式时返回 None。"""
    text = normalize_number_text(cell)
    m = MEAN_SD_RE.match(text)
    if m:
        mean, sd = parse_number(m.group(1)), parse_number(m.group(2))
        if mean is not None and sd is not None:
            return mean, sd
    nums = extract_numbers_from_text(text)
    if len(nums) == 1:
        return nums[0], None
    return None


def significant_digit_count(cleaned):
    s = re.split(r"[eE]", cleaned.lstrip("+-"))[0].replace(".", "").lstrip("0")
    return len(s)


def is_year_like(value, cleaned):
    return "." not in cleaned and 1900 <= value <= 2100


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

    def para_text(p):
        # w:t 是文字；w:tab / w:br 当作空格，避免相邻数字被粘成一个（如 "12.3" + "4.5" -> "12.34.5"）
        parts = []
        for el in p.iter():
            if el.tag == W_NS + "t":
                parts.append(el.text or "")
            elif el.tag in (W_NS + "tab", W_NS + "br", W_NS + "cr"):
                parts.append(" ")
        return "".join(parts).strip()

    def cell_text(tc):
        # 只取单元格自己的段落（嵌套表格单独作为一张表处理），段落之间用空格隔开
        return " ".join(t for t in (para_text(p) for p in tc.findall(W_NS + "p")) if t)

    def grid_span(tc):
        span = tc.find(f"{W_NS}tcPr/{W_NS}gridSpan")
        try:
            return max(1, int(span.get(W_NS + "val"))) if span is not None else 1
        except (TypeError, ValueError):
            return 1

    tables = []
    table_paragraphs = set()
    for tbl in root.iter(W_NS + "tbl"):
        table_paragraphs.update(tbl.iter(W_NS + "p"))
        rows = []
        for tr in tbl.findall(W_NS + "tr"):
            cells = []
            for tc in tr.findall(W_NS + "tc"):
                cells.append(cell_text(tc))
                # 横向合并单元格占多列：补空位，保证后面的列不错位
                cells.extend([""] * (grid_span(tc) - 1))
            rows.append(cells)
        if rows:
            tables.append(rows)

    # 正文（不含表格里的段落——表格数字已在 tables 中，避免全文统计重复计数）
    texts = []
    body = root.find(W_NS + "body")
    if body is not None:
        for p in body.iter(W_NS + "p"):
            if p in table_paragraphs:
                continue
            txt = para_text(p)
            if txt:
                texts.append(txt)
    full_text = "\n".join(texts)
    return full_text, tables


TABULAR_ENVS = r"(?:tabular\*?|tabularx|tabulary|longtable|array|tblr|longtblr|talltblr|NiceTabular\*?)"
LATEX_TABULAR_RE = re.compile(r"\\begin\{(" + TABULAR_ENVS + r")\}.*?\\end\{\1\}", re.DOTALL)
LATEX_DISPLAY_MATH_RE = re.compile(
    r"\\begin\{(equation|align|gather|multline|eqnarray|displaymath)(\*?)\}.*?\\end\{\1\2\}"
    r"|\\\[.*?\\\]|\$\$.*?\$\$", re.DOTALL)
# 只用于排版/引用、参数里的数字不是数据的命令：整条连参数删掉
LATEX_NOISE_CMD_RE = re.compile(
    r"\\(?:label|ref|eqref|autoref|cref|Cref|pageref|cite[a-zA-Z]*|includegraphics|input|include|"
    r"url|href|bibliography|bibliographystyle|graphicspath|usepackage|documentclass|setlength|"
    r"addtolength|vspace|hspace|rule|resizebox|scalebox|setcounter|addlinespace|cmidrule|cline|"
    r"arrayrulecolor|rowcolor|cellcolor|columncolor)\*?(?:\([^)]*\))?(?:\s*\[[^\]]*\])*(?:\s*\{[^{}]*\})*")


def _skip_latex_args(s, i):
    """从位置 i 起跳过连续的 [..] / {..} 参数（支持嵌套花括号），返回新位置。"""
    while i < len(s):
        j = i
        while j < len(s) and s[j] in " \t\r\n":
            j += 1
        if j < len(s) and s[j] in "[{":
            close = "]" if s[j] == "[" else "}"
            depth, k = 0, j
            while k < len(s):
                if s[k] == s[j]:
                    depth += 1
                elif s[k] == close:
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            i = k + 1
        else:
            return i
    return i


def _strip_latex(cell):
    s = cell
    # 合并单元格只保留内容，否则 \multicolumn{3}{c}{...} 里的 "3" 会被当成数据
    s = re.sub(r"\\multicolumn\s*\{[^{}]*\}\s*\{[^{}]*\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
    s = re.sub(r"\\multirow\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
    s = re.sub(r"\\textcolor\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", s)
    s = LATEX_NOISE_CMD_RE.sub(" ", s)
    s = re.sub(r"\\pm\b|\\mp\b", "±", s)
    s = re.sub(r"\\(?:textbf|textit|mathbf|mathrm|text|emph|num|SI|si|underline|makecell)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)   # 其余命令
    s = s.replace("{", " ").replace("}", " ").replace("\\", " ")
    s = s.replace("~", " ").replace("&nbsp;", " ")
    return s.strip()


def strip_latex_comments(content):
    return re.sub(r"(?<!\\)%.*", "", content)


INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")


def expand_latex_inputs(path, seen=None):
    """读取 .tex 并递归展开 \\input / \\include / \\subfile，返回 (内容, 已读取文件集合)。"""
    seen = set() if seen is None else seen
    real = os.path.realpath(path)
    if real in seen:
        return "", seen
    seen.add(real)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = strip_latex_comments(f.read())
    base = os.path.dirname(os.path.abspath(path))

    def repl(m):
        ref = m.group(1).strip()
        for cand in (ref, ref + ".tex"):
            p = os.path.join(base, cand)
            if os.path.isfile(p):
                sub, _ = expand_latex_inputs(p, seen)
                return "\n" + sub + "\n"
        return m.group(0)

    return INPUT_RE.sub(repl, content), seen


def parse_latex_tables(content):
    tables = []
    for m in LATEX_TABULAR_RE.finditer(content):
        block = m.group(0)
        # 去掉 \begin{env}[pos]{width}{colspec} 本身（列格式可能含嵌套花括号，如 {p{3cm}c}）
        head = re.match(r"\\begin\{[^}]*\}", block)
        inner = block[_skip_latex_args(block, head.end()):]
        inner = re.sub(r"\\end\{[^}]*\}\s*$", "", inner)
        rows = []
        for raw_row in re.split(r"\\\\", inner):
            raw_row = re.sub(r"^\s*\[[^\]]*\]", "", raw_row)   # \\[2pt] 的行距参数
            raw_row = re.sub(r"\\hline|\\toprule|\\midrule|\\bottomrule|\\endhead|\\endfirsthead|\\endfoot|\\endlastfoot", "", raw_row)
            raw_row = LATEX_NOISE_CMD_RE.sub(" ", raw_row)
            if not raw_row.strip():
                continue
            cells = [_strip_latex(c) for c in raw_row.split("&")]
            rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def latex_body_text(content):
    """正文文本：去掉导言区、表格、行间公式和排版命令参数，避免把 0.5\\textwidth、12pt 之类当数据。"""
    m = re.search(r"\\begin\{document\}", content)
    body = content[m.end():] if m else content
    body = LATEX_TABULAR_RE.sub(" ", body)
    body = LATEX_DISPLAY_MATH_RE.sub(" ", body)
    body = LATEX_NOISE_CMD_RE.sub(" ", body)
    body = re.sub(r"\\begin\{[^}]*\}(?:\s*\[[^\]]*\])?", " ", body)
    return body


def read_latex(path):
    content, _ = expand_latex_inputs(path)
    return latex_body_text(content), parse_latex_tables(content)


def find_latex_roots(input_dir):
    tex_files = []
    for dirpath, _, files in os.walk(input_dir):
        for fn in files:
            if fn.lower().endswith(".tex"):
                tex_files.append(os.path.join(dirpath, fn))
    roots = []
    for tf in sorted(tex_files):
        with open(tf, "r", encoding="utf-8", errors="replace") as f:
            if re.search(r"^[^%\n]*\\documentclass", f.read(), re.M):
                roots.append(tf)
    return sorted(tex_files), roots


def load_source(input_path):
    """支持 .docx / .tex / 目录。返回 (full_text, tables, source_label)。

    目录：找到含 \\documentclass 的主文件并展开其 \\input/\\include；没有主文件时读取全部 .tex。
    """
    tables = []
    texts = []
    if os.path.isdir(input_path):
        tex_files, roots = find_latex_roots(input_path)
        if len(roots) > 1:
            print(f"[warn] 目录中有 {len(roots)} 个含 \\documentclass 的主文件："
                  f"{', '.join(os.path.relpath(r, input_path) for r in roots)}。"
                  "旧版/备份稿会造成'重复数据'误报，建议直接把主 .tex 传给 --input。", file=sys.stderr)
        seen = set()
        for tf in (roots or tex_files):
            if os.path.realpath(tf) in seen:
                continue
            content, seen = expand_latex_inputs(tf, seen)
            texts.append(latex_body_text(content))
            tables.extend(parse_latex_tables(content))
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
    """从一张表抽出每列、每行的数值序列。返回 dict:
    {'columns': [...], 'sdColumns': {列号: [...]}, 'rows': [...], 'rowKeys': [...]}，
    每个序列是 [(float, cleaned_str), ...]。"均值 ± SD" 单元格的均值进 columns/rows，SD 进 sdColumns；
    rowKeys 是整行全部数字（含 SD），用于重复行比对。"""
    # 规整列数
    ncols = max((len(r) for r in table), default=0)
    columns = [[] for _ in range(ncols)]
    sd_columns = defaultdict(list)
    rows = []
    row_keys = []
    for r in table:
        row_vals = []
        row_key = []
        for ci in range(ncols):
            cell = r[ci] if ci < len(r) else ""
            parsed = parse_cell_numbers(cell)
            if parsed is None:
                continue
            main, sd = parsed
            columns[ci].append(main)
            row_vals.append(main)
            row_key.append(main[1])
            if sd is not None:
                sd_columns[ci].append(sd)
                row_key.append("±" + sd[1])
        rows.append(row_vals)
        row_keys.append(row_key)
    return {"columns": columns, "sdColumns": dict(sd_columns), "rows": rows, "rowKeys": row_keys}


def is_trivial_series(series):
    """年份表头、从 0/1 开始步长为 1 的序号列这类天然线性/重复的序列，不参与等差/重复检查。"""
    vals = [(v, c) for (v, c) in series]
    if not vals:
        return True
    if all(is_year_like(v, c) for (v, c) in vals):
        return True
    if all("." not in c for (_v, c) in vals):
        ints = [v for (v, _c) in vals]
        if ints[0] in (0, 1) and all(b - a == 1 for a, b in zip(ints, ints[1:])):
            return True
    return False


# ---------------------------------------------------------------------------
# 取证检查
# ---------------------------------------------------------------------------

def binomial_upper_tail(k, n, p):
    """P(X >= k), X ~ Binomial(n, p)。"""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def check_terminal_digits(numbers, location, min_n=30, min_n_exact=12):
    """末位数字应近似均匀（耿同学经典：2400 个数据末尾全是 5）。

    只统计至少 2 位有效数字、且不像年份的数：0.5、7 这类单个有效数字的末位本身就不均匀，
    2023 这类年份会让末位 3/4 偏多，都会造成误报。"""
    digits = [last_significant_digit(c) for (v, c) in numbers
              if significant_digit_count(c) >= 2 and not is_year_like(v, c)]
    digits = [d for d in digits if d is not None]
    if len(digits) < min_n_exact:
        return None
    counts = Counter(digits)
    observed = [counts.get(str(d), 0) for d in range(10)]
    dist = {str(d): counts.get(str(d), 0) for d in range(10)}
    top_digit, top_count = max(dist.items(), key=lambda kv: kv[1])
    if len(digits) >= min_n:
        chi2, dof, p = chi_square_test(observed, [0.1] * 10)
        method = "chi-square"
    else:
        # 样本太少，卡方近似不可靠：改用"最多的那个末位数字"的精确二项检验（×10 做 Bonferroni 校正）
        chi2, dof = None, None
        p = min(1.0, 10 * binomial_upper_tail(top_count, len(digits), 0.1))
        method = "exact-binomial(dominant digit)"
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
        "stat": {"n": len(digits), "method": method,
                  "chi2": round(chi2, 3) if chi2 is not None else None, "dof": dof, "pValue": p,
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


def check_duplicate_blocks(all_series, min_block=4, covered=None):
    """在所有数值序列里寻找重复的数据块（>=min_block 个数完全相同的子序列）。

    重叠的滑动窗口会合并成"最长重复段"，一段重复数据只报一条；
    covered 里的 (位置, 长度) 表示已被 duplicate_row 报过的整行，不再重复报。"""
    findings = []
    covered = covered or set()
    seqs = {}
    for loc, series in all_series:
        cleaned = [c for (_v, c) in series]
        if len(cleaned) >= min_block:
            seqs[loc] = cleaned
    seen = defaultdict(list)
    for loc, cleaned in seqs.items():
        for i in range(0, len(cleaned) - min_block + 1):
            block = tuple(cleaned[i:i + min_block])
            # 跳过全相同块（重复值另有检查）
            if len(set(block)) == 1:
                continue
            seen[block].append((loc, i))

    # 出现位置集合 -> 窗口；把 (loc,i)->(loc,i+1) 连续的窗口串成一段
    windows = {tuple(sorted(occ)): block for block, occ in seen.items() if len(occ) >= 2}
    for occ, block in windows.items():
        prev = tuple(sorted((loc, i - 1) for loc, i in occ))
        if prev in windows:
            continue  # 不是一段重复的起点
        run = list(block)
        cur = occ
        while True:
            nxt = tuple(sorted((loc, i + 1) for loc, i in cur))
            if nxt not in windows:
                break
            run.append(windows[nxt][-1])
            cur = nxt
        if all((loc, len(run)) in covered and i == 0 for loc, i in occ):
            continue
        findings.append({
            "type": "duplicate_data_block",
            "severity": "high",
            "location": "; ".join(sorted({loc for loc, _i in occ})),
            "stat": {"block": run, "length": len(run), "occurrences": len(occ),
                      "where": [f"{loc}@idx{i}" for loc, i in occ[:6]]},
            "explanation": "同一段数值块在多处重复出现。跨样本/跨实验条件出现相同数据块，"
                           "是数据复制粘贴的强信号，'不小心'难以解释。",
        })
    return findings


def check_duplicate_rows(tables_series, source_label):
    """跨表/表内完全相同的行。≥3 个数值全同 -> high；恰好 2 个数值全同（如消融表两行一模一样）-> medium。"""
    findings = []
    rowmap = defaultdict(list)
    for ti, ts in enumerate(tables_series):
        for ri, (row, key) in enumerate(zip(ts["rows"], ts["rowKeys"])):
            if len(row) >= 2 and len(set(key)) > 1 and not is_trivial_series(row):
                rowmap[tuple(key)].append((f"{source_label}#表{ti + 1}行{ri + 1}", len(row)))
    for key, occ in rowmap.items():
        if len(occ) >= 2:
            n_values = occ[0][1]
            findings.append({
                "type": "duplicate_row",
                "severity": "high" if n_values >= 3 else "medium",
                "location": "; ".join(loc for loc, _n in occ[:8]),
                "stat": {"row": list(key), "occurrences": len(occ)},
                "explanation": f"完全相同的数据行重复出现（{n_values} 个数值全部一致）。"
                               "不同条件/样本/配置的结果逐位相同，需确认是否同一行被复制。",
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

    # 收集"命名"序列：表的每列/每行（SD 列单独成序列）
    all_series = []
    for ti, ts in enumerate(tables_series):
        for ci, col in enumerate(ts["columns"]):
            if len(col) >= 3:
                all_series.append((f"{source_label}#表{ti + 1}列{ci + 1}", col))
        for ci, col in sorted(ts["sdColumns"].items()):
            if len(col) >= 3:
                all_series.append((f"{source_label}#表{ti + 1}列{ci + 1}(SD)", col))
        for ri, row in enumerate(ts["rows"]):
            if len(row) >= 3:
                all_series.append((f"{source_label}#表{ti + 1}行{ri + 1}", row))
    all_series = [(loc, s) for loc, s in all_series if not is_trivial_series(s)]

    # 正文数字（用于全局末位/Benford）：正文已不含表格，去掉 [12] / [3-5] 这类引用编号
    text_numbers = extract_numbers_from_text(
        re.sub(r"\[\d+(?:\s*[-–,，、]\s*\d+)*\]", " ", full_text))
    table_numbers = [pair for ts in tables_series for col in ts["columns"] for pair in col]
    table_numbers += [pair for ts in tables_series for col in ts["sdColumns"].values() for pair in col]
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

    # 重复行 / 重复数据块（已作为整行报过的不再按数据块重复报）
    dup_rows = check_duplicate_rows(tables_series, source_label)
    covered = set()
    for fd in dup_rows:
        for loc in fd["location"].split("; "):
            covered.add((loc, len([k for k in fd["stat"]["row"] if not k.startswith("±")])))
    findings.extend(dup_rows)
    findings.extend(check_duplicate_blocks(all_series, covered=covered))

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


R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def docx_media_references(z):
    """统计 document.xml 里每个 media 文件被引用（插入）了几次。

    Word 会把内容完全相同的图片只存一份 media 文件、多处共用同一个关系 ID，
    所以"同一张图在文中用了两次"在 media 目录里看不出来，只能靠引用计数发现。"""
    import xml.etree.ElementTree as ET
    names = set(z.namelist())
    if "word/_rels/document.xml.rels" not in names:
        return {}
    rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    rid_to_media = {}
    for rel in rels.iter(PKG_REL_NS + "Relationship"):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External" or "media/" not in target:
            continue
        rid_to_media[rel.get("Id")] = "word/" + target.lstrip("/").replace("word/", "", 1)
    counts = Counter()
    root = ET.fromstring(z.read("word/document.xml"))
    for el in root.iter():
        for attr in (R_NS + "embed", R_NS + "id", R_NS + "link"):
            rid = el.get(attr)
            if rid in rid_to_media and (el.tag.endswith("}blip") or el.tag.endswith("}imagedata")):
                counts[rid_to_media[rid]] += 1
    return counts


def extract_images_docx(path, out_dir):
    items = []
    with zipfile.ZipFile(path) as z:
        refs = docx_media_references(z)
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        for n in sorted(media):
            data = z.read(n)
            base = os.path.basename(n)
            out_path = os.path.join(out_dir, base)
            with open(out_path, "wb") as f:
                f.write(data)
            items.append({"file": base, "origin": n, "bytes": len(data), "sha1": sha1_of(data),
                          "references": refs.get(n, 0)})
    return items


INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^}]*\}\s*)+)\}")


def extract_images_latex(input_path, out_dir):
    if os.path.isdir(input_path):
        tex_files, roots = find_latex_roots(input_path)
        entries = roots or tex_files
        base_dirs = [input_path]
    else:
        entries = [input_path]
        base_dirs = [os.path.dirname(os.path.abspath(input_path))]

    exts = ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".tif", ".tiff", ".gif", ".bmp", ".svg"]
    by_path = {}     # 同一个源文件被多次 \includegraphics 时只抽一次，记录引用次数
    items = []
    used_names = set()
    seen_tex = set()
    for tex in entries:
        if os.path.realpath(tex) in seen_tex:
            continue
        content_nc, seen_tex = expand_latex_inputs(tex, seen_tex)
        tex_dir = os.path.dirname(os.path.abspath(tex))
        graphics_dirs = []
        for gm in GRAPHICSPATH_RE.finditer(content_nc):
            graphics_dirs += [os.path.join(tex_dir, d.strip()) for d in re.findall(r"\{([^}]*)\}", gm.group(1))]
        for m in INCLUDEGRAPHICS_RE.finditer(content_nc):
            ref = m.group(1).strip()
            # 找到附近的 caption 作为上下文
            ctx = ""
            tail = content_nc[m.end():m.end() + 400]
            cm = re.search(r"\\caption\{(.+?)\}", tail, re.DOTALL)
            if cm:
                ctx = _strip_latex(cm.group(1))[:160]
            # 解析实际文件路径（含 \graphicspath）
            resolved = None
            for d in [tex_dir] + graphics_dirs + base_dirs:
                for ext in exts:
                    cand = os.path.join(d, ref + ext)
                    if os.path.isfile(cand):
                        resolved = os.path.realpath(cand)
                        break
                if resolved:
                    break
            if resolved is None:
                items.append({"file": None, "origin": ref, "resolved": False,
                               "refContext": ctx, "bytes": 0, "sha1": None, "references": 1})
                continue
            if resolved in by_path:
                it = by_path[resolved]
                it["references"] += 1
                if ctx:
                    it.setdefault("otherContexts", []).append(ctx)
                continue
            with open(resolved, "rb") as f:
                data = f.read()
            base = os.path.basename(resolved)
            if base in used_names:
                base = f"{len(used_names)}_" + base
            used_names.add(base)
            with open(os.path.join(out_dir, base), "wb") as f:
                f.write(data)
            it = {"file": base, "origin": ref, "resolved": True,
                  "refContext": ctx, "bytes": len(data), "sha1": sha1_of(data), "references": 1}
            by_path[resolved] = it
            items.append(it)
    return items


def render_vector_previews(items, out_dir, dpi=150):
    """PDF 矢量图无法直接看图：装了 PyMuPDF 时把第一页渲染成 PNG 预览，供拼版和模型看图。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return 0
    n = 0
    for it in items:
        name = it.get("file")
        if not name or not name.lower().endswith(".pdf"):
            continue
        try:
            with fitz.open(os.path.join(out_dir, name)) as doc:
                pix = doc[0].get_pixmap(dpi=dpi)
                preview = os.path.splitext(name)[0] + ".preview.png"
                pix.save(os.path.join(out_dir, preview))
                it["preview"] = preview
                n += 1
        except Exception:
            continue
    return n


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
        p = os.path.join(out_dir, it.get("preview") or it["file"])
        try:
            with Image.open(p) as src:
                im = src.convert("RGB")
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
    previews = render_vector_previews(items, out_dir)

    # 自动检测 1：完全相同的图片文件（sha1 一致）
    byhash = defaultdict(list)
    for it in items:
        if it.get("sha1"):
            byhash[it["sha1"]].append(it["file"])
    exact_dups = [{"sha1": h, "files": fs} for h, fs in byhash.items() if len(fs) > 1]
    # 自动检测 2：同一个图片文件在文中被插入多次（Word 会把相同图片合并成一个文件，只能靠引用计数发现）
    reused = [{"file": it["file"], "references": it["references"],
               "contexts": [c for c in [it.get("refContext")] + it.get("otherContexts", []) if c]}
              for it in items if it.get("file") and it.get("references", 0) > 1]

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
        "reusedImages": reused,
        "vectorPreviews": previews,
        "contactSheet": sheet,
        "images": items,
    }
    return manifest


# ---------------------------------------------------------------------------
# GRIM
# ---------------------------------------------------------------------------

def grim_check(mean, n, decimals, items=1):
    """GRIM：报告均值 mean（小数位 decimals）在样本量 n 下是否在数学上可能。

    整数数据（或 items 道整数题的均分）的总和必须是整数，所以真实均值只能是 k/(n*items)。
    判定标准：存在某个 k 使 k/(n*items) 落在报告值的舍入区间 [mean-半个末位, mean+半个末位] 内。
    用区间判断而不是 Python 的 round()，因为 round() 是"银行家舍入"，
    会把 2.125 舍成 2.12，从而把四舍五入报告的 2.13 误判为不一致。"""
    denom = n * items
    half = 0.5 * 10 ** (-decimals)
    eps = 1e-9
    lo = math.ceil((mean - half - eps) * denom)
    hi = math.floor((mean + half + eps) * denom)
    consistent = lo <= hi
    informative = denom < 10 ** decimals
    if consistent:
        note = "均值在数学上可由整数总和得到，GRIM 通过。"
        if not informative:
            note += f"（注意：n×题数={denom} ≥ 10^{decimals}，任何均值都能通过，GRIM 在此不具判别力。）"
    else:
        note = "报告均值无法由任何整数总和在该小数位下还原，GRIM 不一致——可能是笔误或编造。"
    return {
        "mean": mean, "n": n, "items": items, "decimals": decimals,
        "consistent": consistent,
        "informative": informative,
        "impliedSum": lo if consistent else round(mean * denom),
        "note": note,
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
            f"<tr class='sev-{_html_escape(fd.get('severity',''))}'>"
            f"<td>{_html_escape(fd.get('id',''))}</td>"
            f"<td>{SEV_LABEL.get(fd.get('severity'),'')}</td>"
            f"<td>{_html_escape(fd.get('type',''))}</td>"
            f"<td>{_html_escape(fd.get('location',''))}</td>"
            f"<td>{_html_escape(fd.get('explanation',''))}<br><small>{_html_escape(json.dumps(fd.get('stat',{}), ensure_ascii=False))}</small></td>"
            f"<td><b>{_html_escape(str(verdict))}</b><br>{_html_escape(comment)}</td>"
            f"</tr>"
        )
    image_rows = []
    for iv in verdicts.get("imageVerdicts", []):
        image_rows.append(
            f"<tr class='sev-{_html_escape(iv.get('severity','info'))}'>"
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


# 中文 TrueType 字体候选（reportlab 不支持 CFF/OTF 轮廓的字体，如 Noto Sans CJK .otf、苹方）
CJK_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", "MSYH"),
    ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
    ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", "STHeiti"),
    ("/System/Library/Fonts/STHeiti Light.ttc", "STHeiti"),
    ("/Library/Fonts/Arial Unicode.ttf", "ArialUnicode"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "ArialUnicode"),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "WQYMicroHei"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "WQYZenHei"),
    ("/usr/share/fonts/wqy-microhei/wqy-microhei.ttc", "WQYMicroHei"),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "DroidSansFallback"),
]


def register_cjk_font(pdfmetrics, TTFont):
    """依次尝试 环境变量 GENG_PDF_FONT -> 系统中文 TTF -> reportlab 内置 STSong-Light（任何系统都可用）。"""
    candidates = list(CJK_FONT_CANDIDATES)
    env_font = os.environ.get("GENG_PDF_FONT")
    if env_font:
        candidates.insert(0, (env_font, "GengCustom"))
    for fp, fn in candidates:
        if os.path.isfile(fp):
            try:
                pdfmetrics.registerFont(TTFont(fn, fp))
                return fn
            except Exception:
                continue
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    except Exception:
        return "Helvetica"


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

    font_name = register_cjk_font(pdfmetrics, TTFont)

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
    meta = (f"文档：{_html_escape(data.get('source','—'))}　生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}<br/>"
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
    if manifest["reusedImages"]:
        print(f"         发现 {len(manifest['reusedImages'])} 张图片在文中被插入多次（见 manifest.json 的 reusedImages）。")
    if manifest["vectorPreviews"]:
        print(f"         已把 {manifest['vectorPreviews']} 个 PDF 矢量图渲染为 *.preview.png 供看图。")
    if manifest.get("contactSheet"):
        print(f"         缩略图拼版：{manifest['contactSheet']}")


def cmd_grim(args):
    res = grim_check(args.mean, args.n, args.decimals, items=args.items)
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
    p.add_argument("--items", type=int, default=1, help="多题量表的题目数（均分=总分/(n×题数)），默认 1")
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
