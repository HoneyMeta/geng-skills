# -*- coding: utf-8 -*-
"""geng-skills 冒烟测试：构造含已知造假信号的 LaTeX/DOCX，断言能被检出。

运行：
    python -m pytest tests/test_smoke.py      # 有 pytest
    python tests/test_smoke.py                # 无 pytest，直接跑
"""
import os
import sys
import zipfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import geng_check as g  # noqa: E402


def _make_tex(path):
    tex = r"""
\documentclass{article}
\begin{document}
\begin{tabular}{lcccc}
S1 & 12.5 & 24.5 & 36.5 & 48.5 \\
S2 & 13.5 & 25.5 & 37.5 & 49.5 \\
S3 & 14.5 & 26.5 & 38.5 & 50.5 \\
S4 & 15.5 & 27.5 & 39.5 & 51.5 \\
S5 & 16.5 & 28.5 & 40.5 & 52.5 \\
\end{tabular}
\begin{tabular}{lccc}
A & 7.1 & 8.2 & 9.3 \\
B & 7.1 & 8.2 & 9.3 \\
\end{tabular}
\end{document}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(tex)


def _make_docx(path):
    rows = [("S%d" % i, 10.0 + i, 20.0 + i, 30.0 + i) for i in range(1, 9)]
    body = '<w:p><w:r><w:t>mean 3.45</w:t></w:r></w:p><w:tbl>'
    for r in rows:
        body += "<w:tr>" + "".join(
            "<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % c for c in r) + "</w:tr>"
    body += "</w:tbl>"
    doc = ('<?xml version="1.0"?><w:document '
           'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % body)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\n")
        z.writestr("word/media/image2.png", b"\x89PNG\r\n\x1a\n")  # exact dup


def test_latex_data():
    with tempfile.TemporaryDirectory() as d:
        tex = os.path.join(d, "p.tex")
        _make_tex(tex)
        full, tables, label = g.load_source(tex)
        res = g.run_data_forensics(full, tables, label)
        types = {f["type"] for f in res["findings"]}
        assert "arithmetic_progression" in types, types
        assert "duplicate_row" in types, types
        print("[ok] latex:", types)


def test_docx_data_and_images():
    with tempfile.TemporaryDirectory() as d:
        docx = os.path.join(d, "p.docx")
        _make_docx(docx)
        full, tables, label = g.load_source(docx)
        res = g.run_data_forensics(full, tables, label)
        types = {f["type"] for f in res["findings"]}
        assert "arithmetic_progression" in types, types
        man = g.run_image_extraction(docx, os.path.join(d, "imgs"))
        assert man["imageCount"] == 2
        assert len(man["exactDuplicateFiles"]) == 1, man["exactDuplicateFiles"]
        print("[ok] docx:", types, "| exactDup ok")


def test_grim():
    assert g.grim_check(3.45, 20, 2)["consistent"] is True
    assert g.grim_check(2.37, 10, 2)["consistent"] is False
    print("[ok] grim")


def test_sprite():
    # Likert 1-7, mean=2, n=20: SD=3.0 impossible, SD=1.0 possible
    assert g.sprite_lite(2.0, 3.0, 20, 1, 7)["consistent"] is False
    assert g.sprite_lite(2.0, 1.0, 20, 1, 7)["consistent"] is True
    assert g.sprite_lite(8.0, 1.0, 20, 1, 7)["consistent"] is False  # mean out of bounds
    print("[ok] sprite-lite")


def test_decimal_inconsistency():
    nums = [(float(s), s) for s in
            ["1.5", "2.25", "3.125", "4.7", "5.42", "6.1234", "7.8",
             "8.33", "9.001", "10.5", "11.25", "12.7777"]]
    f = g.check_decimal_consistency(nums, "loc")
    assert f is not None and f["type"] == "decimal_place_inconsistency", f
    # uniform precision should NOT flag
    uni = [(float(s), s) for s in ["%.2f" % (i + 0.11) for i in range(12)]]
    assert g.check_decimal_consistency(uni, "loc") is None
    print("[ok] decimal-inconsistency")


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx_with(path, body, rels=None, media=()):
    doc = '<?xml version="1.0"?><w:document %s %s><w:body>%s</w:body></w:document>' % (
        W, 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
           'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"', body)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        if rels:
            z.writestr("word/_rels/document.xml.rels",
                       '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       + "".join('<Relationship Id="%s" Type="x/image" Target="%s"/>' % kv for kv in rels)
                       + "</Relationships>")
        for name in media:
            z.writestr(name, b"\x89PNG\r\n\x1a\n" + name.encode())


def _tc(text):
    return "<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % text


def test_docx_table_numbers_not_double_counted():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.docx")
        _docx_with(p, '<w:p><w:r><w:t>正文 12.5</w:t></w:r></w:p><w:tbl><w:tr>%s%s</w:tr></w:tbl>'
                   % (_tc("33.3"), _tc("44.4")))
        full, tables, _ = g.load_source(p)
        assert "33.3" not in full and "12.5" in full, full
        assert tables == [[["33.3", "44.4"]]], tables
    print("[ok] no double counting")


def test_mean_sd_cells_are_parsed():
    ts = g.table_numeric_series([["A", "12.3 ± 1.2", "45.6 (2.1)"], ["B", "13.1±0.9", "44.0 (1.8)"]])
    assert [c for (_v, c) in ts["columns"][1]] == ["12.3", "13.1"]
    assert [c for (_v, c) in ts["sdColumns"][1]] == ["1.2", "0.9"]
    assert [c for (_v, c) in ts["sdColumns"][2]] == ["2.1", "1.8"]
    print("[ok] mean±sd cells")


def test_latex_layout_numbers_are_ignored():
    with tempfile.TemporaryDirectory() as d:
        main = os.path.join(d, "main.tex")
        with open(os.path.join(d, "tab.tex"), "w", encoding="utf-8") as f:
            f.write(r"""\begin{tabular*}{\textwidth}{l@{\extracolsep{\fill}}p{3cm}c}
