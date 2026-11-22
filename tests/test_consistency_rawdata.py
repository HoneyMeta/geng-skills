# -*- coding: utf-8 -*-
"""v1.3 checks: GRIMMER, GRIM/GRIMMER on means reported in the text, text vs table numbers,
raw data files (.csv / .xlsx)."""
import itertools
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import zipfile
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import consistency_check as cc  # noqa: E402
import geng_check as g  # noqa: E402
import rawdata_check  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "geng_check.py")


def _round(x, d):
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def _types(findings):
    return [f["type"] for f in findings]


def _docx(path, paragraphs, table_rows=()):
    body = "".join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in paragraphs)
    if table_rows:
        body += "<w:tbl>" + "".join(
            "<w:tr>" + "".join("<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % c for c in row) + "</w:tr>"
            for row in table_rows) + "</w:tbl>"
    doc = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           "<w:body>%s</w:body></w:document>" % body)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
    return path


# ---------------------------------------------------------------- GRIMMER


def test_grimmer_never_rejects_achievable_integer_data():
    for n in range(2, 6):
        for combo in itertools.combinations_with_replacement(range(0, 7), n):
            mean, sd = sum(combo) / n, statistics.stdev(combo)
            for d in (1, 2):
                res = cc.grimmer_check(_round(mean, d), _round(sd, d), n, d)
                assert res["consistent"], (combo, d, res)
            pop = statistics.pstdev(combo)
            assert cc.grimmer_check(_round(mean, 2), _round(pop, 2), n, 2, sample=False)["consistent"], combo


def test_grimmer_with_multi_item_scales():
    random.seed(3)
    for _ in range(300):
        n = random.randint(3, 9)
        items = random.choice([2, 3, 4])
        totals = [random.randint(items, 5 * items) for _ in range(n)]
        means = [t / items for t in totals]
        m, sd = _round(statistics.mean(means), 2), _round(statistics.stdev(means), 2)
        assert cc.grimmer_check(m, sd, n, 2, items=items)["consistent"], (totals, items)


def test_grimmer_flags_impossible_pairs():
    res = cc.grimmer_check(3.00, 0.85, 5, 2)
    assert res["grimConsistent"] and not res["consistent"]
    assert not cc.grimmer_check(3.47, 1.20, 20, 2)["grimConsistent"]
    assert cc.grimmer_check(3.0, 1.0, 5, 2)["consistent"]


def test_grimmer_cli():
    out = subprocess.run([sys.executable, SCRIPT, "grimmer", "--mean", "3.0", "--sd", "0.85", "--n", "5", "--decimals", "2"],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["consistent"] is False


# ---------------------------------------------------------------- means reported in the text


def test_reported_means_need_an_integer_data_context():
    likert = ("Participants rated satisfaction on a 5-point Likert scale. "
              "Satisfaction was high (M = 3.47, SD = 1.20, n = 20).")
    rt = "Reaction times were short (M = 3.47, SD = 1.20, n = 20)."
    found = cc.check_reported_means(likert, "x")
    assert _types(found) == ["grim_inconsistent"] and found[0]["severity"] == "medium"
    assert "整数数据" in found[0]["explanation"]
    assert cc.check_reported_means(rt, "x") == [], "continuous data must not be GRIM-tested"


def test_reported_means_try_item_means_before_flagging():
    # 3.47 * 20 is not an integer, but 3.47 * 20 * 5 items is: an item mean of a 5-item scale is fine
    text = "We used a 5-item questionnaire rated on a 5-point scale. The mean item score was M = 3.47 (SD = 0.61, n = 20)."
    assert cc.check_reported_means(text, "x") == []


def test_grimmer_and_chinese_notation():
    text = "问卷采用李克特 5 点计分。干预组的均分为 3.00（SD = 0.85，n = 5），对照组见下文。"
    found = cc.check_reported_means(text, "x")
    assert _types(found) == ["grimmer_inconsistent"]
    pm = "量表总分为 12.40 ± 2.10（n = 25）。"
    assert [(m, sd, n) for _s, _w, m, sd, n in cc.extract_reported_means(pm)] == [("12.40", "2.10", 25)]


def test_ambiguous_group_sizes_are_skipped():
    text = "Ratings on the 7-point scale were M = 3.47 (n = 20) and M = 4.10 (n = 25)."
    assert cc.check_reported_means(text, "x") == []


# ---------------------------------------------------------------- text vs table


def test_transposed_number_between_text_and_table():
    with tempfile.TemporaryDirectory() as d:
        path = _docx(os.path.join(d, "p.docx"), [
            "如表 1 所示，本文方法的准确率达到 95.3%，比基线高 4.1 个百分点。",
            "Table 1 also shows a recall of 88.24 and an F1 of 90.7.",
            "The abstract claims 95.3% without citing the table, which is not checked.",
        ], table_rows=[("Method", "Acc", "Recall", "F1"), ("Baseline", "89.4", "84.10", "86.7"), ("Ours", "93.5", "88.237", "90.66")])
        result = g.analyze(path)
    mism = [f for f in result["findings"] if f["type"] == "text_table_mismatch"]
    assert len(mism) == 1, mism
    assert mism[0]["stat"] == {"text": "95.3", "table": "93.5", "tableCell": "表1行3列2"}
    # 88.24 is a rounding of 88.237 and 90.7 of 90.66: both consistent


def test_statistics_in_table_sentences_are_ignored():
    text = "Table 2 shows the effect, t(28) = 2.20, p = .036."
    tables = [[["a", "2.02", "0.630"]]]
    assert cc.check_text_vs_tables(text, tables, "x", g.parse_cell_numbers, g.extract_numbers_from_text) == []


# ---------------------------------------------------------------- raw data files


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(header) + "\n")
        for row in rows:
            f.write(",".join(str(c) for c in row) + "\n")
    return path


