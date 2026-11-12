# -*- coding: utf-8 -*-
"""v1.2 checks: reported-statistics recomputation, p-value bunching, tortured phrases,
reference list checks (offline + mocked Crossref), perceptual near-duplicate images."""
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import geng_check as g  # noqa: E402
import image_similarity  # noqa: E402
import refs_check  # noqa: E402
import stats_check  # noqa: E402

from test_smoke import _docx_with  # noqa: E402


def _p(text):
    return "<w:p><w:r><w:t xml:space=\"preserve\">%s</w:t></w:r></w:p>" % text


def _types(findings):
    return [f["type"] for f in findings]


# ---------------------------------------------------------------- statistics


def test_distribution_identities():
    chi = g.chi_square_pvalue
    # F(1, df) = t(df)^2 and chi2(1) = z^2
    assert abs(stats_check.p_f(3.2404 ** 2, 1, 200) - stats_check.p_t(3.2404, 200)) < 1e-9
    assert abs(chi(2.1 ** 2, 1) - stats_check.p_z(2.1)) < 1e-9
    assert abs(stats_check.p_t(2.20, 28) - 0.0362) < 5e-4
    assert abs(stats_check.p_z(1.96) - 0.05) < 5e-4


def test_consistent_results_are_not_flagged():
    text = ("Groups differed, t(28) = 2.20, p = .036. The interaction was F(2, 57) = 3.12, p = .052; "
            "χ2(1, N = 90) = 4.50, p = .034 and r(48) = .30, p = .034. Overall z = 2.10, p = .036.")
    assert len(stats_check.extract_reported_tests(text)) == 5
    assert stats_check.check_reported_tests(text, "x", g.chi_square_pvalue) == []


def test_inconsistent_and_decision_errors_are_flagged():
    text = "Effect A: t(28) = 2.20, p = .018. Effect B: F(2, 57) = 3.12, p < .05. Effect C: t(40) = 1.10, p = .14."
    findings = stats_check.check_reported_tests(text, "x", g.chi_square_pvalue)
    by_raw = {f["location"].split(":", 1)[1]: f for f in findings}
    assert len(findings) == 3
    a = next(f for raw, f in by_raw.items() if raw.startswith("t(28)"))
    assert a["severity"] == "low" and a["stat"]["oneTailedConsistent"], "p = .018 matches a one-tailed test"
    b = next(f for raw, f in by_raw.items() if raw.startswith("F(2"))
    assert b["severity"] == "high" and b["stat"]["decisionError"]
    c = next(f for raw, f in by_raw.items() if raw.startswith("t(40)"))
    assert c["severity"] == "low" or c["severity"] == "medium"


def test_latex_and_chinese_notation():
    latex = r"we found $t(28) = 2.20$, $p = .036$ and $\chi^2(1) = 4.5$, $p \leq .01$."
    found = stats_check.check_reported_tests(latex, "x", g.chi_square_pvalue)
    assert len(stats_check.extract_reported_tests(latex)) == 2
    assert _types(found) == ["reported_p_inconsistent"] and found[0]["stat"]["test"] == "chi2"
    chinese = "两组差异显著（t（28）＝2.20，P＜0.05），且 F（2，57）＝3.12，P＜0.01。"
    found = stats_check.check_reported_tests(chinese, "x", g.chi_square_pvalue)
    assert len(found) == 1 and found[0]["stat"]["test"] == "F" and found[0]["severity"] == "high"


def test_p_value_bunching_below_05():
    bunched = " ".join(f"p = .0{v}" for v in [41, 42, 43, 44, 45, 46, 47, 48, 49, 46, 44, 32])
    finding = stats_check.check_p_caliper(bunched, "x")
    assert finding and finding["type"] == "p_value_bunching" and finding["severity"] == "low"
    spread = " ".join(f"p = .0{v}" for v in [31, 32, 33, 34, 35, 36, 41, 42, 12, 22])
    assert stats_check.check_p_caliper(spread, "x") is None


# ---------------------------------------------------------------- text + references