\toprule
 & \multicolumn{2}{c}{Group A} \\
\cmidrule(lr){2-3}
x & 1.25 & 3.5 $\pm$ 0.2 \\[2pt]
y & 2.75 & 4.5 $\pm$ 0.3 \\
\end{tabular*}""")
        with open(main, "w", encoding="utf-8") as f:
            f.write("\\documentclass[12pt]{article}\\usepackage[margin=2.5cm]{geometry}\n"
                    "\\begin{document}\\includegraphics[width=0.5\\textwidth]{a}\\input{tab}\\end{document}")
        full, tables, _ = g.load_source(main)
        ts = g.table_numeric_series(tables[0])
        nums = [c for col in ts["columns"] for (_v, c) in col]
        assert nums == ["1.25", "2.75", "3.5", "4.5"], nums
        assert [c for (_v, c) in ts["sdColumns"][2]] == ["0.2", "0.3"]
        assert g.extract_numbers_from_text(full) == [], full   # 12pt/2.5cm/0.5\textwidth 都不算数据
    print("[ok] latex layout noise + \\input")


def test_duplicate_block_reported_once():
    a = [(float(x), x) for x in ["1.1", "2.3", "3.7", "4.2", "5.9", "6.4", "7.8", "8.6"]]
    b = [(9.9, "9.9")] + a
    found = g.check_duplicate_blocks([("t#列1", a), ("t#列2", b)])
    assert len(found) == 1 and found[0]["stat"]["length"] == 8, found
    print("[ok] duplicate block merged")


def test_year_header_and_index_columns_not_flagged():
    years = [["", "2019", "2020", "2021", "2022", "2023"], ["", "2019", "2020", "2021", "2022", "2023"]]
    idx = [[str(i), "%.2f" % (i * 1.37 + 0.11 * (i % 3))] for i in range(1, 9)]
    for table in (years, idx):
        res = g.run_data_forensics("", [table], "t")
        assert not [f for f in res["findings"] if f["type"] in ("arithmetic_progression", "duplicate_row")], res["findings"]
    print("[ok] years/index not flagged")


def test_grim_uses_half_up_interval():
    # 17/8 = 2.125 -> 四舍五入报告为 2.13；Python round() 会给 2.12，旧实现因此误判
    assert g.grim_check(2.13, 8, 2)["consistent"] is True
    assert g.grim_check(3.47, 150, 2)["informative"] is False
    assert g.grim_check(3.44, 20, 2)["consistent"] is False
    # 5 道题量表，n=10：均分粒度 1/50
    assert g.grim_check(3.42, 10, 2, items=5)["consistent"] is True
    assert g.grim_check(3.43, 10, 2, items=5)["consistent"] is False
    print("[ok] grim rounding")


def test_docx_reused_image_detected_via_relationships():
    blip = '<w:p><w:r><w:drawing><a:blip r:embed="%s"/></w:drawing></w:r></w:p>'
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.docx")
        _docx_with(p, blip % "rId5" + blip % "rId5" + blip % "rId6",
                   rels=[("rId5", "media/image1.png"), ("rId6", "media/image2.png")],
                   media=["word/media/image1.png", "word/media/image2.png"])
        man = g.run_image_extraction(p, os.path.join(d, "imgs"))
        assert man["reusedImages"] == [{"file": "image1.png", "references": 2, "contexts": []}], man["reusedImages"]
        assert man["exactDuplicateFiles"] == []
    print("[ok] docx reused image")


def test_small_sample_terminal_digit_exact_test():
    nums = [(float(s), s) for s in ["%d.5" % (10 + i) for i in range(18)] + ["11.2", "13.4", "17.1", "19.8"]]
    f = g.check_terminal_digits(nums, "loc")
    assert f and f["severity"] == "high" and f["stat"]["method"].startswith("exact"), f
    print("[ok] small-sample terminal digits")


if __name__ == "__main__":
    test_latex_data()
    test_docx_data_and_images()
    test_grim()
    test_sprite()
    test_decimal_inconsistency()
    test_docx_table_numbers_not_double_counted()
    test_mean_sd_cells_are_parsed()
    test_latex_layout_numbers_are_ignored()
    test_duplicate_block_reported_once()
    test_year_header_and_index_columns_not_flagged()
    test_grim_uses_half_up_interval()
    test_docx_reused_image_detected_via_relationships()
    test_small_sample_terminal_digit_exact_test()
    print("ALL SMOKE TESTS PASSED")