def test_raw_csv_checks_and_false_positive_guards():
    random.seed(11)
    rows = []
    for i in range(1, 61):
        likert = [random.randint(1, 5) for _ in range(5)]
        weight = round(random.uniform(50, 90), 1)
        fabricated = random.randint(100, 900) + 0.5  # every value ends in 5
        age = random.randint(18, 65)
        rows.append([i] + likert + [weight, f"{fabricated:.1f}", age, f"{weight:.1f}"])
    rows[40][6:10] = rows[10][6:10]
    rows[40][1:6] = rows[10][1:6]  # a copied participant (continuous values included)
    rows[41][1:6] = rows[12][1:6]  # same Likert answers only: normal
    header = ["id", "q1", "q2", "q3", "q4", "q5", "weight", "score", "age", "weight_copy"]
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(os.path.join(d, "raw.csv"), header, rows)
        findings, info = rawdata_check.run_raw_data_checks(path, g.RAW_DATA_HELPERS)
    types = _types(findings)
    assert info["sheets"][0]["idColumnsSkipped"] == ["A"]
    terminal = [f for f in findings if f["type"] == "terminal_digit_uniformity"]
    assert [f["location"] for f in terminal] == ["raw.csv#sheet1!H(score)"], "ages and Likert answers are not digit-tested"
    assert types.count("duplicate_column") == 1
    dup_rows = [f for f in findings if f["type"] == "duplicate_row"]
    assert len(dup_rows) == 1 and "第 12、42 行" in dup_rows[0]["location"], "spreadsheet row numbers (header = row 1)"


def _write_xlsx(path, rows):
    shared, cells_xml = [], []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, value in enumerate(row):
            ref = chr(65 + ci) + str(ri)
            if isinstance(value, str):
                shared.append(value)
                cells.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, len(shared) - 1))
            else:
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, value))
        cells_xml.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>' % ns)
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst %s>%s</sst>' % (ns, "".join("<si><t>%s</t></si>" % s for s in shared)))
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, "".join(cells_xml)))
    return path


def test_raw_xlsx_reader_cleans_float_noise_and_restores_trailing_zeros():
    rows = [["编号", "浓度"], [1, "0.30000000000000004"], [2, 3.4], [3, 3.45]]
    rows = [[r[0], float(r[1])] if isinstance(r[1], str) and r[1][0].isdigit() else r for r in rows]
    with tempfile.TemporaryDirectory() as d:
        path = _write_xlsx(os.path.join(d, "raw.xlsx"), rows)
        sheets = rawdata_check.read_raw_file(path)
    name, data = sheets[0]
    assert name == "Data" and data[0] == ["编号", "浓度"]
    names, columns, start = rawdata_check.sheet_columns(data)
    assert start == 1 and names[1] == "浓度"
    assert [c for _r, _v, c in columns[1]] == ["0.30", "3.40", "3.45"]
    assert rawdata_check.is_id_column(names[0], columns[0])


def test_data_command_with_raw_file():
    random.seed(5)
    rows = [["sample", "a", "b", "c", "d"]]
    for i in range(1, 41):
        rows.append([i] + [round(random.uniform(1, 9), 3) for _ in range(4)])
    rows[30][1:] = rows[5][1:]
    with tempfile.TemporaryDirectory() as d:
        raw = _write_xlsx(os.path.join(d, "raw.xlsx"), rows)
        paper = _docx(os.path.join(d, "p.docx"), ["正文没有表格。"])
        out = os.path.join(d, "findings.json")
        run = subprocess.run([sys.executable, SCRIPT, "data", "--input", paper, "--data-file", raw, "--out", out],
                             capture_output=True, text=True, encoding="utf-8")
        assert run.returncode == 0, run.stderr
        assert "原始数据 raw.xlsx" in run.stdout
        result = json.load(open(out, encoding="utf-8"))
    assert result["stats"]["rawDataFiles"][0]["sheets"][0]["rows"] == 40
    dup = [f for f in result["findings"] if f["type"] == "duplicate_row"]
    assert dup and dup[0]["location"].startswith("raw.xlsx#Data")
    assert all(f["id"].startswith("data-") for f in result["findings"])
