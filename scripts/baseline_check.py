# -*- coding: utf-8 -*-
"""Comparison-method (baseline) verification (v1.4).

Offline (always run by ``data``):
  - comparison tables are re-parsed with their markup (bold cells, citations in cells, †/* markers,
    caption and table notes); rows are classified as proposed / baseline / variant and every
    baseline is resolved to its reference-list entry (number, LaTeX key or author-year);
  - ``best_marked_not_best``: a bold cell that is not the best value of its column;
  - ``metric_inconsistent``: F1 outside [min(P, R), max(P, R)] or far from 2PR/(P+R), bounded
    metrics above 100 % / 1.0 or below 0, negative SD;
  - ``baseline_value_inconsistent``: the same cited baseline with different values for the same
    column in two tables;
  - ``improvement_claim_mismatch``: "improves … by 3.2 %" / "提升了 3.2 个百分点" that no pair of
    table rows reproduces;
  - a *baseline dossier* (tables, baselines, resolved references with retrieval links, dataset
    statistics claimed in the text, code/data links) for the model to work through.

Online (only with ``data --fetch-baselines``; the user must agree first):
  - each cited baseline is looked up on arXiv / OpenAlex / Crossref; open full text (arXiv HTML,
    arXiv PDF, OpenAlex open-access PDF when PyMuPDF is installed) is cached under
    ``baseline_sources/`` for the model to read;
  - the values this paper reports for the baseline are searched for in the cited work, the method
    name is searched for, the proposed method's own numbers are searched for (copied results),
    unresolvable references are reported, claimed code/data links are checked.

Everything here produces leads with statistics. The model reading SKILL.md makes the judgement;
nothing in this module is evidence of misconduct on its own.
"""

import functools
import html.parser
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import refs_check

VERSION = "1.4.0"
USER_AGENT = "geng-skills/1.4 (+https://github.com/HoneyMeta/geng-skills)"
EPS = 1e-9

# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\.\d+|\d+"
_UNIT = r"(?:%|％|[kKMGBT](?![A-Za-z])|ms|s|h|dB|pp|x|×)?"
CELL_VALUE_RE = re.compile(
    r"^\s*([-+]?(?:" + _NUM + r"))\s*(" + _UNIT + r")\s*"
    r"(?:(?:±|\+/-|\+-)\s*([-+]?(?:" + _NUM + r"))\s*%?|[(（]\s*([-+]?(?:" + _NUM + r"))\s*%?\s*[)）])?\s*(" + _UNIT + r")\s*$")
SRC_NUM_RE = re.compile(r"(?<![\w.])(" + _NUM + r")")
MARKER_CHARS = "†‡*⋆§¶"
MARKER_RE = re.compile(r"[†‡*⋆§¶]")


def norm_text(text):
    return (str(text or "").replace("−", "-").replace("–", "-").replace("—", "-").replace("\u00a0", " ")
            .translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,")))


# sentences: split at sentence punctuation and blank lines (LaTeX source wraps lines inside a sentence)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[\\])|(?<=[。！？；])|\n\s*\n")


def sentences(text):
    t = norm_text(text or "").replace("\\%", "%")
    return [re.sub(r"\s+", " ", s).strip() for s in SENTENCE_RE.split(t) if s and s.strip()]


def _decimals(raw):
    s = raw.lstrip("+-").replace(",", "")
    return len(s.split(".", 1)[1]) if "." in s else 0


def _half(decimals):
    return 0.5 * 10 ** (-decimals)


def _sig(raw):
    return len(re.sub(r"\D", "", raw.replace(",", "")).lstrip("0"))


def _round_half_up(value, decimals):
    q = Decimal(1).scaleb(-decimals)
    return Decimal(repr(value)).quantize(q, rounding=ROUND_HALF_UP)


def same_displayed(a, da, b, db):
    """Equal once the more precise number is rounded to the coarser precision (ties within rounding)."""
    d = min(da, db)
    return _round_half_up(a, d) == _round_half_up(b, d)


def cell_value(text):
    """Single-number cell -> dict(value, raw, decimals, sd, sdRaw, unit); otherwise None.

    Accepts '91.2', '91.2%', '25.6M', '91.2 ± 0.3', '91.2 (0.3)', '91.2†', '-3.1', '91.2 [12]'."""
    t = norm_text(text)
    t = MARKER_RE.sub(" ", t)
    t = re.sub(r"\[[^\]]*\]|\^\s*\{?[a-z]\}?", " ", t)
    m = CELL_VALUE_RE.match(t)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    sd_raw = (m.group(3) or m.group(4) or "").replace(",", "") or None
    sd = None
    if sd_raw is not None:
        try:
            sd = float(sd_raw)
        except ValueError:
            sd_raw = None
    return {"value": value, "raw": raw, "decimals": _decimals(raw), "sd": sd, "sdRaw": sd_raw,
            "unit": (m.group(2) or m.group(5) or "").strip()}


def source_numbers(text):
    """[(value, cleaned)] of every number in a fetched source text."""
    out = []
    for m in SRC_NUM_RE.finditer(norm_text(text)):
        cleaned = m.group(1).replace(",", "")
        try:
            out.append((float(cleaned), cleaned))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# rich tables (markup kept)
# ---------------------------------------------------------------------------

LATEX_CITE_CMD_RE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*)\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
NUMERIC_CITE_RE = re.compile(r"[\[［]\s*(\d{1,4}(?:\s*[-–—~,，、]\s*\d{1,4})*)\s*[\]］]")
AUTHOR_YEAR_RE = re.compile(
    r"\(?\s*([A-Z][A-Za-z'\-]+)(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)\s*,?\s*[\[(]?\s*((?:19|20)\d{2})[a-z]?\s*[\])]?")
LATEX_BOLD_RE = re.compile(r"\\(?:textbf|mathbf|boldsymbol|bm|best|first|bestresult)\s*\{|\\bf\b|\\bfseries\b|\\textbf\b")
LATEX_UNDERLINE_RE = re.compile(r"\\(?:underline|uline|second|secondbest)\s*\{")
LATEX_MULTICOL_RE = re.compile(r"^\s*\\multicolumn\s*\{\s*(\d+)\s*\}")
CAPTION_NUM_RE = re.compile(r"^\s*(?:(Table|TABLE|Tab\.)\s*([\dIVX]+[a-z]?)|表\s*(\d+(?:[.-]\d+)?))")


def latex_symbols(raw):
    """Turn LaTeX footnote symbols into their characters so they survive stripping."""
    s = raw
    s = re.sub(r"\\textdagger|\\dagger|\\dag\b", "†", s)
    s = re.sub(r"\\textdaggerdbl|\\ddagger|\\ddag\b", "‡", s)
    s = re.sub(r"\$\s*\^\s*\{?\s*(?:\\ast|\*|\\star)\s*\}?\s*\$|\\ast\b|\\star\b|\\textasteriskcentered", "*", s)
    s = re.sub(r"\\S\b|\\textsection", "§", s)
    return s


def _balanced_arg(s, start):
    """Content of the {...} group that begins at or after ``start``; returns (content, end) or (None, start)."""
    i = s.find("{", start)
    if i < 0:
        return None, start
    depth = 0
    for k in range(i, len(s)):
        if s[k] == "{" and (k == 0 or s[k - 1] != "\\"):
            depth += 1
        elif s[k] == "}" and s[k - 1] != "\\":
            depth -= 1
            if depth == 0:
                return s[i + 1:k], k + 1
    return None, start


def _latex_cites(raw):
    cites = []
    for m in LATEX_CITE_CMD_RE.finditer(raw):
        cites += ["key:" + k.strip() for k in m.group(1).split(",") if k.strip()]
    return cites


def _text_cites(text):
    cites = []
    for m in NUMERIC_CITE_RE.finditer(text or ""):
        cites += ["num:%d" % n for n in refs_check.expand_citation_numbers(m.group(1))]
    if not cites:
        for m in AUTHOR_YEAR_RE.finditer(text or ""):
            cites.append("ay:%s:%s" % (m.group(1), m.group(2)))
    return cites


