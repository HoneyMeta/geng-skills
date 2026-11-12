# -*- coding: utf-8 -*-
"""Text and reference-list checks.

Offline (always run by `data`):
  - tortured phrases: nonsense synonyms produced by paraphrasing tools ("profound learning"
    for deep learning), the signal used by the Problematic Paper Screener;
  - numeric citations that point past the end of the reference list, entries never cited,
    LaTeX keys cited but not defined;
  - duplicate reference entries.

Online (only with `data --verify-refs`, the user must agree first):
  - DOI resolves on Crossref and its title matches the entry (fabricated / hallucinated
    references), the cited work has a retraction or expression-of-concern notice;
  - English entries without DOI are looked up by bibliographic query.

Every result is a lead to re-check, never proof of misconduct.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# tortured phrases
# ---------------------------------------------------------------------------

# Curated, high-precision subset of phrases documented by Cabanac et al. and the Problematic
# Paper Screener. Each maps to the expected term.
TORTURED_PHRASES = {
    "counterfeit consciousness": "artificial intelligence",
    "man-made consciousness": "artificial intelligence",
    "profound learning": "deep learning",
    "profound neural organization": "deep neural network",
    "neural organization": "neural network",
    "irregular woodland": "random forest",
    "arbitrary woodland": "random forest",
    "choice tree": "decision tree",
    "help vector machine": "support vector machine",
    "backing vector machine": "support vector machine",
    "mean square blunder": "mean square error",
    "mean squared blunder": "mean squared error",
    "straight relapse": "linear regression",
    "strategic relapse": "logistic regression",
    "hereditary calculation": "genetic algorithm",
    "molecule swarm advancement": "particle swarm optimization",
    "colossal information": "big data",
    "enormous information": "big data",
    "flag to commotion": "signal to noise",
    "sign to clamor": "signal to noise",
    "haphazard access memory": "random access memory",
    "cloud figuring": "cloud computing",
    "web of things": "internet of things",
    "remote sensor organization": "wireless sensor network",
    "facial acknowledgment": "facial recognition",
    "picture acknowledgment": "image recognition",
    "discourse acknowledgment": "speech recognition",
    "bosom malignant growth": "breast cancer",
    "bosom disease": "breast cancer",
    "lung malignant growth": "lung cancer",
    "kidney disappointment": "kidney failure",
    "heart disappointment": "heart failure",
    "circulatory strain": "blood pressure",
    "p esteem": "p value",
    "standard deviation blunder": "standard error",
}
_TORTURED_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(TORTURED_PHRASES, key=len, reverse=True)) + r")\b", re.I)


def check_tortured_phrases(text, source_label):
    hits = {}
    for m in _TORTURED_RE.finditer(text or ""):
        phrase = m.group(1).lower()
        hits[phrase] = hits.get(phrase, 0) + 1
    if not hits:
        return []
    return [{
        "type": "tortured_phrases",
        "severity": "medium" if len(hits) >= 2 else "low",
        "location": f"{source_label}#正文",
        "stat": {"phrases": [{"phrase": p, "expected": TORTURED_PHRASES[p], "count": c} for p, c in sorted(hits.items())]},
        "explanation": (
            "出现'扭曲短语'（tortured phrases）：用改写/洗稿工具把专业术语换成字面同义词后的产物，"
            "如把 deep learning 写成 profound learning。Problematic Paper Screener 用它筛查论文工厂稿件。"
            "如是本人撰写，请改回规范术语；如有段落来自他人或代写，请核实来源。"
        ),
    }]


# ---------------------------------------------------------------------------
# reference list extraction
# ---------------------------------------------------------------------------

REF_HEADING_RE = re.compile(r"^\s*(?:(?:\d+(?:\.\d+)*|[IVX]+|第[一二三四五六七八九十\d]+章)[.、．]?\s*)?(?:参考文献|参 考 文 献|references|bibliography|literature cited|works cited)\s*[:：]?\s*$", re.I)
REF_END_RE = re.compile(r"^\s*(?:致\s*谢|附\s*录|作者简介|攻读.{0,12}期间|acknowledg(?:e)?ments?|appendix|supplementary)", re.I)
ENTRY_NUMBER_RE = re.compile(r"^\s*(?:\[(\d{1,4})\]|［(\d{1,4})］|(\d{1,4})[.、]\s)")
CITE_BRACKET_RE = re.compile(r"[\[［]\s*(\d{1,4}(?:\s*[-–—~,，、]\s*\d{1,4})*)\s*[\]］]")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;\"'<>\]}]+)", re.I)


def split_plain_references(full_text):
    """(body_text, entries) from text that has one paragraph per line (DOCX / TXT)."""
    lines = (full_text or "").split("\n")
    start = None
    for i, line in enumerate(lines):
        if REF_HEADING_RE.match(line):
            start = i  # keep the last heading: a TOC entry "参考文献" may come first
    if start is None:
        return full_text or "", []
    entries = []
    for line in lines[start + 1:]:
        if REF_END_RE.match(line):
            break
        if line.strip():
            entries.append(line.strip())
    return "\n".join(lines[:start]), entries


def expand_citation_numbers(group):
    numbers = []
    for part in re.split(r"\s*[,，、]\s*", group):
        rng = re.split(r"\s*[-–—~]\s*", part)
        try:
            if len(rng) == 2:
                a, b = int(rng[0]), int(rng[1])
                if 0 < a <= b and b - a <= 60:
                    numbers.extend(range(a, b + 1))
            elif len(rng) == 1 and rng[0]:
                numbers.append(int(rng[0]))
        except ValueError:
            continue
    return numbers


def entry_number(entry, index):
    m = ENTRY_NUMBER_RE.match(entry)
    if m:
        return int(next(g for g in m.groups() if g))
    return index


def _norm_entry(entry):
    entry = ENTRY_NUMBER_RE.sub("", entry)
    return re.sub(r"[^\w]+", " ", entry.lower()).strip()


def _words(text):
    return {w for w in re.findall(r"[a-z\u4e00-\u9fff]{3,}|[\u4e00-\u9fff]{2,}", (text or "").lower())}


def check_duplicate_entries(entries, source_label):
    findings = []
    seen = []
    for i, entry in enumerate(entries):
        doi = (DOI_RE.search(entry).group(1).rstrip(".").lower() if DOI_RE.search(entry) else None)
        words = _words(_norm_entry(entry))
        for j, (other_doi, other_words) in enumerate(seen):
            same_doi = doi and other_doi and doi == other_doi
            overlap = len(words & other_words) / max(1, len(words | other_words)) if words and other_words else 0
            if same_doi or (len(words) >= 5 and overlap >= 0.9):
                findings.append({
                    "type": "duplicate_reference",
                    "severity": "low",
                    "location": f"{source_label}#参考文献 第{j + 1}条 与 第{i + 1}条",
                    "stat": {"sameDoi": bool(same_doi), "wordOverlap": round(overlap, 3)},
                    "explanation": "参考文献列表里同一文献出现两次，请合并并检查正文引用编号。",
                })
                break
        seen.append((doi, words))
    return findings


def check_numeric_citations(body_text, entries, source_label):
    cited = []
    for m in CITE_BRACKET_RE.finditer(body_text or ""):
        cited.extend(expand_citation_numbers(m.group(1)))
    if not cited or not entries:
        return []
    numbers = {entry_number(e, i + 1) for i, e in enumerate(entries)}
    findings = []
    beyond = sorted({n for n in cited if n not in numbers})
    if beyond:
        findings.append({
            "type": "citation_without_reference",
            "severity": "medium",
            "location": f"{source_label}#正文引用",
            "stat": {"citedNumbers": beyond[:30], "referenceCount": len(entries)},
            "explanation": "正文引用的编号在参考文献列表中找不到对应条目，可能是列表被删减、编号错位或引用了不存在的文献。",
        })
    never = sorted(numbers - set(cited))
    if never and len(never) <= max(3, len(entries) // 2):
        findings.append({
            "type": "reference_never_cited",
            "severity": "low",
            "location": f"{source_label}#参考文献",
            "stat": {"entries": never[:40], "referenceCount": len(entries)},
            "explanation": "这些参考文献在正文中没有被引用。多数期刊要求列表只包含正文引用过的文献；也请确认没有'凑数'引用。",
        })
    return findings


# ---------------------------------------------------------------------------
# LaTeX references
# ---------------------------------------------------------------------------

LATEX_CITE_RE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*|cite)\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\}|$)", re.S)
BIB_FILES_RE = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n\s*@\w+\s*\{|\Z)", re.S)


def _bib_field(body, name):
    m = re.search(r"(?<![A-Za-z])" + name + r"\s*=\s*[{\"](.+?)[}\"]\s*,?\s*(?:\n|$)", body, re.I | re.S)
    return re.sub(r"[{}]", "", m.group(1)).strip() if m else ""


def parse_latex_references(content, base_dir):
    """Returns (cited_keys, entries) where entries = [{key, text, doi, title}]."""
    cited = []
    for m in LATEX_CITE_RE.finditer(content):
        cited.extend(k.strip() for k in m.group(1).split(",") if k.strip())
    entries = []
    for m in BIBITEM_RE.finditer(content):
        text = re.sub(r"\s+", " ", re.sub(r"\\[a-zA-Z]+\s*|[{}]", " ", m.group(2))).strip()
        doi = DOI_RE.search(m.group(2))
        entries.append({"key": m.group(1).strip(), "text": text, "doi": doi.group(1).rstrip(".") if doi else "", "title": "", "source": "bibitem"})
    for m in BIB_FILES_RE.finditer(content):
        for name in m.group(1).split(","):
            name = name.strip()
            path = os.path.join(base_dir, name if name.endswith(".bib") else name + ".bib")
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                bib = f.read()
            for e in BIB_ENTRY_RE.finditer(bib):
                if e.group(1).lower() in ("comment", "string", "preamble"):
                    continue
                body = e.group(3)
                title = _bib_field(body, "title")
                parts = [_bib_field(body, "author"), title, _bib_field(body, "journal") or _bib_field(body, "booktitle"), _bib_field(body, "year")]
                entries.append({"key": e.group(2).strip(), "text": ". ".join(p for p in parts if p), "doi": _bib_field(body, "doi"), "title": title, "source": "bib"})
    return cited, entries


def check_latex_citations(cited, entries, source_label):
    if not entries:
        return []
    keys = {e["key"] for e in entries}
    findings = []
    undefined = sorted({k for k in cited if k not in keys})
    if undefined:
        findings.append({
            "type": "citation_without_reference",
            "severity": "medium",
            "location": f"{source_label}#\\cite",
            "stat": {"undefinedKeys": undefined[:30]},
            "explanation": "\\cite 的这些 key 在 .bib / thebibliography 中没有定义，编译后会显示 [?]。",
        })
    bibitem_keys = [e["key"] for e in entries if e["source"] == "bibitem"]
    unused = [k for k in bibitem_keys if k not in set(cited)]
    if unused and cited and "*" not in cited:
        findings.append({
            "type": "reference_never_cited",
            "severity": "low",
            "location": f"{source_label}#thebibliography",
            "stat": {"keys": unused[:40]},
            "explanation": "thebibliography 中这些条目在正文没有被 \\cite，请删除或补引用。",
        })
    return findings


# ---------------------------------------------------------------------------
# Crossref verification (opt-in, network)
# ---------------------------------------------------------------------------

USER_AGENT = "geng-skills/1.2 (+https://github.com/HoneyMeta/geng-skills)"


def crossref_get(url, mailto=None, timeout=20):
    """GET a Crossref API URL. Returns (status, json_or_None)."""
    if mailto:
        url += ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(mailto)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def _title_match(title, entry_text):
    title_words = _words(title)
    if not title_words:
        return 1.0
    return len(title_words & _words(entry_text)) / len(title_words)


def _is_english_entry(text):
    letters = re.findall(r"[A-Za-z]", text)
    return len(text) >= 40 and len(letters) / max(1, len(re.sub(r"\s", "", text))) >= 0.7


NOTICE_SEVERITY = {"retraction": "high", "withdrawal": "high", "removal": "high", "expression_of_concern": "medium"}


def verify_references(entries, source_label, fetch=crossref_get, mailto=None, max_queries=150, pause=0.15):
    """entries: [{"text", "doi"}]. Returns findings; the network is only touched here."""
    findings = []
    queries = 0
    for index, entry in enumerate(entries, 1):
        if queries >= max_queries:
            break
        text, doi = entry.get("text", ""), (entry.get("doi") or "").strip().rstrip(".")
        label = f"{source_label}#参考文献 第{index}条"
        if doi:
            queries += 1
            status, data = fetch("https://api.crossref.org/works/" + urllib.parse.quote(doi), mailto)
            if status == 404:
                findings.append({
                    "type": "reference_doi_not_found", "severity": "medium", "location": label,
                    "stat": {"doi": doi},
                    "explanation": "这个 DOI 在 Crossref 查不到。可能是 DOI 抄错，也可能是虚构/AI 幻觉生成的参考文献（DataCite 等其他注册机构的 DOI 也会查不到，请到 doi.org 确认）。",
                })
            elif status == 200 and data:
                message = data.get("message", {})
                titles = message.get("title") or []
                title = titles[0] if titles else ""
                ratio = _title_match(title, text + " " + entry.get("title", ""))
                if title and ratio < 0.5 and _is_english_entry(text):
                    findings.append({
                        "type": "reference_doi_mismatch", "severity": "medium", "location": label,
                        "stat": {"doi": doi, "crossrefTitle": title[:200], "titleWordOverlap": round(ratio, 2)},
                        "explanation": "DOI 对应的论文标题与该条参考文献不一致：DOI 可能填错，或条目是拼凑出来的。",
                    })
                queries += 1
                status2, notices = fetch("https://api.crossref.org/works?filter=updates:" + urllib.parse.quote(doi) + "&rows=5", mailto)
                for item in ((notices or {}).get("message", {}).get("items") or []):
                    for update in item.get("update-to") or []:
                        kind = str(update.get("type", "")).lower().replace("-", "_").replace(" ", "_")
                        if str(update.get("DOI", "")).lower() == doi.lower() and kind in NOTICE_SEVERITY:
                            findings.append({
                                "type": "cited_retracted_work", "severity": NOTICE_SEVERITY[kind], "location": label,
                                "stat": {"doi": doi, "notice": kind, "noticeDoi": item.get("DOI")},
                                "explanation": "被引用的论文已有撤稿/撤回/关注声明。若引用它作为支持证据，请替换或在文中说明。",
                            })
            time.sleep(pause)
        elif _is_english_entry(text):
            queries += 1
            query = urllib.parse.quote(re.sub(r"^\s*\[?\d+[\].、]?\s*", "", text)[:300])
            status, data = fetch("https://api.crossref.org/works?rows=1&select=DOI,title&query.bibliographic=" + query, mailto)
            items = ((data or {}).get("message", {}) or {}).get("items") or []
            title = (items[0].get("title") or [""])[0] if items else ""
            if status == 200 and (not title or _title_match(title, text) < 0.6):
                findings.append({
                    "type": "reference_not_found", "severity": "low", "location": label,
                    "stat": {"bestCrossrefTitle": title[:200]},
                    "explanation": "在 Crossref 找不到与该条目标题相符的记录。书籍、学位论文、网页、会议论文常查不到，属正常；若应为期刊论文，请核实它是否真实存在。",
                })
            time.sleep(pause)
        if index % 20 == 0:
            print(f"[refs] 已核对 {index}/{len(entries)} 条参考文献", file=sys.stderr)
    return findings
