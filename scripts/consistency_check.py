# -*- coding: utf-8 -*-
"""Consistency between reported numbers (zero dependencies).

1. GRIM / GRIMMER: for integer data (Likert or rating scales, counts) a reported mean must be a
   possible fraction k/n and the reported SD must be reachable from integer values with that sum.
   ``grimmer_check`` is the exact test; ``check_reported_means`` runs GRIM/GRIMMER on
   "M = 3.45, SD = 1.20, n = 20" / "3.45 ± 1.20 (n = 20)" in the text, but only when the nearby text
   says the variable is integer-valued, and only flags a result that is impossible under every
   reading (sample or population SD; item mean or scale total).
2. Text vs table: a number quoted in a sentence that cites a table, found in no table, while a table
   holds the same digits with two neighbours swapped (95.3 in the text, 93.5 in the table).

Both are leads for the author: most hits are typos, and a GRIM hit on data that are not integers is
meaningless, which is why the explanation always states the assumption.
"""

import math
import re

EPS = 1e-9

# ---------------------------------------------------------------------------
# GRIM / GRIMMER
# ---------------------------------------------------------------------------


def _half(decimals):
    return 0.5 * 10 ** (-decimals)


def grim_sums(mean, n, decimals, items=1):
    """Integer sums S (of per-person totals) whose mean S/(n*items) rounds to the reported mean."""
    denom = n * items
    half = _half(decimals)
    lo = math.ceil((mean - half - EPS) * denom)
    hi = math.floor((mean + half + EPS) * denom)
    return range(lo, hi + 1) if lo <= hi else range(0)


def grimmer_check(mean, sd, n, decimals, sd_decimals=None, items=1, sample=True):
    """GRIMMER: is the reported SD possible for integer data with this mean and n?

    Every person contributes an integer total T_i (the item scores summed; ``items`` = 1 for a
    single integer variable). With S = sum T_i and Q = sum T_i^2, the SD of the reported means is
    sqrt((Q - S^2/n) / (n-1)) / items (or / n for a population SD). Q must be an integer with the
    same parity as S (x^2 and x are both odd or both even) and at least the smallest sum of squares
    for that S. If no (S, Q) pair reproduces both reported values after rounding, the pair is
    impossible for integer data.
    """
    sd_decimals = decimals if sd_decimals is None else sd_decimals
    if n < 2:
        return {"consistent": True, "applicable": False, "note": "n < 2: GRIMMER does not apply."}
    sums = grim_sums(mean, n, decimals, items)
    base = {"mean": mean, "sd": sd, "n": n, "items": items, "decimals": decimals, "sdDecimals": sd_decimals,
            "sdInterpretedAs": "sample(n-1)" if sample else "population(n)"}
    if not len(sums):
        return {**base, "consistent": False, "grimConsistent": False,
                "note": "均值本身无法由整数总和得到（GRIM 不一致），标准差无需再看。"}
    k = (n - 1) if sample else n
    half_sd = _half(sd_decimals)
    sd_lo = max(0.0, (sd - half_sd - EPS) * items)
    sd_hi = (sd + half_sd + EPS) * items
    for s in sums:
        q_lo = s * s / n + k * sd_lo * sd_lo
        q_hi = s * s / n + k * sd_hi * sd_hi
        base_q, rem = divmod(s, n)
        q_min = (n - rem) * base_q * base_q + rem * (base_q + 1) ** 2
        lo = max(math.ceil(q_lo - EPS), q_min)
        hi = math.floor(q_hi + EPS)
        if lo > hi:
            continue
        first = lo if (lo - s) % 2 == 0 else lo + 1
        if first <= hi:
            return {**base, "consistent": True, "grimConsistent": True, "impliedSum": s, "impliedSumOfSquares": first,
                    "note": "均值和标准差都能由整数数据得到，GRIMMER 通过（只说明数学上可能，不代表数据真实）。"}
    return {**base, "consistent": False, "grimConsistent": True,
            "note": "均值可以由整数总和得到，但在该均值和样本量下，没有任何整数数据能得到这个标准差（GRIMMER 不一致）。"}


# ---------------------------------------------------------------------------
# reported means in the text
# ---------------------------------------------------------------------------