def test_tortured_phrases():
    text = "We apply profound learning and an irregular woodland classifier to bosom malignant growth images."
    finding = refs_check.check_tortured_phrases(text, "x")[0]
    phrases = {p["phrase"]: p["expected"] for p in finding["stat"]["phrases"]}
    assert phrases["profound learning"] == "deep learning" and "irregular woodland" in phrases
    assert finding["severity"] == "medium"
    assert refs_check.check_tortured_phrases("We apply deep learning to random forests.", "x") == []


def _paper_docx(path):
    body = "".join([
        _p("1 引言"),
        _p("已有研究[1]表明该方法有效[2-3]，后续工作见[7]。"),
        _p("参考文献"),
        _p("[1] Smith J. Deep acoustic recognition. Journal of Sound, 2020. doi:10.1000/abc.123"),
        _p("[2] Wang L. Ship noise classification with CNN. Ocean Eng, 2021."),
        _p("[3] Li H. A survey of underwater target recognition. Acta Acustica, 2019."),
        _p("[4] Zhao Q. Never cited work on sonar arrays. IEEE JOE, 2018."),
        _p("[5] Smith J. Deep acoustic recognition. Journal of Sound, 2020. doi:10.1000/ABC.123"),
        _p("致谢"),
        _p("感谢导师 2023 年的指导。"),
    ])
    _docx_with(path, body)


def test_docx_reference_checks_offline():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.docx")
        _paper_docx(path)
        info = g.load_reference_info(path)
        assert len(info["entries"]) == 5 and "引言" in info["bodyText"]
        findings = g.run_text_checks(g.load_source(path)[0], info, "p.docx")
        types = _types(findings)
        missing = next(f for f in findings if f["type"] == "citation_without_reference")
        assert missing["stat"]["citedNumbers"] == [7]
        unused = next(f for f in findings if f["type"] == "reference_never_cited")
        assert 4 in unused["stat"]["entries"]
        assert "duplicate_reference" in types


def test_analyze_keeps_reference_numbers_out_of_digit_statistics():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.docx")
        _paper_docx(path)
        result = g.analyze(path)
        assert result["stats"]["referenceCount"] == 5
        assert result["stats"]["numberCountText"] < len(g.extract_numbers_from_text(g.load_source(path)[0]))
        assert [f["id"] for f in result["findings"]] == [f"data-{i:04d}" for i in range(1, len(result["findings"]) + 1)]


def test_latex_bib_checks():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "refs.bib"), "w", encoding="utf-8") as f:
            f.write("@article{good2020,\n  title = {Deep acoustic recognition},\n  author = {Smith, J},\n"
                    "  journal = {Journal of Sound},\n  year = {2020},\n  doi = {10.1000/abc.123}\n}\n")
        tex = os.path.join(d, "main.tex")
        with open(tex, "w", encoding="utf-8") as f:
            f.write("\\documentclass{article}\\begin{document}\nAs shown \\citep{good2020, missing2021}.\n"
                    "\\bibliography{refs}\n\\end{document}\n")
        info = g.load_reference_info(tex)
        assert info["entries"][0]["doi"] == "10.1000/abc.123"
        findings = g.run_text_checks(g.load_source(tex)[0], info, "main.tex")
        undefined = next(f for f in findings if f["type"] == "citation_without_reference")
        assert undefined["stat"]["undefinedKeys"] == ["missing2021"]


