# -*- coding: utf-8 -*-
"""Reported-statistics checks (zero dependencies).

1. statcheck-style recomputation: find "t(28) = 2.20, p = .036", "F(2, 57) = 3.12, p < .05",
   "χ2(1, N = 90) = 4.5, p = .03", "r(48) = .30, p = .03", "z = 2.1, p = .04" in the text,
   recompute the p value from the test statistic and degrees of freedom, and flag reported
   p values that cannot result from them (allowing for rounding of the statistic).
2. p-value caliper signal: exact p values that pile up just below .05.

Like every other check in geng-skills these are leads for the author to re-check, not
proof of misconduct: a mismatch is most often a typo in the statistic, df or p value.
"""

import math
import re

# ---------------------------------------------------------------------------
# distributions
# ---------------------------------------------------------------------------


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def betainc_regularized(a, b, x):
    """I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(log_front) * _betacf(b, a, 1.0 - x) / b


def p_t(t, df):
    """Two-tailed p for Student's t."""
    if df <= 0:
        return float("nan")
    return betainc_regularized(df / 2.0, 0.5, df / (df + t * t))


def p_f(f, df1, df2):
    if f <= 0 or df1 <= 0 or df2 <= 0:
        return 1.0
    return betainc_regularized(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def p_z(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


def p_r(r, df):
    if abs(r) >= 1:
        return 0.0
    return p_t(r * math.sqrt(df / (1.0 - r * r)), df)


def p_chi2(x, df, chi_square_pvalue):
    return chi_square_pvalue(x, df)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

_NUMV = r"[-−–]?\s*(?:\d+(?:\.\d+)?|\.\d+)"
_DF = r"\d+(?:\.\d+)?"
_P = r"[pP]\s*(?P<cmp><=|>=|=|<|>|≤|≥)\s*(?P<p>\d(?:\.\d+)?\s*[x×]\s*10\s*\^?\s*\{?[-−]\s*\d+\}?|\d(?:\.\d+)?[eE][-−]\d+|0?\.\d+|1(?:\.0+)?(?![\d.])|0(?![\d.]))"
_SEP = r"\s*[,，;；]?\s*(?:\w+\s*=\s*[^,，;；]{1,12}[,，;；]\s*){0,2}"  # allow e.g. "d = 0.5," between stat and p

STAT_PATTERNS = [
    ("t", re.compile(r"(?<![A-Za-z])t\s*\(\s*(?P<df1>" + _DF + r")\s*\)\s*=\s*(?P<stat>" + _NUMV + r")" + _SEP + _P)),
    ("F", re.compile(r"(?<![A-Za-z])F\s*\(\s*(?P<df1>" + _DF + r")\s*,\s*(?P<df2>" + _DF + r")\s*\)\s*=\s*(?P<stat>" + _NUMV + r")" + _SEP + _P)),
    ("r", re.compile(r"(?<![A-Za-z])r\s*\(\s*(?P<df1>" + _DF + r")\s*\)\s*=\s*(?P<stat>" + _NUMV + r")" + _SEP + _P)),
    ("chi2", re.compile(
        r"(?:χ\s*2|χ²|chi\s*-?\s*square[d]?|X2)\s*\(\s*(?P<df1>" + _DF + r")\s*(?:,\s*[Nn]\s*=\s*\d+\s*)?\)\s*=\s*(?P<stat>" + _NUMV + r")" + _SEP + _P,
        re.I)),
    ("z", re.compile(r"(?<![A-Za-z])[zZ]\s*=\s*(?P<stat>" + _NUMV + r")" + _SEP + _P)),
]


def normalize_stats_text(text):
    """Unify LaTeX math, full-width and typographic variants so one regex set fits all."""
    s = text or ""
    s = re.sub(r"\\chi\s*\^\s*\{?\s*2\s*\}?", "χ2", s)
    s = re.sub(r"\\(?:leq?|leqslant)\b", "≤", s)
    s = re.sub(r"\\(?:geq?|geqslant)\b", "≥", s)
    s = re.sub(r"\\(?:mathit|mathrm|textit|emph|text)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[,;:! ]|~|\$", " ", s)
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("\\times", "×")
    table = {"（": "(", "）": ")", "＝": "=", "＜": "<", "＞": ">", "，": ",", "；": ";", "−": "-", "–": "-", "Ｐ": "P", "χ²": "χ2"}
    for a, b in table.items():
        s = s.replace(a, b)
    s = re.sub(r"χ\s*\^?\s*2", "χ2", s)
    return s


def _to_float(token):
    token = token.replace(" ", "").replace("−", "-").replace("–", "-").replace("{", "").replace("}", "")
    m = re.match(r"^(\d(?:\.\d+)?)[x×]10\^?-(\d+)$", token)
    if m:
        return float(m.group(1)) * 10 ** -int(m.group(2))
    return float(token.replace("E", "e"))


def _decimals(token):
    token = token.strip()
    if re.search(r"[x×eE]", token):
        return None
    return len(token.split(".", 1)[1]) if "." in token else 0


def extract_reported_tests(text):
    s = normalize_stats_text(text)
    tests, spans = [], []
    for kind, pattern in STAT_PATTERNS:
        for m in pattern.finditer(s):
            if any(a <= m.start() < b for a, b in spans):
                continue  # e.g. "z = ..." inside an already matched test
            stat_token = m.group("stat").replace(" ", "")
            try:
                stat = _to_float(stat_token)
                p_value = _to_float(m.group("p"))
                df1 = float(m.group("df1")) if "df1" in m.groupdict() and m.group("df1") else None
                df2 = float(m.group("df2")) if "df2" in m.groupdict() and m.group("df2") else None
            except (ValueError, IndexError):
                continue
            spans.append((m.start(), m.end()))
            cmp_ = {"≤": "<=", "≥": ">="}.get(m.group("cmp"), m.group("cmp"))
            tests.append({
                "test": kind, "stat": stat, "statDecimals": _decimals(stat_token), "df1": df1, "df2": df2,
                "comparison": cmp_, "p": p_value, "pDecimals": _decimals(m.group("p")),
                "raw": re.sub(r"\s+", " ", m.group(0)).strip(), "offset": m.start(),
            })
    tests.sort(key=lambda t: t["offset"])
    return tests


# ---------------------------------------------------------------------------
# consistency
# ---------------------------------------------------------------------------


def recompute_p(test, chi_square_pvalue):
    kind, x, df1, df2 = test["test"], test["stat"], test["df1"], test["df2"]
    if kind == "t":
        return p_t(x, df1)
    if kind == "F":
        return p_f(x, df1, df2)
    if kind == "r":
        return p_r(x, df1)
    if kind == "chi2":
        return chi_square_pvalue(x, df1)
    return p_z(x)


def p_interval(test, chi_square_pvalue):
    """Recomputed p for the statistic rounded up/down by half a unit of its last decimal."""
    decimals = test["statDecimals"]
    half = 0.5 * 10 ** -(decimals if decimals is not None else 6)
    values = []
    for delta in (-half, 0.0, half):
        variant = dict(test)
        magnitude = abs(test["stat"]) + delta
        if magnitude < 0:
            magnitude = 0.0
        if test["test"] == "r":
            magnitude = min(magnitude, 0.999999)
        variant["stat"] = magnitude
        values.append(recompute_p(variant, chi_square_pvalue))
    return min(values), max(values), values[1]


def _consistent(lo, hi, cmp_, p, p_decimals):
    if cmp_ == "=":
        if p == 0:  # "p = .000" means p < .0005 after rounding
            return lo < 0.0005
        half = 0.5 * 10 ** -(p_decimals if p_decimals is not None else 6)
        return hi >= p - half and lo <= p + half
    if cmp_ in ("<", "<="):
        return lo < p or (cmp_ == "<=" and lo <= p)
    return hi > p or (cmp_ == ">=" and hi >= p)


def check_reported_tests(text, source_label, chi_square_pvalue, alpha=0.05):
    findings = []
    for test in extract_reported_tests(text):
        lo, hi, mid = p_interval(test, chi_square_pvalue)
        if any(math.isnan(v) for v in (lo, hi)):
            continue
        cmp_, p = test["comparison"], test["p"]
        if _consistent(lo, hi, cmp_, p, test["pDecimals"]):
            continue
        one_tailed_ok = test["test"] in ("t", "z", "r") and _consistent(lo / 2, hi / 2, cmp_, p, test["pDecimals"])
        if cmp_ in ("<", "<="):
            reported_sig = True if p <= alpha else None  # "p < .10" says nothing about the .05 decision
        elif cmp_ == "=":
            reported_sig = p <= alpha
        else:
            reported_sig = False if p >= alpha else None
        # conservative: only a decision error when no rounding of the statistic could explain it
        recomputed_sig = (lo < alpha) if reported_sig else (hi < alpha)
        decision_error = reported_sig is not None and reported_sig != recomputed_sig and not one_tailed_ok
        severity = "high" if decision_error else ("low" if one_tailed_ok else "medium")
        if one_tailed_ok:
            note = "按单尾检验可以对上；若文中未说明使用单尾检验，请补充说明或改正。"
        elif decision_error:
            note = "按报告的统计量与自由度重算，显著性结论（α=0.05）与报告的 p 值相反，属于'决策性不一致'。"
        else:
            note = "按报告的统计量与自由度重算的 p 值与报告值不符（已考虑统计量的四舍五入）。"
        findings.append({
            "type": "reported_p_inconsistent",
            "severity": severity,
            "location": f"{source_label}#正文:{test['raw'][:60]}",
            "stat": {
                "test": test["test"], "statistic": test["stat"], "df1": test["df1"], "df2": test["df2"],
                "reported": f"p {cmp_} {p:g}", "recomputedP": round(mid, 6),
                "recomputedRange": [round(lo, 6), round(hi, 6)], "decisionError": decision_error,
                "oneTailedConsistent": one_tailed_ok,
            },
            "explanation": note + " 常见原因是统计量、自由度或 p 值的笔误，请对照原始分析输出复核。",
        })
    return findings


EXACT_P_RE = re.compile(r"(?<![A-Za-z])[pP]\s*=\s*(0?\.\d{2,4})(?!\d)")


def _binom_upper(k, n, prob=0.5):
    return sum(math.comb(n, i) * prob ** i * (1 - prob) ** (n - i) for i in range(k, n + 1))


def check_p_caliper(text, source_label, min_n=8):
    """Exact p values bunched in (.04, .05) versus (.03, .04]: a weak p-hacking signal."""
    values = [float(v) for v in EXACT_P_RE.findall(normalize_stats_text(text))]
    upper = sum(1 for v in values if 0.04 < v < 0.05)
    lower = sum(1 for v in values if 0.03 < v <= 0.04)
    n = upper + lower
    if n < min_n:
        return None
    tail = _binom_upper(upper, n)
    if tail >= 0.05:
        return None
    return {
        "type": "p_value_bunching",
        "severity": "low",
        "location": f"{source_label}#正文全部精确 p 值",
        "stat": {"exactPValues": len(values), "in_0.04_0.05": upper, "in_0.03_0.04": lower, "binomialP": round(tail, 6)},
        "explanation": (
            "精确报告的 p 值在 0.04–0.05 之间明显多于 0.03–0.04 之间。真实效应下 p 值分布通常向小值偏，"
            "在 0.05 下方堆积可能提示选择性报告或反复调整分析（p-hacking）。这是很弱的整体信号，"
            "只用于提醒作者核对是否报告了全部分析、是否事先确定了分析方案。"
        ),
    }