def _clean_label(text):
    t = NUMERIC_CITE_RE.sub(" ", text or "")
    t = AUTHOR_YEAR_RE.sub(" ", t)
    t = MARKER_RE.sub(" ", t).replace("^", " ")
    t = re.sub(r"\(\s*\)|（\s*）", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ,;:")


def _mk_cell(text, raw="", bold=False, underline=False, cites=None, span=1, copy=False):
    text = norm_text(text)
    return {"text": text, "raw": raw, "bold": bold, "underline": underline, "cites": cites or [],
            "markers": sorted(set(MARKER_RE.findall(text))), "span": span, "spanCopy": copy,
            "value": None if copy else cell_value(text)}


def latex_rich_tables(content, helpers):
    """Rich tables from expanded LaTeX, in the same order as geng_check.parse_latex_tables."""
    tabular_re, skip_args, strip, noise_re = (helpers["tabular_re"], helpers["skip_args"],
                                               helpers["strip_latex"], helpers["noise_re"])
    floats = []
    number = 0
    for m in re.finditer(r"\\begin\{(table\*?|sidewaystable\*?|longtable\*?)\}(.*?)\\end\{\1\}", content, re.S):
        cap_at = m.group(2).find("\\caption")
        caption = ""
        if cap_at >= 0:
            c, _ = _balanced_arg(m.group(2), cap_at + len("\\caption"))
            caption = c or ""
            number += 1
        floats.append((m.start(), m.end(), caption, number if cap_at >= 0 else None, m.group(2)))
    tables = []
    for ti, m in enumerate(tabular_re.finditer(content)):
        block = m.group(0)
        head = re.match(r"\\begin\{[^}]*\}", block)
        inner = block[skip_args(block, head.end()):]
        inner = re.sub(r"\\end\{[^}]*\}\s*$", "", inner)
        rows = []
        for raw_row in re.split(r"\\\\", inner):
            raw_row = re.sub(r"^\s*\[[^\]]*\]", "", raw_row)
            raw_row = re.sub(r"\\hline|\\toprule|\\midrule|\\bottomrule|\\endhead|\\endfirsthead|\\endfoot|\\endlastfoot", "", raw_row)
            raw_row = re.sub(r"\\(?:cmidrule|cline)\s*(?:\([^)]*\))?\s*\{[^}]*\}", " ", raw_row)
            raw_row = re.sub(r"\\caption\s*\{.*", " ", raw_row, flags=re.S) if "\\caption" in raw_row else raw_row
            if not noise_re.sub(" ", raw_row).strip():
                continue
            cells = []
            for raw_cell in re.split(r"(?<!\\)&", raw_row):
                rc = latex_symbols(raw_cell)
                span_m = LATEX_MULTICOL_RE.match(rc)
                span = int(span_m.group(1)) if span_m else 1
                cites = _latex_cites(rc)
                text = strip(rc)
                text = re.sub(r"\s+", " ", text).strip()
                cites += _text_cites(text)
                cell = _mk_cell(text, raw=rc.strip(), bold=bool(LATEX_BOLD_RE.search(rc)),
                                underline=bool(LATEX_UNDERLINE_RE.search(rc)), cites=cites, span=span)
                cells.append(cell)
                for _ in range(span - 1):
                    cells.append(_mk_cell(text, raw="", cites=[], span=0, copy=True))
            rows.append(cells)
        caption, notes, name = "", "", None
        for start, end, cap, num, body in floats:
            if start <= m.start() < end:
                caption = re.sub(r"\s+", " ", strip(latex_symbols(cap))).strip()
                rest = re.sub(r"^\s*\[[^\]]*\]", " ", body.replace(m.group(0), " "))
                k = rest.find("\\caption")
                if k >= 0:
                    _c, end = _balanced_arg(rest, k + len("\\caption"))
                    rest = rest[:k] + " " + rest[end:]
                notes = re.sub(r"\s+", " ", strip(latex_symbols(noise_re.sub(" ", rest)))).strip()
                if num is not None:
                    name = "Table %d" % num
                break
        if rows:
            tables.append({"index": ti, "name": name, "caption": caption, "notes": notes[:600],
                           "rows": rows, "format": "latex"})
    return tables


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _docx_on(el):
    return el is not None and str(el.get(W_NS + "val", "true")).lower() not in ("0", "false", "off", "none")


def _docx_para_text(p):
    parts = []
    for el in p.iter():
        if el.tag == W_NS + "t":
            parts.append(el.text or "")
        elif el.tag in (W_NS + "tab", W_NS + "br", W_NS + "cr"):
            parts.append(" ")
    return "".join(parts).strip()


def _docx_cell(tc):
    texts, digit_runs, bold_runs, under_runs = [], 0, 0, 0
    for p in tc.findall(W_NS + "p"):
        texts.append(_docx_para_text(p))
        for r in p.iter(W_NS + "r"):
            rt = "".join(t.text or "" for t in r.iter(W_NS + "t"))
            if not re.search(r"\d", rt):
                continue
            digit_runs += 1
            rpr = r.find(W_NS + "rPr")
            if rpr is not None:
                style = rpr.find(W_NS + "rStyle")
                if _docx_on(rpr.find(W_NS + "b")) or (style is not None and "strong" in str(style.get(W_NS + "val", "")).lower()):
                    bold_runs += 1
                u = rpr.find(W_NS + "u")
                if u is not None and str(u.get(W_NS + "val", "single")).lower() != "none":
                    under_runs += 1
    text = " ".join(t for t in texts if t)
    span_el = tc.find(f"{W_NS}tcPr/{W_NS}gridSpan")
    try:
        span = max(1, int(span_el.get(W_NS + "val"))) if span_el is not None else 1
    except (TypeError, ValueError):
        span = 1
    return _mk_cell(text, raw=text, bold=digit_runs > 0 and bold_runs == digit_runs,
                    underline=digit_runs > 0 and under_runs == digit_runs, cites=_text_cites(text), span=span)


def docx_rich_tables(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    parent = {c: p for p in root.iter() for c in p}
    tables = []
    for tbl in root.iter(W_NS + "tbl"):
        rows = []
        for tr in tbl.findall(W_NS + "tr"):
            cells = []
            for tc in tr.findall(W_NS + "tc"):
                cell = _docx_cell(tc)
                cells.append(cell)
                for _ in range(cell["span"] - 1):
                    cells.append(_mk_cell(cell["text"], copy=True, span=0))
            rows.append(cells)
        caption, notes, name = "", "", None
        par = parent.get(tbl)
        if par is not None:
            sibs = list(par)
            i = sibs.index(tbl)
            before = [s for s in sibs[max(0, i - 3):i] if s.tag == W_NS + "p"][::-1]
            after = [s for s in sibs[i + 1:i + 4] if s.tag == W_NS + "p"]
            for p in before + after[:1]:
                t = _docx_para_text(p)
                if t and CAPTION_NUM_RE.match(t):
                    caption = t
                    break
            note_parts = []
            for p in after:
                t = _docx_para_text(p)
                if t and t != caption and (re.match(r"^\s*(?:Notes?|注|说明|[†‡*§])", t) or RERUN_RE.search(t) or QUOTED_RE.search(t)):
                    note_parts.append(t)
            notes = " ".join(note_parts)
        if caption:
            cm = CAPTION_NUM_RE.match(caption)
            name = ("Table " + cm.group(2)) if cm.group(2) else ("表" + cm.group(3))
        if rows:
            tables.append({"index": len(tables), "name": name, "caption": norm_text(caption), "notes": norm_text(notes)[:600],
                           "rows": rows, "format": "docx"})
    return tables


def docx_external_links(path):
    """[(url, paragraph text)] of external hyperlinks in a DOCX."""
    out = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/_rels/document.xml.rels" not in names:
            return out
        rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(PKG_REL_NS + "Relationship")
                   if r.get("TargetMode") == "External" and "hyperlink" in r.get("Type", "")}
        if not targets:
            return out
        root = ET.fromstring(z.read("word/document.xml"))
        parent = {c: p for p in root.iter() for c in p}
        for h in root.iter(W_NS + "hyperlink"):
            url = targets.get(h.get(R_NS + "id"))
            if not url:
                continue
            p = h
            while p is not None and p.tag != W_NS + "p":
                p = parent.get(p)
            out.append((url, _docx_para_text(p) if p is not None else ""))
    return out


# ---------------------------------------------------------------------------
# table semantics
# ---------------------------------------------------------------------------

LOWER_RE = re.compile(
    r"(?<![a-z])(?:err(?:or)?s?|err\.|loss|mae|mse|rmse|mape|smape|fid|kid|eer|wer|cer|latency|params?|"
    r"#\s*params?|parameters|flops|gflops|macs|time|runtime|memory|mem|size|nll|perplexity|ppl|distance|"
    r"chamfer|epe|abs\s*rel|miss\s*rate|fpr|far|frr|hter|bpd|bits/dim)(?![a-z])|"
    r"误差|损失|错误率|误识率|耗时|时间|参数量|计算量|延迟|困惑度|显存|漏检率|虚警率", re.I)
HIGHER_RE = re.compile(
    r"(?<![a-z])(?:acc(?:uracy)?|acc\.|top-?[15]|precision|prec\.?|recall|rec\.?|f1|f-?score|f-?measure|"
    r"auc|auroc|aupr|map|ap|ap50|ap75|aps|apm|apl|iou|miou|dice|bleu(?:-\d)?|rouge(?:-[l12])?|meteor|cider|"
    r"spice|psnr|ssim|ms-ssim|r2|r²|kappa|ndcg(?:@\d+)?|mrr|hits?@\d+|recall@\d+|r@\d+|success|spl|fps|"
    r"throughput|pass@\d+|win\s*rate|exact\s*match|em)(?![a-z])|"
    r"准确率|精确率|精准率|召回率|精度|正确率|识别率|查准率|查全率|f值|成功率|命中率|检出率", re.I)
BOUNDED_RE = re.compile(
    r"(?<![a-z])(?:acc(?:uracy)?|acc\.|top-?[15](?:\s*acc)?|precision|prec\.?|recall|rec\.?|f1|f-?score|"
    r"f-?measure|auc|auroc|aupr|map|ap|ap50|ap75|iou|miou|dice)(?![a-z])|"
    r"准确率|精确率|精准率|召回率|正确率|识别率|查准率|查全率|f值", re.I)
COST_RE = re.compile(
    r"(?<![a-z])(?:params?|#\s*params?|parameters|flops|gflops|macs|memory|mem|time|runtime|latency|fps|"
    r"throughput|size|speed|epochs?|batch|gpu\s*hours?)(?![a-z])|参数量|计算量|耗时|时间|显存|速度|延迟", re.I)
ACC_ERR_RE = re.compile(r"(?<![a-z])(?:acc(?:uracy)?|acc\.|top-?[15]|err(?:or)?s?|err\.)(?![a-z])|准确率|正确率|错误率|识别率", re.I)
MACRO_RE = re.compile(r"macro|weighted|宏平均|加权", re.I)
MICRO_RE = re.compile(r"micro|binary|微平均|二分类", re.I)

PROPOSED_LABEL_RE = re.compile(
    r"(?<![a-z])ours?(?![a-z])|\bproposed\b|\bour\s+(?:method|approach|model|framework|system|network|net)\b|"
    r"本文|所提|我们的|本方法|提出的方法|本研究|本章", re.I)
VARIANT_RE = re.compile(r"^\s*(?:w/o|w/|without\b|with\b|\+|-|no\s|only\s|去掉|去除|移除|仅|不含|无)", re.I)
RERUN_RE = re.compile(
    r"re-?produc(?:ed|e|tion)|re-?implement(?:ed|ation)?|our\s+(?:own\s+)?(?:implementation|re-?implementation|reproduction)|"
    r"implemented\s+by\s+(?:us|ourselves)|(?:re-?)?run\s+by\s+(?:us|ourselves)|re-?trained|re-?ran\b|\bre-?run\b|retrain|"
    r"(?:using|with)\s+(?:the|their)\s+(?:official|released|public(?:ly available)?)\s+(?:code|implementation)|"
    r"复现|重新训练|重新实现|重新运行|重新测试|我们实现|自行实现|自行训练|官方代码", re.I)
QUOTED_RE = re.compile(
    r"results?\s+(?:are\s+|were\s+)?(?:taken|quoted|copied|borrowed|reported)\s+(?:directly\s+)?from|as\s+reported\s+in|"
    r"reported\s+(?:in|by)\s+(?:the\s+)?original|from\s+(?:the\s+)?original\s+(?:paper|work|publication)s?|"
    r"numbers?\s+(?:are\s+)?from|引自|来自原文|取自|摘自|原文报告|原论文", re.I)
SELF_RE = re.compile(r"\b(?:our|ours|we|proposed|this\s+(?:paper|work|method|approach))\b|本文|我们|所提|本方法", re.I)
PROPOSE_WORD_RE = re.compile(r"\b(?:propose[sd]?|present(?:s|ed)?|introduce[sd]?|develop(?:s|ed)?)\b|提出|设计了|构建了", re.I)
NAME_TOKEN = r"([A-Z][A-Za-z0-9]*(?:[-‐][A-Za-z0-9]+)*)"
PROPOSE_NAME_RES = [
    re.compile(r"\b(?:[Ww]e|[Tt]his\s+(?:paper|work|study))\s+(?:propose|present|introduce|develop|design)s?\s+(?:(?:a|an|the)\s+)?"
               r"(?:(?:novel|new|simple|efficient|unified|general)\s+)*" + NAME_TOKEN),
    re.compile(r"\b(?:named|dubbed|termed|called|coined)\s+(?:as\s+)?[\"“'`]*" + NAME_TOKEN),
    re.compile(r"(?:称为|命名为|简称|记为|名为)\s*[\"“「]?\s*" + NAME_TOKEN),
    re.compile(r"[(（]\s*" + NAME_TOKEN + r"\s*[)）]"),
]
NAME_STOP = {"we", "the", "this", "our", "in", "a", "an", "it", "table", "fig", "figure", "section", "eq", "and", "or",
             "cnn", "dnn", "rnn", "ai", "ml", "dl", "nlp", "cv", "sota", "i", "ii", "iii", "iv"}


def _norm_name(label):
    return re.sub(r"\s+", "", _clean_label(label).lower()).replace("‐", "-")


def proposed_names(text):
    names = []
    for sentence in sentences(text):
        if not PROPOSE_WORD_RE.search(sentence) or not SELF_RE.search(sentence):
            continue
        for rx in PROPOSE_NAME_RES:
            for m in rx.finditer(sentence):
                tok = m.group(1).strip("-")
                if (2 <= len(tok) <= 30 and tok.lower() not in NAME_STOP
                        and (re.search(r"[A-Z]", tok[1:]) or re.search(r"\d", tok)) and tok not in names):
                    names.append(tok)
    return names


def _direction(text):
    t = norm_text(text)
    if "↑" in t or "\\uparrow" in t or "higher is better" in t.lower() or "越高越好" in t:
        return "higher"
    if "↓" in t or "\\downarrow" in t or "lower is better" in t.lower() or "越低越好" in t:
        return "lower"
    if LOWER_RE.search(t):
        return "lower"
    if HIGHER_RE.search(t):
        return "higher"
    return None


def _metric_part(name):
    return name.split(" / ")[-1]


def _metric_kind(part):
    n = re.sub(r"\(.*?\)|（.*?）|[%↑↓$\\]|uparrow|downarrow", " ", part.lower())
    n = re.sub(r"^\s*(?:macro|micro|weighted|binary|avg\.?|mean)[\s\-_]*", "", n.strip())
    n = re.sub(r"\s+", "", n).rstrip(".:")
    if n in ("p", "pre", "prec", "precision", "精确率", "查准率", "精准率", "精度p"):
        return "P"
    if n in ("r", "rec", "recall", "召回率", "查全率"):
        return "R"
    if n in ("f", "f1", "f1-score", "f1score", "fscore", "f-score", "f-measure", "fmeasure", "f1值", "f值", "f1-measure"):
        return "F"
    return None


def analyze_table(t, names):
    """Adds header/columns/data rows/roles to a rich table dict (in place) and returns it."""
    rows = t["rows"]
    ncols = max((len(r) for r in rows), default=0)

    def is_data(r):
        if not r or r[0]["value"] is not None or not re.search(r"[A-Za-z\u4e00-\u9fff]", r[0]["text"] or ""):
            return False
        return any(c["value"] is not None for c in r[1:])

    data_idx = [i for i, r in enumerate(rows) if is_data(r)]
    first = data_idx[0] if data_idx else len(rows)
    header_idx = list(range(max(0, first - 4), first))
    columns = []
    for c in range(ncols):
        parts = []
        for hi in header_idx:
            r = rows[hi]
            if c < len(r) and r[c]["text"] and (not parts or parts[-1] != r[c]["text"]):
                parts.append(r[c]["text"])
        raw_hdr = " ".join(rows[hi][c]["raw"] for hi in header_idx if c < len(rows[hi]))
        name = " / ".join(parts) or ("列%d" % (c + 1))
        direction = _direction(raw_hdr + " " + name)
        columns.append({"index": c, "name": name, "direction": direction,
                        "cost": bool(COST_RE.search(name)), "bounded": bool(BOUNDED_RE.search(name)),
                        "accErr": bool(ACC_ERR_RE.search(name)), "kind": _metric_kind(_metric_part(name))})
    # caption fallback for direction when headers are dataset names
    cap_dir = _direction(t.get("caption", ""))
    for col in columns:
        if col["direction"] is None and col["index"] > 0 and cap_dir:
            col["direction"] = cap_dir
            col["directionFrom"] = "caption"
        if not col["bounded"] and col["index"] > 0 and BOUNDED_RE.search(t.get("caption", "")) and not col["cost"] and not LOWER_RE.search(col["name"]):
            col["boundedFrom"] = "caption"
    # groups: text-only rows between data rows
    group = 0
    groups = {}
    for i in range(first, len(rows)):
        if i in data_idx:
            groups[i] = group
        elif rows[i] and rows[i][0]["text"] and not any(c["value"] is not None for c in rows[i]):
            group += 1
    lower_names = [n.lower() for n in names]
    roles = {}
    for i in data_idx:
        label = _clean_label(rows[i][0]["text"])
        ln = label.lower().replace(" ", "")
        if PROPOSED_LABEL_RE.search(rows[i][0]["text"]) or any(ln.startswith(n.lower()) for n in lower_names):
            roles[i] = "proposed"
        elif VARIANT_RE.match(label):
            roles[i] = "variant"
        else:
            roles[i] = "baseline"
    t.update({"ncols": ncols, "headerRows": header_idx, "dataRows": data_idx, "columns": columns,
              "groups": groups, "roles": roles, "hasGroups": group > 0})
    t["isComparison"] = len(data_idx) >= 3 and any(
        sum(1 for i in data_idx if c < len(rows[i]) and rows[i][c]["value"] is not None) >= 2 for c in range(1, ncols)) and (
        "proposed" in roles.values() or any(rows[i][0]["cites"] or any(c["cites"] for c in rows[i]) for i in data_idx))
    t["isCandidate"] = len(data_idx) >= 3
    return t


def table_name(t):
    return t.get("name") or ("表%d" % (t["index"] + 1))


def _loc(source_label, t, ri=None, ci=None, extra=""):
    s = "%s#表%d" % (source_label, t["index"] + 1)
    if t.get("name"):
        s += "(%s)" % t["name"]
    if ri is not None:
        s += " 行%d" % (ri + 1)
    if ci is not None:
        s += " 列%d" % (ci + 1)
    return s + extra


def _val(t, ri, ci):
    r = t["rows"][ri]
    return r[ci]["value"] if ci < len(r) else None


# ---------------------------------------------------------------------------
# offline checks
# ---------------------------------------------------------------------------


def _better(a, b, direction):
    return a > b if direction == "higher" else a < b


def check_best_marked(t, source_label):
    findings = []
    rows = t["rows"]
    for col in t["columns"][1:]:
        c = col["index"]
        cells = [(i, rows[i][c]) for i in t["dataRows"] if c < len(rows[i]) and rows[i][c]["value"] is not None]
        if len(cells) < 3:
            continue
        bold = [(i, cell) for i, cell in cells if cell["bold"]]
        if not bold or len(bold) > len(cells) / 2:
            continue
        d = col["direction"]
        for i, cell in bold:
            v, dv = cell["value"]["value"], cell["value"]["decimals"]
            scope = cells
            if len(bold) >= 2 and t.get("hasGroups"):
                scope = [(j, x) for j, x in cells if t["groups"].get(j) == t["groups"].get(i)]
            beaters = []
            for j, other in scope:
                if j == i:
                    continue
                ov, od = other["value"]["value"], other["value"]["decimals"]
                if same_displayed(v, dv, ov, od):
                    continue
                if d:
                    if _better(ov, v, d):
                        beaters.append((j, other))
            if d is None:
                vals = [x["value"]["value"] for _j, x in scope]
                top, bottom = max(vals), min(vals)
                is_extreme = (same_displayed(v, dv, top, dv) or same_displayed(v, dv, bottom, dv))
                if is_extreme:
                    continue
                beaters = [(j, x) for j, x in scope if x["value"]["value"] in (top, bottom)]
            if not beaters:
                continue
            numeric_in_row = [x for x in rows[i][1:] if x["value"] is not None]
            whole_row = len(numeric_in_row) >= 2 and all(x["bold"] for x in numeric_in_row)
            best_j, best_cell = (max if d != "lower" else min)(beaters, key=lambda jx: jx[1]["value"]["value"])
            findings.append({
                "type": "best_marked_not_best",
                "severity": "low" if whole_row else "medium",
                "location": _loc(source_label, t, i, c, ": " + _clean_label(rows[i][0]["text"])),
                "stat": {"table": table_name(t), "column": col["name"], "direction": d or "unknown",
                         "bold": cell["value"]["raw"], "boldRow": _clean_label(rows[i][0]["text"]),
                         "better": best_cell["value"]["raw"], "betterRow": _clean_label(rows[best_j][0]["text"]),
                         "wholeRowBold": whole_row},
                "explanation": (
                    "该列加粗（通常表示最优）的数值不是本列最好的结果："
                    + ("按表头判断此列%s；" % ("越高越好" if d == "higher" else "越低越好") if d else "表头看不出指标方向，加粗值既非最大也非最小；")
                    + "行「%s」的 %s 优于加粗的 %s。可能是排版疏忽，也可能让读者误以为本文方法最优。"
                    % (_clean_label(rows[best_j][0]["text"]), best_cell["value"]["raw"], cell["value"]["raw"])
                    + ("（该行整行加粗，可能只是标记本文方法的排版习惯。）" if whole_row else "")),
            })
    return findings


def _hm(p, r):
    return 2 * p * r / (p + r) if p + r > 0 else 0.0


def check_metric_consistency(t, source_label):
    findings = []
    rows = t["rows"]
    context = " ".join([t.get("caption", ""), t.get("notes", "")] + [c["name"] for c in t["columns"]])
    for col in t["columns"][1:]:
        c = col["index"]
        if not (col["bounded"] or col.get("boundedFrom")):
            continue
        vals = [(i, rows[i][c]["value"]) for i in t["dataRows"] if c < len(rows[i]) and rows[i][c]["value"]]
        if not vals:
            continue
        nums = [v["value"] for _i, v in vals]
        frac_share = sum(1 for x in nums if 0 <= x <= 1) / len(nums)
        for i, v in vals:
            x = v["value"]
            problem = None
            if x < 0:
                problem = "负值"
            elif x > 100 + EPS:
                problem = "超过 100%"
            elif frac_share >= 2 / 3 and len(nums) >= 3 and 1 < x <= 100:
                problem = "本列其他值都是 0–1 小数，这个值却大于 1"
            if problem:
                findings.append({
                    "type": "metric_inconsistent", "severity": "medium",
                    "location": _loc(source_label, t, i, c, ": " + _clean_label(rows[i][0]["text"])),
                    "stat": {"table": table_name(t), "column": col["name"], "value": v["raw"], "check": "bounded", "problem": problem},
                    "explanation": "「%s」是有界指标（0–100%% 或 0–1），表中值 %s %s，数学上不可能或量纲写错。" % (col["name"], v["raw"], problem),
                })
    for i in t["dataRows"]:
        for cell in rows[i][1:]:
            v = cell["value"]
            if v and v["sd"] is not None and v["sd"] < 0:
                findings.append({
                    "type": "metric_inconsistent", "severity": "medium",
                    "location": _loc(source_label, t, i, None, ": " + _clean_label(rows[i][0]["text"])),
                    "stat": {"table": table_name(t), "cell": cell["text"], "check": "negative_sd"},
                    "explanation": "标准差为负数，数学上不可能，请核对。",
                })
    # P / R / F1 triples per dataset group
    groups = {}
    for col in t["columns"][1:]:
        if col["kind"]:
            key = " / ".join(col["name"].split(" / ")[:-1])
            groups.setdefault(key, {}).setdefault(col["kind"], []).append(col)
    for key, kinds in groups.items():
        if not all(len(kinds.get(k, [])) == 1 for k in "PRF"):
            continue
        cp, cr, cf = kinds["P"][0], kinds["R"][0], kinds["F"][0]
        avg_text = " ".join([context, cp["name"], cr["name"], cf["name"]])
        averaging = "macro" if MACRO_RE.search(avg_text) else ("micro" if MICRO_RE.search(avg_text) else None)
        for i in t["dataRows"]:
            vp, vr, vf = (_val(t, i, cp["index"]), _val(t, i, cr["index"]), _val(t, i, cf["index"]))
            if not (vp and vr and vf):
                continue
            P, R, F = vp["value"], vr["value"], vf["value"]
            scales = {x <= 1 for x in (P, R, F)}
            if len(scales) > 1:
                continue
            hp, hr, hf = _half(vp["decimals"]), _half(vr["decimals"]), _half(vf["decimals"])
            tol = hp + hr + hf + EPS
            label = _clean_label(rows[i][0]["text"])
            stat = {"table": table_name(t), "row": label, "P": vp["raw"], "R": vr["raw"], "F1": vf["raw"],
                    "averaging": averaging or "unspecified", "harmonicMean": round(_hm(P, R), 4)}
            sev, why = None, ""
            if F > max(P, R) + tol:
                sev, why = "medium", "F1 大于 P 和 R 中的较大者——无论 micro、macro 还是加权平均都不可能"
            elif F < min(P, R) - tol and averaging != "macro":
                sev = "medium" if averaging == "micro" else "low"
                why = "F1 小于 P 和 R 中的较小者（micro/二分类 F1 是 P、R 的调和平均，必在两者之间；若是 macro 平均则可能出现）"
            elif averaging != "macro" and not (vp["sd"] or vr["sd"] or vf["sd"]):
                lo, hi = _hm(P - hp, R - hr), _hm(P + hp, R + hr)
                if F + hf < lo - EPS or F - hf > hi + EPS:
                    sev, why = "low", "F1 与 2PR/(P+R)=%.4g 相差超过四舍五入范围（若是 macro 平均或多次运行取均值可解释）" % _hm(P, R)
            if sev:
                findings.append({
                    "type": "metric_inconsistent", "severity": sev,
                    "location": _loc(source_label, t, i, None, ": " + label),
                    "stat": {**stat, "check": "f1"},
                    "explanation": "精确率、召回率与 F1 对不上：" + why + "。请核对是否抄错列或平均方式。",
                })
    return findings


def _is_generic_header(name):
    rest = re.sub(LOWER_RE.pattern + "|" + HIGHER_RE.pattern, " ", name, flags=re.I)
    rest = re.sub(r"[\W_\d]+|列", "", rest)
    return not rest


def check_cross_table_values(tables, source_label):
    findings = []
    seen = {}
    for t in tables:
        if not t.get("isComparison"):
            continue
        for i in t["dataRows"]:
            if t["roles"].get(i) != "baseline":
                continue
            cites = tuple(sorted(set(_row_cites(t, i))))
            if not cites:
                continue
            name = _norm_name(t["rows"][i][0]["text"])
            for col in t["columns"][1:]:
                v = _val(t, i, col["index"])
                if not v or _is_generic_header(col["name"]):
                    continue
                key = (name, cites, re.sub(r"[\s%()（）↑↓$\\]|uparrow|downarrow", "", col["name"].lower()))
                if key in seen:
                    t0, i0, v0 = seen[key]
                    if t0 is t:
                        continue
                    a, b = v0["value"], v["value"]
                    if same_displayed(a, v0["decimals"], b, v["decimals"]) or any(
                            same_displayed(a * k, v0["decimals"], b, v["decimals"]) for k in (100, 0.01)):
                        continue
                    findings.append({
                        "type": "baseline_value_inconsistent", "severity": "medium",
                        "location": "%s; %s" % (_loc(source_label, t0, i0), _loc(source_label, t, i)),
                        "stat": {"method": _clean_label(t["rows"][i][0]["text"]), "cites": list(cites), "column": col["name"],
                                 "values": [{"table": table_name(t0), "value": v0["raw"]}, {"table": table_name(t), "value": v["raw"]}]},
                        "explanation": "同一个被引对比方法（同名、同引用）在两张表的同一列给出了不同数值。"
                                       "如果两张表的实验设置不同，应在表注说明；否则至少有一处抄错。",
                    })
                else:
                    seen[key] = (t, i, v)
    return findings


CLAIM_UNIT = r"(?P<unit>%|％|percentage\s+points?|percent(?:age)?(?:\s+points?)?|points?|pts?\.?|pp\b|p\.p\.|个百分点|百分点|个点)"
CLAIM_NUM = r"(?P<num>\d+(?:\.\d+)?)"
EN_VERB = r"(?:improv|outperform|surpass|exceed|beat|boost|increas|reduc|decreas|lower|rais|gain|advanc|lead|better|cut|drop|enhanc)"
CLAIM_RES = [
    re.compile(r"\b" + EN_VERB + r"[a-z]*\b[^.;:!?]{0,100}?\bby\s+(?:about\s+|around\s+|approximately\s+|nearly\s+|almost\s+|roughly\s+|"
               r"up\s+to\s+|over\s+|more\s+than\s+|~\s*|an?\s+(?:large\s+|significant\s+)?margin\s+of\s+)?" + CLAIM_NUM + r"\s*" + CLAIM_UNIT, re.I),
    re.compile(r"(?<![\d.])" + CLAIM_NUM + r"\s*" + CLAIM_UNIT + r"\s+(?:(?:absolute|relative|error|accuracy|top-[15]|map|ap|f1|bleu|"
               r"performance|average|mean|overall|miou|auc|wer|cer|eer)\s+){0,2}(?:improvement|gain|increase|boost|reduction|"
               r"decrease|drop|margin|higher|better|lower|fewer|less|more)", re.I),
    re.compile(r"\b(?:improvement|gain|increase|boost|reduction|decrease|margin)\s+of\s+(?:about\s+|around\s+|up\s+to\s+|over\s+|nearly\s+)?"
               + CLAIM_NUM + r"\s*" + CLAIM_UNIT, re.I),
    re.compile(r"(?:提升|提高|改善|改进|增加|增长|超过|超出|优于|领先|降低|减少|下降|减小|高出|高于|低于)了?\s*(?:约|近|达|高达|超过|分别为?|大约|将近)?\s*"
               + CLAIM_NUM + r"\s*" + CLAIM_UNIT),
    re.compile(r"(?<![\d.])" + CLAIM_NUM + r"\s*" + CLAIM_UNIT + r"\s*的?(?:提升|提高|增益|改进|改善|降低|下降)"),
]


def extract_improvement_claims(text, names):
    claims = []
    seen = set()
    name_re = re.compile("|".join(re.escape(n) for n in names), re.I) if names else None
    for sentence in sentences(text):
        if not (SELF_RE.search(sentence) or (name_re and name_re.search(sentence))):
            continue
        for rx in CLAIM_RES:
            for m in rx.finditer(sentence):
                unit = m.group("unit")
                key = (sentence, m.group("num"))
                if key in seen:
                    continue
                seen.add(key)
                if re.search(r"point|pts|pp|p\.p\.|百分点|个点", unit, re.I):
                    kinds = {"abs"}
                elif re.search(r"\brelative(?:ly)?\b|相对", sentence, re.I):
                    kinds = {"rel"}
                elif re.search(r"\babsolute\b|绝对", sentence, re.I):
                    kinds = {"abs"}
                else:
                    kinds = {"abs", "rel"}
                claims.append({"sentence": re.sub(r"\s+", " ", sentence).strip()[:300], "claimed": m.group("num"),
                               "unit": unit, "kinds": sorted(kinds)})
    return claims


def improvement_candidates(tables):
    """Every absolute / relative difference between a proposed row and another row, per column and averaged."""
    out = []
    for t in tables:
        if not t.get("isCandidate"):
            continue
        rows = t["rows"]
        props = [i for i in t["dataRows"] if t["roles"].get(i) == "proposed"]
        for p in props:
            for o in t["dataRows"]:
                if o == p:
                    continue
                per_col = []
                for col in t["columns"][1:]:
                    vp, vo = _val(t, p, col["index"]), _val(t, o, col["index"])
                    if not (vp and vo):
                        continue
                    a, b = vp["value"], vo["value"]
                    tol = _half(vp["decimals"]) + _half(vo["decimals"])
                    diff = abs(a - b)
                    desc = "%s: %s vs %s, %s" % (table_name(t), _clean_label(rows[p][0]["text"]), _clean_label(rows[o][0]["text"]), col["name"])
                    out.append(("abs", diff, tol, desc + " (差值)"))
                    if a <= 1 and b <= 1:
                        out.append(("abs", diff * 100, tol * 100, desc + " (差值×100)"))
                    for base, name in ((b, "相对对比方法"), (a, "相对本文")):
                        if abs(base) > EPS:
                            lo = max(0.0, diff - tol) / (abs(base) + tol)
                            hi = (diff + tol) / max(EPS, abs(base) - tol)
                            mid = diff / abs(base)
                            out.append(("rel", mid * 100, max(mid - lo, hi - mid) * 100, desc + " (%s的相对变化)" % name))
                    per_col.append((diff * (100 if a <= 1 and b <= 1 else 1), tol * (100 if a <= 1 and b <= 1 else 1)))
                if len(per_col) >= 2:
                    mean = sum(d for d, _t in per_col) / len(per_col)
                    mtol = sum(tt for _d, tt in per_col) / len(per_col)
                    out.append(("abs", mean, mtol, "%s: %s vs %s, 各列平均差值" % (table_name(t), _clean_label(rows[p][0]["text"]),
                                                                            _clean_label(rows[o][0]["text"]))))
    return out


def check_improvement_claims(text, tables, names, source_label):
    if not any(t.get("isCandidate") and "proposed" in t["roles"].values() for t in tables):
        return []
    candidates = improvement_candidates(tables)
    if not candidates:
        return []
    findings = []
    for claim in extract_improvement_claims(text, names):
        c = float(claim["claimed"])
        h = _half(_decimals(claim["claimed"])) + EPS
        usable = [x for x in candidates if x[0] in claim["kinds"]]
        if not usable:
            continue
        if any(abs(val - c) <= h + tol for kind, val, tol, desc in usable):
            continue
        closest = sorted(usable, key=lambda x: abs(x[1] - c))[:5]
        findings.append({
            "type": "improvement_claim_mismatch", "severity": "low",
            "location": "%s#正文: %s" % (source_label, claim["sentence"][:90]),
            "stat": {"claimed": claim["claimed"], "unit": claim["unit"], "interpretations": claim["kinds"],
                     "sentence": claim["sentence"],
                     "closest": [{"value": round(v, 3), "kind": k, "between": d} for k, v, _t, d in closest]},
            "explanation": ("正文声称的提升幅度 %s%s 在对比表里找不到出处：本文方法行与其他任何行、任何列的%s都对不上（已允许四舍五入）。"
                            "可能是提升幅度算错、引用的是旧版结果、或数字来自图/其他实验。请模型核对这句话具体指哪两个数。")
                           % (claim["claimed"], claim["unit"], "绝对差值或相对变化" if len(claim["kinds"]) == 2 else
                              ("绝对差值" if claim["kinds"] == ["abs"] else "相对变化")),
        })
    return findings


# ---------------------------------------------------------------------------
# references, dataset claims, links
# ---------------------------------------------------------------------------

ARXIV_ID_RE = re.compile(r"(?:arxiv\s*[:/]?\s*|arxiv\.org/(?:abs|pdf|html)/|10\.48550/arxiv\.)(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def title_guess(text):
    t = re.sub(r"^\s*(?:\[\d+\]|［\d+］|\d+[.、]\s)", "", text or "").strip()
    q = re.search(r"[\"“]([^\"”]{12,300})[\"”]", t)
    if q:
        return q.group(1).strip(" ,.")
    zh = re.search(r"([^.．]{4,200})\s*\[(?:J|C|M|D|R|S|P|EB/OL|Z|A|N)\]", t)
    if zh:
        return zh.group(1).strip()
    segs = []
    for piece in re.split(r"\.\s+(?=[A-Z0-9\u4e00-\u9fff])", t):
        # "K. He, X. Zhang" -> keep author initials together with the author list, but not
        # "He K, Sun J. Deep residual learning" (Vancouver style ends the author list with an initial)
        continues_authors = (len(piece.split()) <= 2 or re.match(r"^[\w'\-]+(?:,|\s+and\b|\s+et\s+al\b)", piece)
                             or re.match(r"^(?:van|von|de|der|da|di|du|le|la)\b", piece))
        if segs and ((re.search(r"(?:^|[\s,(])[A-Z]$", segs[-1]) and continues_authors)
                     or re.search(r"\b(?:Jr|Dr|vs|Proc|Vol|No)$", segs[-1])):
            segs[-1] += ". " + piece
        else:
            segs.append(piece)
    segs = [s.strip() for s in segs if s.strip()]
    best = ""
    for s in segs[:4]:
        words = s.split()
        initials = len(re.findall(r"\b[A-Z]\.", s))
        commas = s.count(",")
        looks_authors = (initials >= 2) or (commas >= 2 and len(words) <= commas * 4) or re.search(r"\bet al\b", s)
        if looks_authors or YEAR_RE.fullmatch(s.strip(" ()")) or len(words) < 3:
            continue
        if len(s) > len(best) and not re.match(r"^(?:In|Proc|Proceedings|arXiv|IEEE|ACM)\b", s):
            best = s
            break
    return best.strip(" ,.")


def reference_index(refinfo):
    """dict cite-id -> entry dict (key:/num:) plus the list of entries."""
    entries = refinfo.get("entries") or []
    index = {}
    bibitems = [e for e in entries if e.get("source") == "bibitem"]
    for i, e in enumerate(entries):
        if e.get("key"):
            index["key:" + e["key"]] = e
        if refinfo.get("kind") != "latex":
            index["num:%d" % refs_check.entry_number(e.get("text", ""), i + 1)] = e
    for i, e in enumerate(bibitems):
        index.setdefault("num:%d" % (i + 1), e)
    return index


def resolve_cite(cite, index, entries):
    if cite in index:
        return index[cite]
    if cite.startswith("ay:"):
        _, surname, year = cite.split(":", 2)
        hits = [e for e in entries if surname.lower() in e.get("text", "").lower() and year in e.get("text", "")]
        if len(hits) == 1:
            return hits[0]
    return None


def ref_record(cite, entry):
    text = entry.get("text", "")
    blob = " ".join([text, entry.get("doi", ""), entry.get("eprint", ""), entry.get("url", "")])
    am = ARXIV_ID_RE.search(blob)
    if not am and re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", entry.get("eprint", "").strip()):
        am = re.match(r"(\d{4}\.\d{4,5})(v\d+)?", entry["eprint"].strip())
    arxiv = am.group(1) if am else ""
    doi = (entry.get("doi") or "").strip().rstrip(".")
    if doi.lower().startswith("10.48550/arxiv."):
        arxiv = arxiv or doi.split(".", 2)[-1]
    years = YEAR_RE.findall(text)
    title = entry.get("title") or title_guess(text)
    hints = {}
    if doi:
        hints["doi"] = "https://doi.org/" + doi
    if arxiv:
        hints["arxiv"] = "https://arxiv.org/abs/" + arxiv
    if title:
        q = urllib.parse.quote(title[:250])
        hints["openalexSearch"] = "https://api.openalex.org/works?search=" + q
        hints["semanticScholarSearch"] = "https://www.semanticscholar.org/search?q=" + q
        hints["googleScholarSearch"] = "https://scholar.google.com/scholar?q=" + q
    return {"refId": cite, "key": entry.get("key", ""), "text": text[:500], "doi": doi, "arxivId": arxiv,
            "year": int(years[-1]) if years else None, "titleGuess": title[:300], "retrieval": hints}


def _row_cites(t, i):
    return [c for cell in t["rows"][i] for c in cell["cites"]]


def find_text_cites(label, raw_body, kind):
    core = re.sub(r"\([^)]*\)|（[^）]*）", " ", _clean_label(label)).strip()
    if len(core) < 2 or not raw_body:
        return []
    pat = re.escape(core).replace("\\ ", r"\s+")
    if kind == "latex":
        rx = re.compile(r"(?<![A-Za-z0-9])" + pat + r"\s*(?:\([^)]{0,40}\))?\s*~?\s*\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}", re.I)
        for m in rx.finditer(raw_body):
            return ["key:" + k.strip() for k in m.group(1).split(",") if k.strip()]
    else:
        rx = re.compile(r"(?<![A-Za-z0-9])" + pat + r"\s*(?:\([^)]{0,40}\))?\s*[\[［]\s*(\d[\d,，、\s\-–]*)\s*[\]］]", re.I)
        for m in rx.finditer(raw_body):
            return ["num:%d" % n for n in refs_check.expand_citation_numbers(m.group(1))]
    return []


DATASET_WORD_RE = re.compile(r"\b(?:datasets?|benchmarks?|corpus|corpora|database|collection)\b|数据集|语料|数据库", re.I)
KNOWN_DATASET_RE = re.compile(
    r"\b(?:CIFAR-?10{1,2}|ImageNet(?:-\w+)?|MNIST|Fashion-MNIST|SVHN|STL-?10|COCO|MS[- ]?COCO|PASCAL\s*VOC|VOC\s*20\d\d|Cityscapes|ADE20K|"
    r"KITTI|LibriSpeech|TIMIT|VoxCeleb\d?|SQuAD|GLUE|SuperGLUE|WMT\s*\d+|Penn\s*Treebank|WikiText-?\d+|CelebA|LSUN|Omniglot|"
    r"miniImageNet|tieredImageNet|CUB-?200(?:-2011)?|Kinetics(?:-\d+)?|UCF-?101|HMDB-?51|ShipsEar|DeepShip|MIMIC-I{2,3}|UCI)\b", re.I)
COUNT_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kKmM]?)\s*(?:labeled\s+|labelled\s+|annotated\s+|training\s+|test(?:ing)?\s+|"
    r"validation\s+|colou?r\s+|gr[ae]yscale\s+|natural\s+|\d+\s*[×x]\s*\d+\s+)*"
    r"(images?|samples?|instances?|examples?|videos?|clips?|sentences?|utterances?|recordings?|classes|categories|subjects|"
    r"patients|participants|speakers|hours|pairs|documents|frames|scenes|records|questions|segments|tokens|words)\b"
    r"|(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*万?)\s*(个样本|张图像|张图片|幅图像|幅图片|个类别|类|段音频|段录音|条样本|条记录|条|段|名受试者|名被试|名患者|例|小时|帧|个片段)", re.I)