_NUM = r"[-+]?(?:\d+\.\d+|\.\d+|\d+)"
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；;])|(?<=[.!?])\s+(?=[A-Z(（\[])|\n+")

MEAN_RE = re.compile(
    r"(?:(?<![A-Za-z])(?:M|Mean|mean|Avg|avg)\s*(?:=|:|was|of|is)\s*"
    r"|(?:均值|平均值|平均分|平均得分|均分|平均数)\s*(?:为|是|=|:|：|达到)?\s*)"
    r"(" + _NUM + r")")
SD_RE = re.compile(
    r"(?:(?<![A-Za-z])(?:SD|S\.D\.|sd|Sd|std)\s*(?:=|:|was|of|is)\s*|标准差\s*(?:为|是|=|:|：)?\s*)(" + _NUM + r")")
PM_RE = re.compile(r"(" + _NUM + r")\s*(?:±|\+/-|\+-|\\pm)\s*(" + _NUM + r")")
N_RE = re.compile(r"(?<![A-Za-z])[nN]\s*=\s*(\d+)(?![\d.])")
ITEMS_RE = re.compile(r"(\d+)\s*-?\s*items?\b|(\d+)\s*(?:个|道)?\s*(?:条目|题项|道题|个题)", re.I)
INTEGER_CUE_RE = re.compile(
    r"likert|\d+\s*-?\s*point\b|rating|rated|questionnaire|count(?:s|ed)?\b|number of|frequency of|"
    r"量表|李克特|评分|打分|计分|评定|分制|次数|个数|频次",
    re.I)
STAT_EXPR_RE = re.compile(r"(?<![A-Za-z])(?:[ptFrzd]|χ2|chi2)\s*(?:\([^)]*\))?\s*[=<>≤≥]\s*[-−]?\d*\.?\d+")


def split_sentences(text):
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text or "") if s and s.strip()]


def _decimals(raw):
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def _normalize(text):
    return (text or "").replace("（", "(").replace("）", ")").replace("＝", "=").replace("：", ":").replace("−", "-")


def extract_reported_means(text):
    """[(sentence, window, mean_raw, sd_raw or None, n)] for sentences with exactly one n = …."""
    out = []
    sentences = split_sentences(_normalize(text))
    for idx, sentence in enumerate(sentences):
        ns = {m.group(1) for m in N_RE.finditer(sentence)}
        if len(ns) != 1:
            continue  # no sample size, or several groups: ambiguous
        n = int(next(iter(ns)))
        window = " ".join(sentences[max(0, idx - 2): idx + 1])
        events = []
        for m in PM_RE.finditer(sentence):
            events.append((m.start(), "pm", m.group(1), m.group(2)))
        taken = [(m.start(), m.end()) for m in PM_RE.finditer(sentence)]
        for m in MEAN_RE.finditer(sentence):
            if not any(a <= m.start(1) < b for a, b in taken):
                events.append((m.start(), "mean", m.group(1), None))
        for m in SD_RE.finditer(sentence):
            if not any(a <= m.start(1) < b for a, b in taken):
                events.append((m.start(), "sd", m.group(1), None))
        events.sort()
        pending = None
        for _pos, kind, a, b in events:
            if kind == "pm":
                if pending:
                    out.append((sentence, window, pending, None, n))
                    pending = None
                out.append((sentence, window, a, b, n))
            elif kind == "mean":
                if pending:
                    out.append((sentence, window, pending, None, n))
                pending = a
            elif kind == "sd" and pending:
                out.append((sentence, window, pending, a, n))
                pending = None
        if pending:
            out.append((sentence, window, pending, None, n))
    return out


def _snippet(sentence, limit=90):
    s = re.sub(r"\s+", " ", sentence).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def check_reported_means(full_text, source_label):
    findings = []
    seen = set()
    for sentence, window, mean_raw, sd_raw, n in extract_reported_means(full_text):
        if not INTEGER_CUE_RE.search(window):
            continue
        key = (mean_raw, sd_raw, n)
        if key in seen:
            continue
        seen.add(key)
        mean, decimals = float(mean_raw), _decimals(mean_raw)
        if decimals == 0 or n < 2 or mean < 0:
            continue
        item_counts = {1}
        for m in ITEMS_RE.finditer(window):
            k = int(m.group(1) or m.group(2))
            if 1 < k <= 100:
                item_counts.add(k)
        grim_ok = [bool(len(grim_sums(mean, n, decimals, k))) for k in sorted(item_counts)]
        informative = any(n * k < 10 ** decimals for k in item_counts)
        base_stat = {"mean": mean_raw, "n": n, "decimals": decimals, "itemsTried": sorted(item_counts), "sentence": _snippet(sentence, 160)}
        if informative and not any(grim_ok):
            findings.append({
                "type": "grim_inconsistent",
                "severity": "medium",
                "location": f"{source_label}#正文: {_snippet(sentence)}",
                "stat": base_stat,
                "explanation": (
                    f"上下文显示这是整数数据（量表/评分/计数）。在 n = {n} 时，没有任何整数总和能得到报告的均值 {mean_raw}"
                    f"（已按单题均分和总分{'、多题均分' if len(item_counts) > 1 else ''}分别尝试）。"
                    "如果该变量确实是整数数据，这是数学上不可能的结果（按 high 处理）；常见原因是 n、均值抄错或排除了部分被试却没改 n。"
                    "如果变量其实是连续数据（如反应时、比例），此条可忽略。"),
            })
            continue
        if sd_raw is None or not any(grim_ok):
            continue
        sd, sd_decimals = float(sd_raw), _decimals(sd_raw)
        if sd_decimals == 0 or sd < 0:
            continue
        results = [grimmer_check(mean, sd, n, decimals, sd_decimals, items=k, sample=sample)
                   for k in sorted(item_counts) for sample in (True, False)]
        if any(r["consistent"] for r in results):
            continue
        findings.append({
            "type": "grimmer_inconsistent",
            "severity": "medium",
            "location": f"{source_label}#正文: {_snippet(sentence)}",
            "stat": {**base_stat, "sd": sd_raw, "sdDecimals": sd_decimals},
            "explanation": (
                f"上下文显示这是整数数据（量表/评分/计数）。均值 {mean_raw} 在 n = {n} 时可以得到，"
                f"但标准差 {sd_raw} 不能由任何整数数据得到（已按样本/总体标准差、单题/多题分别尝试，GRIMMER）。"
                "如果该变量确实是整数数据，这组均值和标准差数学上不可能同时成立（按 high 处理）；常见原因是数字抄错或 SD 与 SE 混用。"
                "如果变量其实是连续数据，此条可忽略。"),
        })
    return findings