def test_verify_references_with_mocked_crossref():
    entries = [
        {"text": "[1] Smith J. Deep acoustic recognition in shallow water. J Sound, 2020.", "doi": "10.1000/real"},
        {"text": "[2] Doe A. Totally invented paper about sonar. J Fake, 2024.", "doi": "10.1000/fake"},
        {"text": "[3] Roe B. Robust ship noise classification with transformers. Ocean Eng, 2022.", "doi": "10.1000/other"},
        {"text": "[4] Poe C. Imaginary benchmark for underwater acoustic target recognition. 2023.", "doi": ""},
    ]

    def fake_fetch(url, mailto=None):
        if url.endswith("/works/10.1000/real"):
            return 200, {"message": {"title": ["Deep acoustic recognition in shallow water"]}}
        if url.endswith("/works/10.1000/fake"):
            return 404, None
        if url.endswith("/works/10.1000/other"):
            return 200, {"message": {"title": ["Protein folding in yeast cells"]}}
        if "filter=updates:10.1000/real" in url:
            return 200, {"message": {"items": [{"DOI": "10.1000/notice", "update-to": [{"DOI": "10.1000/real", "type": "retraction"}]}]}}
        if "filter=updates:" in url:
            return 200, {"message": {"items": []}}
        if "query.bibliographic" in url:
            return 200, {"message": {"items": [{"title": ["Something unrelated entirely"]}]}}
        raise AssertionError(url)

    findings = refs_check.verify_references(entries, "x", fetch=fake_fetch, pause=0)
    by_type = {f["type"]: f for f in findings}
    assert by_type["cited_retracted_work"]["severity"] == "high"
    assert by_type["reference_doi_not_found"]["stat"]["doi"] == "10.1000/fake"
    assert by_type["reference_doi_mismatch"]["stat"]["doi"] == "10.1000/other"
    assert by_type["reference_not_found"]["severity"] == "low"


# ---------------------------------------------------------------- images


def _texture(seed, size=(160, 120)):
    from PIL import Image, ImageFilter

    rnd = random.Random(seed)
    img = Image.new("L", size)
    img.putdata([rnd.randint(0, 255) for _ in range(size[0] * size[1])])
    return img.filter(ImageFilter.GaussianBlur(1.2)).resize((size[0] * 2, size[1] * 2))


def test_near_duplicate_panels_survive_rotation_mirroring_and_resizing():
    from PIL import Image, ImageDraw

    with tempfile.TemporaryDirectory() as d:
        blot = _texture(1)
        figure = Image.new("L", (blot.width * 2 + 60, blot.height + 40), 255)
        figure.paste(blot, (20, 20))
        figure.paste(_texture(2), (blot.width + 40, 20))
        figure.save(os.path.join(d, "fig1.png"))
        reused = blot.transpose(Image.Transpose.FLIP_LEFT_RIGHT).rotate(90, expand=True).resize((200, 260))
        reused.save(os.path.join(d, "fig2.jpg"), quality=80)
        chart = Image.new("L", (400, 300), 255)
        draw = ImageDraw.Draw(chart)
        draw.line([(20, 280), (380, 280)], fill=0)
        draw.line([(20, 280), (100, 150), (200, 200), (380, 40)], fill=0, width=2)
        chart.save(os.path.join(d, "chart1.png"))
        chart.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(os.path.join(d, "chart2.png"))

        paths = [os.path.join(d, n) for n in ("fig1.png", "fig2.jpg", "chart1.png", "chart2.png")]
        pairs = image_similarity.find_near_duplicates(paths, out_dir=d)
        assert len(pairs) == 1, pairs
        pair = pairs[0]
        assert {pair["a"]["file"], pair["b"]["file"]} == {"fig1.png", "fig2.jpg"}
        assert pair["transform"] != "identity" and not pair["sameFigure"]
        assert os.path.isfile(os.path.join(d, pair["preview"]))


def test_manifest_lists_near_duplicates_for_docx():
    import io

    from PIL import Image

    def png(img):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.docx")
        blot = _texture(5)
        _docx_with(path, _p("Figure"), rels=[("rId1", "media/image1.png"), ("rId2", "media/image2.png")])
        import zipfile

        with zipfile.ZipFile(path, "a") as z:
            z.writestr("word/media/image1.png", png(blot))
            z.writestr("word/media/image2.png", png(blot.transpose(Image.Transpose.ROTATE_180).resize((300, 220))))
        manifest = g.run_image_extraction(path, os.path.join(d, "imgs"))
        assert manifest["nearDuplicates"], json.dumps(manifest, ensure_ascii=False)[:500]