SPLIT_RE = re.compile(r"\b(?:train(?:ing)?|test(?:ing)?|validation|val|split)\b|训练集|测试集|验证集|划分", re.I)


def extract_dataset_claims(text):
    out = []
    for sentence in sentences(text):
        counts = [{"value": (m.group(1) or m.group(3)).strip(), "unit": (m.group(2) or m.group(4))} for m in COUNT_RE.finditer(sentence)]
        if not counts:
            continue
        names = sorted({m.group(0) for m in KNOWN_DATASET_RE.finditer(sentence)})
        if not names and not DATASET_WORD_RE.search(sentence):
            continue
        out.append({"sentence": re.sub(r"\s+", " ", sentence).strip()[:400], "datasets": names, "counts": counts[:12],
                    "mentionsSplit": bool(SPLIT_RE.search(sentence))})
    return out[:60]


URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'{}|\\^`\]\)，。；、）]+|(?<![\w/.])(?:github\.com|gitlab\.com|huggingface\.co|zenodo\.org|figshare\.com|osf\.io)/[^\s<>\"'{}|\\^`\]\)，。；、）]+", re.I)
CODE_HOST_RE = re.compile(r"github\.com|gitlab\.com|bitbucket\.org|gitee\.com|huggingface\.co|codeocean\.com|sourceforge\.net", re.I)
DATA_HOST_RE = re.compile(r"zenodo\.org|figshare\.com|osf\.io|datadryad\.org|kaggle\.com|drive\.google\.com|pan\.baidu\.com|dataverse|"
                          r"data\.mendeley\.com|openneuro\.org|physionet\.org|10\.5281/zenodo|10\.6084/m9\.figshare", re.I)
AVAIL_RE = re.compile(r"\bavailable\b|\breleased?\b|open[- ]?source|publicly|can\s+be\s+(?:found|downloaded|accessed|obtained)|"
                      r"\bcode\b|\brepository\b|开源|代码|可在|获取|下载|公开", re.I)


def extract_links(body_text, extra=()):
    found = {}
    text = norm_text(body_text or "")
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:")
        start = max(0, m.start() - 160)
        context = re.sub(r"\s+", " ", text[start:m.end() + 80]).strip()
        found.setdefault(url, context)
    for url, context in extra:
        found.setdefault(url.rstrip(".,;:"), context or "")
    out = []
    for url, context in found.items():
        if re.search(r"(?:dx\.)?doi\.org/10\.(?!5281|6084)", url, re.I):
            continue
        kind = "code" if CODE_HOST_RE.search(url) else ("data" if DATA_HOST_RE.search(url) else "other")
        claimed = bool(AVAIL_RE.search(context))
        if kind == "other" and not claimed:
            continue
        if not re.match(r"https?://", url, re.I):
            url = "https://" + url
        out.append({"url": url, "kind": kind, "claimedAvailable": claimed, "context": context[:300]})
    return out[:40]


def detect_paper_year(raw):
    if raw.get("kind") == "latex":
        for content in raw.get("contents", []):
            m = re.search(r"\\date\s*\{([^}]*)\}", content)
            if m:
                y = YEAR_RE.findall(m.group(1))
                if y:
                    return int(y[-1])
    return None


# ---------------------------------------------------------------------------
# dossier + offline entry point
# ---------------------------------------------------------------------------


def _disclosure_for_notes(notes):
    """{marker: 'rerun'|'quoted'} from table notes like '† re-implemented by us'."""
    defs = {}
    for m in re.finditer(r"([†‡*§])\s*([^†‡*§]{3,200})", notes or ""):
        seg = m.group(2)
        if RERUN_RE.search(seg):
            defs[m.group(1)] = "rerun"
        elif QUOTED_RE.search(seg):
            defs[m.group(1)] = "quoted"
    return defs


def _paper_level_rerun(text):
    for sentence in sentences(text):
        if RERUN_RE.search(sentence) and re.search(r"baselines?|compared\s+methods|competing|comparison|对比方法|基线|对比算法|比较方法", sentence, re.I):
            return re.sub(r"\s+", " ", sentence).strip()[:300]
    return ""


def load_rich_tables(raw, helpers):
    if raw.get("kind") == "docx":
        return docx_rich_tables(raw["path"])
    tables = []
    for content in raw.get("contents", []):
        for t in latex_rich_tables(content, helpers):
            t["index"] = len(tables)
            tables.append(t)
    return tables


def analyze_baselines(raw, full_text, number_text, refinfo, source_label, helpers, paper_year=None):
    """Offline baseline checks. Returns (findings, dossier)."""
    tables = load_rich_tables(raw, helpers)
    names = proposed_names(number_text)
    for t in tables:
        analyze_table(t, names)
    findings = []
    for t in tables:
        if not t["isCandidate"]:
            continue
        findings += check_best_marked(t, source_label)
        findings += check_metric_consistency(t, source_label)
    findings += check_cross_table_values(tables, source_label)
    findings += check_improvement_claims(number_text, tables, names, source_label)

    entries = refinfo.get("entries") or []
    index = reference_index(refinfo)
    raw_body = raw.get("body", "")
    paper_rerun = _paper_level_rerun(number_text)
    references = {}
    dossier_tables = []
    for t in tables:
        if not t["isComparison"]:
            continue
        markers = _disclosure_for_notes(" ".join([t.get("caption", ""), t.get("notes", "")]))
        table_text = " ".join([t.get("caption", ""), t.get("notes", "")])
        table_rerun = (not markers) and bool(RERUN_RE.search(table_text))
        table_quoted = (not markers) and bool(QUOTED_RE.search(table_text))
        cols = [{"index": c["index"], "name": c["name"], "direction": c["direction"], "cost": c["cost"]} for c in t["columns"][1:]]
        entry = {"table": table_name(t), "tableIndex": t["index"] + 1, "caption": t.get("caption", "")[:400],
                 "notes": t.get("notes", "")[:400], "columns": cols, "proposed": [], "baselines": [], "variants": [],
                 "disclosure": {"markers": markers, "tableRerun": table_rerun, "tableQuoted": table_quoted,
                                "paperLevelRerun": paper_rerun}}
        for i in t["dataRows"]:
            row = t["rows"][i]
            values = {}
            for col in t["columns"][1:]:
                v = _val(t, i, col["index"])
                if v:
                    values[col["name"]] = v["raw"] + ("±" + v["sdRaw"] if v["sdRaw"] else "")
            item = {"row": i + 1, "method": _clean_label(row[0]["text"]), "values": values,
                    "bold": [t["columns"][k]["name"] for k in range(1, min(len(row), t["ncols"])) if row[k]["bold"] and row[k]["value"]]}
            role = t["roles"][i]
            if role == "proposed":
                entry["proposed"].append(item)
                continue
            if role == "variant":
                entry["variants"].append(item)
                continue
            cites = _row_cites(t, i)
            cite_src = "cell" if cites else "none"
            if not cites:
                cites = find_text_cites(row[0]["text"], raw_body, raw.get("kind"))
                cite_src = "text" if cites else "none"
            row_markers = sorted({m for cell in row for m in cell["markers"]})
            disclosure = None
            for mk in row_markers:
                if mk in markers:
                    disclosure = markers[mk]
            if disclosure is None and RERUN_RE.search(row[0]["text"]):
                disclosure = "rerun"
            if disclosure is None and table_rerun:
                disclosure = "rerun(table)"
            if disclosure is None and paper_rerun:
                disclosure = "rerun(paper)"
            if disclosure is None and table_quoted:
                disclosure = "quoted(table)"
            refs = []
            for c in dict.fromkeys(cites):
                e = resolve_cite(c, index, entries)
                if e is None:
                    continue
                rid = c
                if rid not in references:
                    references[rid] = ref_record(rid, e)
                refs.append(rid)
            item.update({"cites": list(dict.fromkeys(cites)), "citationSource": cite_src, "refs": refs,
                         "markers": row_markers, "disclosure": disclosure})
            entry["baselines"].append(item)
        dossier_tables.append(entry)
    body_for_links = raw.get("linkText", number_text)
    dossier = {
        "tool": "geng-skills " + VERSION,
        "paperYear": paper_year or detect_paper_year(raw),
        "proposedNames": names,
        "tables": dossier_tables,
        "references": references,
        "datasetClaims": extract_dataset_claims(number_text),
        "links": extract_links(body_for_links, raw.get("extraLinks", ())),
        "online": {"enabled": False},
        "sources": {},
        "fetchErrors": [],
        "note": "脚本只给信号：这里的对比方法、引用、数据集描述与链接需要模型逐条核对原文后在 verdicts.json 的 baselineVerdicts 里下判断。",
    }
    return findings, dossier


# ---------------------------------------------------------------------------
# online retrieval (opt-in)
# ---------------------------------------------------------------------------


def http_fetch(url, method="GET", timeout=45, mailto=None, max_bytes=40 * 1024 * 1024):
    """(status, body bytes or None). status -1 = DNS failure, 0 = other network error."""
    ua = USER_AGENT if not mailto else USER_AGENT[:-1] + "; mailto:" + mailto + ")"
    request = urllib.request.Request(url, method=method, headers={"User-Agent": ua, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, (response.read(max_bytes) if method != "HEAD" else b"")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError as exc:
        return (-1 if isinstance(getattr(exc, "reason", None), socket.gaierror) else 0), None
    except Exception:
        return 0, None


class _Pacer:
    """Polite per-host spacing (arXiv asks for 3 s between API calls)."""

    def __init__(self, pause):
        self.pause = pause
        self.last = {}

    def wait(self, url):
        if not self.pause:
            return
        host = urllib.parse.urlsplit(url).netloc
        gap = max(self.pause, 3.0) if "arxiv.org" in host else self.pause
        dt = time.time() - self.last.get(host, 0.0)
        if dt < gap:
            time.sleep(gap - dt)
        self.last[host] = time.time()


def _as_text(body):
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def _as_json(body):
    try:
        return json.loads(_as_text(body))
    except Exception:
        return None


class _HTMLText(html.parser.HTMLParser):
    SKIP = {"script", "style", "annotation", "annotation-xml", "head", "nav", "footer", "button", "noscript", "svg"}
    BLOCK = {"p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table", "caption", "figcaption", "br",
             "article", "header", "figure", "dd", "dt", "blockquote", "pre", "tbody", "thead"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.row = 0   # inside <tr>: keep the whole table row on one line

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "tr":
            self.row += 1
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in self.BLOCK:
            self.parts.append(" " if self.row else "\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif self.skip:
            return
        elif tag == "tr":
            self.row = max(0, self.row - 1)
            self.parts.append("\n")
        elif tag in self.BLOCK:
            self.parts.append(" " if self.row else "\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(page):
    parser = _HTMLText()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        pass
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in "".join(parser.parts).split("\n")]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def pdf_to_text(data):
    try:
        try:
            import pymupdf as fitz  # noqa: F401
        except ImportError:
            import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception:
        return None


def _inverted_to_text(inv):
    if not inv:
        return ""
    pos = []
    for word, places in inv.items():
        for p in places:
            pos.append((p, word))
    return " ".join(w for _p, w in sorted(pos))


def _parse_arxiv_atom(body):
    try:
        root = ET.fromstring(_as_text(body))
    except ET.ParseError:
        return []
    A = "{http://www.w3.org/2005/Atom}"
    out = []
    for e in root.findall(A + "entry"):
        title = re.sub(r"\s+", " ", e.findtext(A + "title") or "").strip()
        idurl = (e.findtext(A + "id") or "").strip()
        m = re.search(r"abs/(.+?)(v\d+)?$", idurl)
        if not title or title.lower() == "error" or not m:
            continue
        out.append({"title": title, "abstract": re.sub(r"\s+", " ", e.findtext(A + "summary") or "").strip(),
                    "year": int((e.findtext(A + "published") or "0000")[:4] or 0) or None,
                    "arxivId": m.group(1), "version": m.group(2) or "", "url": "https://arxiv.org/abs/" + m.group(1)})
    return out


def _openalex_meta(w):
    if not isinstance(w, dict) or not (w.get("display_name") or w.get("title")):
        return None
    arxiv, pdf = "", ""
    for loc in [w.get("best_oa_location")] + list(w.get("locations") or []):
        if not loc:
            continue
        for u in (loc.get("landing_page_url"), loc.get("pdf_url")):
            m = ARXIV_ID_RE.search(u or "")
            if m and not arxiv:
                arxiv = m.group(1)
        if not pdf and loc.get("pdf_url"):
            pdf = loc["pdf_url"]
    return {"title": w.get("display_name") or w.get("title"), "year": w.get("publication_year"),
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""), "abstract": _inverted_to_text(w.get("abstract_inverted_index")),
            "arxivId": arxiv, "pdfUrl": pdf, "url": w.get("id") or ""}


def _title_ok(candidate, ref):
    if not candidate:
        return False
    words = refs_check._words(candidate)
    if len(words) < 3:
        return False
    target = ref.get("text", "") + " " + ref.get("titleGuess", "")
    if refs_check._title_match(candidate, target) < 0.8:
        return False
    if ref.get("titleGuess") and refs_check._title_match(ref["titleGuess"], candidate) < 0.6:
        return False
    return True


OA_SELECT = "id,display_name,publication_year,doi,abstract_inverted_index,best_oa_location,locations"


def resolve_reference(ref, get, mailto=None):
    """Find the cited work and its open text. Returns a source dict (``resolved`` False when nothing matched)."""
    src = {"refId": ref["refId"], "resolved": False, "answered": False, "attempts": []}
    meta, via = None, ""
    mail = ("&mailto=" + urllib.parse.quote(mailto)) if mailto else ""

    def call(url):
        status, body = get(url)
        src["attempts"].append({"url": url, "status": status})
        if status in (200, 404, 400):
            src["answered"] = True
        return status, body

    if ref.get("arxivId"):
        st, body = call("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(ref["arxivId"]))
        hits = _parse_arxiv_atom(body) if st == 200 else []
        if hits:
            meta, via = hits[0], "arxiv-id"
    if meta is None and ref.get("doi"):
        st, body = call("https://api.openalex.org/works/doi:" + urllib.parse.quote(ref["doi"]) + "?select=" + OA_SELECT + mail)
        m = _openalex_meta(_as_json(body)) if st == 200 else None
        if m:
            meta, via = m, "openalex-doi"
        else:
            st, body = call("https://api.crossref.org/works/" + urllib.parse.quote(ref["doi"]))
            msg = (_as_json(body) or {}).get("message") if st == 200 else None
            if msg and msg.get("title"):
                abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract") or "")
                year = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0][0]
                meta, via = {"title": msg["title"][0], "year": year, "doi": ref["doi"], "abstract": abstract.strip(),
                             "arxivId": "", "pdfUrl": "", "url": "https://doi.org/" + ref["doi"]}, "crossref-doi"
        if meta and ref.get("titleGuess") and refs_check._title_match(meta["title"], ref["text"] + " " + ref["titleGuess"]) < 0.5:
            src["doiTitleMismatch"] = meta["title"]
            meta = None
    if meta is None and ref.get("titleGuess"):
        q = urllib.parse.quote(ref["titleGuess"][:250])
        st, body = call("https://api.openalex.org/works?search=" + q + "&per-page=5&select=" + OA_SELECT + mail)
        for w in ((_as_json(body) or {}).get("results") or []) if st == 200 else []:
            m = _openalex_meta(w)
            if m and _title_ok(m["title"], ref) and not (ref.get("year") and m.get("year") and abs(int(m["year"]) - ref["year"]) > 2):
                meta, via = m, "openalex-search"
                break
        if meta is None:
            phrase = re.sub(r"[^\w\s-]", " ", ref["titleGuess"])[:200]
            st, body = call("https://export.arxiv.org/api/query?max_results=5&search_query=" + urllib.parse.quote('ti:"%s"' % phrase.strip()))
            for m in _parse_arxiv_atom(body) if st == 200 else []:
                if _title_ok(m["title"], ref):
                    meta, via = m, "arxiv-search"
                    break
    if meta is None:
        return src
    src.update({"resolved": True, "resolvedVia": via, "title": meta.get("title", ""), "year": meta.get("year"),
                "doi": meta.get("doi") or ref.get("doi", ""), "arxivId": meta.get("arxivId") or ref.get("arxivId", ""),
                "abstract": meta.get("abstract", ""), "url": meta.get("url", "")})
    if via in ("openalex-search", "openalex-doi") and not src["arxivId"] and ref.get("arxivId"):
        src["arxivId"] = ref["arxivId"]
    full, full_from, full_url = None, "", ""
    if src["arxivId"]:
        url = "https://arxiv.org/html/" + src["arxivId"]
        st, body = call(url)
        page = _as_text(body) if st == 200 else ""
        if page and ("ltx_" in page or "<article" in page):
            full, full_from, full_url = html_to_text(page), "arxiv-html", url
        if full is None:
            url = "https://arxiv.org/pdf/" + src["arxivId"]
            st, body = call(url)
            if st == 200 and isinstance(body, (bytes, bytearray)) and body[:4] == b"%PDF":
                full = pdf_to_text(body)
                full_from, full_url = ("arxiv-pdf", url) if full else ("", "")
            elif st == 200 and isinstance(body, str) and body:
                full, full_from, full_url = body, "arxiv-pdf", url  # injected test fetch returns text directly
    if full is None and meta.get("pdfUrl"):
        st, body = call(meta["pdfUrl"])
        if st == 200 and isinstance(body, (bytes, bytearray)) and body[:4] == b"%PDF":
            full = pdf_to_text(body)
            full_from, full_url = ("oa-pdf", meta["pdfUrl"]) if full else ("", "")
    src["fullText"] = bool(full)
    src["fullTextFrom"] = full_from
    src["fullTextUrl"] = full_url
    src["_text"] = (full or "")[:3_000_000]
    return src


def _slug(text, limit=60):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:limit] or "ref"


def save_source(src, ref, out_dir, today):
    folder = os.path.join(out_dir, "baseline_sources")
    os.makedirs(folder, exist_ok=True)
    name = _slug(ref["refId"].split(":", 1)[-1], 30) + "--" + _slug(src.get("title", ""), 50) + ".txt"
    path = os.path.join(folder, name)
    header = [
        "# geng-skills baseline source (retrieved for the model to read; not part of the paper)",
        "# cited as: %s %s" % (ref["refId"], ref.get("text", "")[:200]),
        "# title: %s" % src.get("title", ""),
        "# year: %s" % (src.get("year") or ""),
        "# url: %s" % (src.get("fullTextUrl") or src.get("url") or ""),
        "# resolved via: %s" % src.get("resolvedVia", ""),
        "# full text: %s" % (src.get("fullTextFrom") or "no (abstract only)"),
        "# retrieved: %s" % today,
        "",
        "## Title", src.get("title", ""), "",
        "## Abstract", src.get("abstract", ""), "",
        "## Full text", src.get("_text", "") or "(not openly available)",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(header))
    return os.path.relpath(path, out_dir).replace("\\", "/")


STOP_TOKENS = {"with", "the", "and", "for", "layers", "layer", "model", "network", "net", "method", "baseline", "base",
               "large", "small", "tiny", "pretrained", "pre-trained", "fine-tuned", "our", "ours", "deep"}
ACRONYMS = {"svm": "support vector", "rf": "random forest", "knn": "nearest neighbo", "k-nn": "nearest neighbo",
            "cnn": "convolutional", "lstm": "long short-term memory", "gru": "gated recurrent", "mlp": "multilayer perceptron",
            "gbdt": "gradient boost", "vit": "image is worth", "gan": "generative adversarial", "lr": "logistic regression"}


def name_variants(label):
    core = _clean_label(label)
    noparen = re.sub(r"\([^)]*\)|（[^）]*）|\[[^\]]*\]", " ", core).strip()
    tokens = [tk for tk in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-‐.][A-Za-z0-9]+)*", noparen)
              if len(tk) >= 3 and tk.lower() not in STOP_TOKENS]
    distinctive = [tk for tk in tokens if re.search(r"[A-Z]", tk[1:]) or re.search(r"\d", tk) or len(tk) >= 6]
    specific = []
    for v in (core, noparen):
        n = re.sub(r"\s+", "", v.lower())
        if n and (re.search(r"\d", n) or len(n) >= 6) and n not in specific:
            specific.append(n)
    return {"core": core, "noparen": noparen, "distinctive": distinctive or tokens, "specific": specific}


def name_in_source(label, text_lower, nospace):
    v = name_variants(label)
    if v["noparen"] and re.sub(r"\s+", "", v["noparen"].lower()) in nospace:
        return True
    for tk in v["distinctive"]:
        if tk.lower() in text_lower:
            return True
        base = re.split(r"[-‐.]", tk)[0].lower()
        if len(base) >= 4 and base in text_lower:
            return True
    for tk in [v["noparen"].lower()] + [x.lower() for x in v["distinctive"]]:
        exp = ACRONYMS.get(tk)
        if exp and exp in text_lower:
            return True
    return not v["distinctive"]  # nothing distinctive to look for -> do not flag


def value_in_source(v, src_vals, acc_err):
    """Where the reported value appears in the source: same number (rounding allowed), then integer-only
    tokens, then % <-> fraction, then 100 − x (accuracy vs error). None when nowhere."""
    r, d = v["value"], v["decimals"]
    h = _half(d) + EPS

    def same(s, c):
        return "." in c and abs(s - r) <= h

    def same_int(s, c):
        return "." not in c and abs(s - r) <= h

    def scaled(s, c):
        return "." in c and ((s <= 1 and abs(s * 100 - r) <= h) or (r <= 1 and 1 < s <= 100 and abs(s / 100 - r) <= h))

    def complement(s, c):
        return acc_err and "." in c and 0 < r < 100 and 0 < s < 100 and abs((100 - s) - r) <= h

    for test, note in ((same, ""), (same_int, ""), (scaled, " (量纲换算)"), (complement, " (100 − 原值，准确率/错误率互换)")):
        for s, cleaned in src_vals:
            if test(s, cleaned):
                return cleaned + note
    return None


def _snippets(text, terms, limit=4, width=260):
    out = []
    low = text.lower()
    for term in terms:
        term = (term or "").strip().lower()
        if len(term) < 3:
            continue
        start = 0
        while len(out) < limit:
            k = low.find(term, start)
            if k < 0:
                break
            a, b = max(0, k - width), min(len(text), k + len(term) + width)
            frag = re.sub(r"\s+", " ", text[a:b]).strip()
            if re.search(r"\d\.\d", frag) and frag not in out:
                out.append(frag)
            start = k + len(term)
        if len(out) >= limit:
            break
    return out


def _source_lines(text_lines, specific):
    """Lines (table rows) of the source that name the method; a row whose cells were extracted one per line
    (PDF text) is joined with the following lines up to a blank line."""
    for v in specific:  # most specific first: "densenet-bc(k=40)" before "densenet-bc"
        hits = _source_lines_for(text_lines, v)
        if hits:
            return hits
    return []


def _source_lines_for(text_lines, variant):
    hits = []
    for k, ln in enumerate(text_lines):
        ns = re.sub(r"\s+", "", ln.lower())
        for v in (variant,):
            if v and v in ns:
                rest = ns.replace(v, " ", 1)
                if not re.search(r"\d\.\d", rest):
                    extra = []
                    for nxt in text_lines[k + 1:k + 9]:
                        if not nxt.strip() or (extra and len(nxt) > 60):
                            break
                        extra.append(nxt.strip())
                    ln = ln + " " + " ".join(extra)
                    rest = rest + " " + re.sub(r"\s+", "", " ".join(extra).lower())
                hits.append((ln, rest))
                break
    return hits[:6]


def _header_terms(colname):
    terms = []
    for part in colname.split(" / "):
        p = re.sub(r"\(.*?\)|[%↑↓$]|\\(?:up|down)arrow", " ", part).strip()
        if len(p) >= 3:
            terms.append(p)
    return terms


def check_against_sources(dossier, sources, source_label, paper_year=None):
    findings = []
    for entry in dossier["tables"]:
        cols = {c["name"]: c for c in entry["columns"]}
        for b in entry["baselines"]:
            for rid in b.get("refs", []):
                src = sources.get(rid)
                if not src or not src.get("resolved"):
                    continue
                text = src.get("_text") or ""
                rerun = bool(b.get("disclosure") and b["disclosure"].startswith("rerun"))
                loc = "%s#%s 行%d: %s %s" % (source_label, entry["table"], b["row"], b["method"], rid)
                if paper_year and src.get("year") and int(src["year"]) > int(paper_year):
                    findings.append({
                        "type": "baseline_newer_than_paper", "severity": "info", "location": loc,
                        "stat": {"sourceYear": src["year"], "paperYear": paper_year, "title": src.get("title", "")},
                        "explanation": "被引对比方法的发表年份晚于本文标注的年份，请确认引用对象是否正确（也可能本文年份未更新）。",
                    })
                if not text:
                    continue
                low = text.lower()
                nospace = re.sub(r"\s+", "", low)
                if not name_in_source(b["method"], low, nospace):
                    findings.append({
                        "type": "baseline_name_not_in_source", "severity": "low", "location": loc,
                        "stat": {"method": b["method"], "source": src.get("title", ""), "file": src.get("file", ""),
                                 "variantsSearched": name_variants(b["method"])["distinctive"]},
                        "explanation": "在被引文献的全文里找不到这个方法名（或其缩写/主要词）。可能是引用张冠李戴（引了别的论文），"
                                       "也可能原文用了不同的名字，请模型打开原文确认。",
                    })
                src_vals = source_numbers(text)
                lines = text.split("\n")
                specific = name_variants(b["method"])["specific"]
                line_hits = _source_lines(lines, specific)
                checks, found, missing, differs = {}, [], [], []
                for colname, raw_value in b["values"].items():
                    col = cols.get(colname) or {}
                    v = cell_value(raw_value)
                    if not v or _sig(v["raw"]) < 2:
                        continue
                    acc_err = bool(ACC_ERR_RE.search(colname))
                    hit = value_in_source(v, src_vals, acc_err)
                    checks[colname] = {"reported": v["raw"], "foundInSource": bool(hit), "match": hit}
                    if col.get("cost"):
                        continue
                    if hit:
                        found.append(colname)
                        continue
                    missing.append(colname)
                    cands = []
                    for ln, rest in line_hits:
                        # numbers of the row, without parameter counts like 25.6M / 4.5G
                        nums = [(float(x), x) for x in re.findall(r"(?<![\d.])(\d+\.\d+|\d+)(?![\d.])(?![mkgbt])", rest)]
                        nums = [(s, x) for s, x in nums if ("." in x or v["decimals"] == 0) and 0.5 * v["value"] <= s <= 2 * v["value"]]
                        if nums:
                            cands.append({"line": ln[:300], "numbers": [x for _s, x in nums]})
                    if cands:
                        d = col.get("direction")
                        all_nums = [float(x) for c in cands for x in c["numbers"]]
                        margin = max(0.5 if v["value"] > 1 else 0.005, 0.1 * v["value"])
                        lowered = bool(d) and all(
                            (v["value"] < s - margin) if d == "higher" else (v["value"] > s + margin) for s in all_nums)
                        differs.append({"column": colname, "reported": v["raw"], "sourceLines": cands, "baselineLowered": lowered})
                b.setdefault("valueChecks", {})[rid] = checks
                if differs:
                    lowered = any(x["baselineLowered"] for x in differs)
                    sev = "info" if rerun else ("high" if lowered else "medium")
                    findings.append({
                        "type": "baseline_value_differs_from_source", "severity": sev, "location": loc,
                        "stat": {"method": b["method"], "source": src.get("title", ""), "file": src.get("file", ""),
                                 "url": src.get("fullTextUrl") or src.get("url", ""), "disclosure": b.get("disclosure"),
                                 "columns": differs, "valuesFound": found},
                        "explanation": ("被引原文里有提到该方法的结果行，但数值与本文表中所报的不同"
                                        + ("，且无论对应原文哪个数，本文都把这个对比方法报得更差（基线疑似被压低）" if lowered else "")
                                        + "。正当原因包括：作者重新运行过并已说明、数据划分/设置不同、版本（arXiv v1 与正式版）不同。"
                                        + ("本文已声明重新运行/复现，此条仅供核对。" if rerun else "")
                                        + "请模型打开原文对照同一数据集、同一指标、同一设置的数值。"),
                    })
                elif missing:
                    sev = "info" if rerun else ("medium" if not found else "low")
                    terms = [b["method"], name_variants(b["method"])["noparen"]]
                    for colname in missing:
                        terms += _header_terms(colname)
                    findings.append({
                        "type": "baseline_value_not_in_source", "severity": sev, "location": loc,
                        "stat": {"method": b["method"], "source": src.get("title", ""), "file": src.get("file", ""),
                                 "url": src.get("fullTextUrl") or src.get("url", ""), "disclosure": b.get("disclosure"),
                                 "missing": {c: b["values"][c] for c in missing}, "found": found,
                                 "snippets": _snippets(text, terms)},
                        "explanation": ("本文为该对比方法报告的"
                                        + ("全部" if not found else "部分")
                                        + "性能数值在被引原文全文中找不到（已允许四舍五入、百分数/小数换算、准确率/错误率互换）。"
                                        + ("本文已声明这些结果是重新运行/复现的，找不到属预期，请确认设置说明充分。" if rerun else
                                           "可能是作者自己重跑了却没说明、引用了另一篇论文的数、或数值被改动。")
                                        + "snippets 里是原文中提到该方法/数据集的片段，请模型对照。"),
                    })
    # results of the proposed method inside a cited work
    for entry in dossier["tables"]:
        for p in entry["proposed"]:
            vals = []
            for raw_value in p["values"].values():
                v = cell_value(raw_value)
                if v and _sig(v["raw"]) >= 3 and v["value"] not in (0.0, 100.0) and v["raw"] not in vals:
                    vals.append(v["raw"])
            if len(vals) < 3:
                continue
            for rid, src in sources.items():
                text = src.get("_text") or src.get("abstract") or ""
                if not text:
                    continue
                present = {c for _s, c in source_numbers(text)}
                hits = [x for x in vals if x in present]
                if len(hits) >= 3:
                    findings.append({
                        "type": "proposed_results_in_cited_work", "severity": "high",
                        "location": "%s#%s 行%d: %s <-> %s" % (source_label, entry["table"], p["row"], p["method"], rid),
                        "stat": {"method": p["method"], "valuesFound": hits, "valuesChecked": vals,
                                 "source": src.get("title", ""), "file": src.get("file", ""), "url": src.get("url", "")},
                        "explanation": "本文方法的 %d 个结果数值同时出现在一篇被引文献里。可能是把别人的结果当成自己的，"
                                       "也可能本文是该文的扩展版/同一作者的前作——请模型核对作者与是否已声明。" % len(hits),
                    })
    return findings


def check_links(links, get, max_links=20):
    findings = []
    checks = []
    for link in links[:max_links]:
        url = link["url"]
        status, verdict, detail = None, "unchecked", ""
        gh = re.match(r"https?://(?:www\.)?github\.com/([^/\s#?]+)/([^/\s#?]+)", url, re.I)
        if gh and gh.group(1).lower() not in ("orgs", "topics", "features", "about", "sponsors"):
            repo = gh.group(2)
            repo = repo[:-4] if repo.lower().endswith(".git") else repo
            status, body = get("https://api.github.com/repos/%s/%s" % (gh.group(1), repo))
            if status == 200:
                data = _as_json(body) or {}
                verdict = "empty" if data.get("size") == 0 else "ok"
                detail = "archived" if data.get("archived") else ""
            elif status in (404, 410):
                verdict = "dead"
            elif status in (-1,):
                verdict = "dead"
        else:
            status, _body = get(url, "HEAD")
            if status in (0, 400, 403, 405, 406, 429, 501) or status is None:
                status, _body = get(url, "GET")
            if status in (404, 410) or status == -1:
                verdict = "dead"
            elif status and 200 <= status < 400:
                verdict = "ok"
        checks.append({"url": url, "kind": link["kind"], "status": status, "result": verdict, "detail": detail})
        if verdict in ("dead", "empty"):
            claimed_code = link["kind"] == "code" and link["claimedAvailable"]
            sev = "medium" if (claimed_code and verdict == "dead") else "low"
            findings.append({
                "type": "claimed_link_unreachable", "severity": sev, "location": url,
                "stat": {"url": url, "kind": link["kind"], "status": status, "result": verdict, "context": link["context"][:200]},
                "explanation": ("文中声称可获取的代码/数据链接打不开（%s）。" % ("HTTP %s" % status if status and status > 0 else "域名无法解析")
                                if verdict == "dead" else "链接指向的代码仓库是空的（没有任何文件）。")
                               + "审稿人和读者无法复现；投稿前请确认仓库已公开、地址拼写正确。",
            })
    return findings, checks


def fetch_baselines(dossier, source_label, out_dir, fetch=None, mailto=None, max_papers=30, pause=1.0, today=None, max_links=20):
    """Online part of the baseline check. Mutates ``dossier`` (sources, online, fetchErrors); returns findings."""
    if fetch is None:
        fetch = functools.partial(http_fetch, mailto=mailto)
    today = today or date.today().isoformat()
    pacer = _Pacer(pause)
    errors = dossier.setdefault("fetchErrors", [])

    def get(url, method="GET"):
        pacer.wait(url)
        try:
            status, body = fetch(url, method=method)
        except Exception as exc:  # an injected fetch or the network may raise; never crash the run
            status, body = 0, None
            errors.append({"url": url, "status": 0, "error": str(exc)[:200]})
            return status, body
        if status is None or status in (0, -1, 429) or (isinstance(status, int) and status >= 500):
            errors.append({"url": url, "status": status})
        return status, body

    refs = dossier.get("references", {})
    order = []
    for entry in dossier.get("tables", []):
        for b in entry["baselines"]:
            for rid in b.get("refs", []):
                if rid not in order:
                    order.append(rid)
    findings = []
    sources = {}
    skipped = order[max_papers:]
    for n, rid in enumerate(order[:max_papers], 1):
        ref = refs[rid]
        print("[baselines] (%d/%d) %s %s" % (n, min(len(order), max_papers), rid, (ref.get("titleGuess") or ref.get("text", ""))[:70]),
              file=sys.stderr)
        src = resolve_reference(ref, get, mailto=mailto)
        if src.get("resolved"):
            src["file"] = save_source(src, ref, out_dir, today)
        elif src.get("answered") and refs_check._is_english_entry(ref.get("text", "")):
            users = [(e["table"], b["method"]) for e in dossier["tables"] for b in e["baselines"] if rid in b.get("refs", [])]
            findings.append({
                "type": "cited_baseline_not_found", "severity": "medium",
                "location": "%s#参考文献 %s" % (source_label, rid),
                "stat": {"ref": ref.get("text", "")[:300], "titleGuess": ref.get("titleGuess", ""), "doi": ref.get("doi", ""),
                         "arxivId": ref.get("arxivId", ""), "usedFor": [{"table": a, "method": m} for a, m in users],
                         "doiTitleMismatch": src.get("doiTitleMismatch", "")},
                "explanation": "这条被当作对比方法来源的参考文献在 arXiv / OpenAlex / Crossref 都找不到相符的论文。"
                               "可能是标题/DOI 写错，也可能是虚构（AI 幻觉）的引用——那样表中该方法的数值也就没有出处。请模型再用搜索核实。",
            })
        sources[rid] = src
    # findings that need the source text
    findings += check_against_sources(dossier, sources, source_label, dossier.get("paperYear"))
    for rid, src in sources.items():
        if src.get("resolved") and not src.get("fullText"):
            users = [{"table": e["table"], "method": b["method"], "values": b["values"]}
                     for e in dossier["tables"] for b in e["baselines"] if rid in b.get("refs", [])]
            findings.append({
                "type": "baseline_source_abstract_only", "severity": "info",
                "location": "%s#参考文献 %s" % (source_label, rid),
                "stat": {"source": src.get("title", ""), "year": src.get("year"), "url": src.get("url", ""),
                         "doi": src.get("doi", ""), "file": src.get("file", ""), "toCheck": users},
                "explanation": "只拿到了被引文献的题录/摘要，拿不到开放全文，脚本无法核对数值。请模型用检索工具打开原文"
                               "（出版社页面、OpenReview、CVF/ACL 开放获取等），逐个核对 toCheck 里的数值。",
            })
    link_findings, link_checks = check_links(dossier.get("links", []), get, max_links=max_links)
    findings += link_findings
    for rid, src in sources.items():
        src.pop("_text", None)
    dossier["sources"] = sources
    dossier["linkChecks"] = link_checks
    dossier["online"] = {
        "enabled": True, "retrievedAt": today, "userAgent": USER_AGENT, "papersRequested": len(order),
        "papersFetched": sum(1 for s in sources.values() if s.get("resolved")),
        "fullText": sum(1 for s in sources.values() if s.get("fullText")),
        "abstractOnly": sum(1 for s in sources.values() if s.get("resolved") and not s.get("fullText")),
        "unresolved": [rid for rid, s in sources.items() if not s.get("resolved")],
        "skippedOverLimit": skipped,
    }
    return findings