# ---------------------------------------------------------------------------
# text vs table
# ---------------------------------------------------------------------------

TABLE_MENTION_RE = re.compile(
    r"(?<![A-Za-z])(?:Table|Tables|Tab\.)(?![A-Za-z])|(?:如|见|由|从|据|在|于|和|与)\s*表|表\s*[~\s]*\d+|表\s*[~\s]*(?:所示|中|列出|给出)",
)
TEXT_NUMBER_RE = re.compile(r"(?<![\w.])(\d+\.\d+)(?![\w.])")


def _sig_digits(raw):
    return len(raw.replace(".", "").lstrip("0"))


def _transpositions(raw):
    """Values made by swapping two adjacent, different digits of ``raw`` (decimal point stays put)."""
    digits = [c for c in raw if c.isdigit()]
    dot = raw.index(".")
    out = set()
    for i in range(len(digits) - 1):
        if digits[i] == digits[i + 1]:
            continue
        swapped = digits[:]
        swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
        s = "".join(swapped)
        whole, frac = s[:dot], s[dot:]
        if len(whole) > 1 and whole.startswith("0"):
            continue  # 10.5 -> 01.5 is not a number anyone writes
        out.add(whole + "." + frac)
    out.discard(raw)
    return out


def table_number_index(tables, parse_cell_numbers, extract_numbers_from_text):
    """[(value, cleaned_str, where)] for every number in every table cell."""
    index = []
    for ti, table in enumerate(tables):
        for ri, row in enumerate(table):
            for ci, cell in enumerate(row):
                for value, cleaned in extract_numbers_from_text(cell or ""):
                    index.append((value, cleaned, f"表{ti + 1}行{ri + 1}列{ci + 1}"))
    return index


def _matches(value, decimals, table_values):
    """The text number equals or is a rounding of some table number (also as % <-> fraction)."""
    tol = _half(decimals) + EPS
    for v in table_values:
        for scaled in (v, v * 100, v / 100):
            if abs(scaled - value) <= tol:
                return True
    return False


def check_text_vs_tables(full_text, tables, source_label, parse_cell_numbers, extract_numbers_from_text):
    index = table_number_index(tables, parse_cell_numbers, extract_numbers_from_text)
    if not index:
        return []
    table_values = [v for v, _c, _w in index]
    by_string = {}
    for _v, cleaned, where in index:
        by_string.setdefault(cleaned, where)
    findings = []
    seen = set()
    for sentence in split_sentences(full_text):
        if not TABLE_MENTION_RE.search(sentence):
            continue
        cleaned_sentence = STAT_EXPR_RE.sub(" ", _normalize(sentence))
        for m in TEXT_NUMBER_RE.finditer(cleaned_sentence):
            raw = m.group(1)
            if _sig_digits(raw) < 3 or raw in seen:
                continue
            value = float(raw)
            if _matches(value, _decimals(raw), table_values):
                continue
            hits = [(cand, by_string[cand]) for cand in sorted(_transpositions(raw)) if cand in by_string]
            if not hits:
                continue
            seen.add(raw)
            cand, where = hits[0]
            findings.append({
                "type": "text_table_mismatch",
                "severity": "medium",
                "location": f"{source_label}#正文: {_snippet(sentence)}",
                "stat": {"text": raw, "table": cand, "tableCell": where},
                "explanation": (
                    f"正文引用表格时写的 {raw} 在所有表格里都找不到，而 {where} 有 {cand}——两者只差相邻两位数字对调，"
                    "很可能是抄写笔误。请核对正文和表格哪个是对的，并检查摘要、结论里是否也用了同一个数。"),
            })
    return findings
