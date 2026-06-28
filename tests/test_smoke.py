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


if __name__ == "__main__":
    test_latex_data()
    test_docx_data_and_images()
    test_grim()
    test_sprite()
    test_decimal_inconsistency()
    print("ALL SMOKE TESTS PASSED")
