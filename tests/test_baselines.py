# -*- coding: utf-8 -*-
"""v1.4 baseline (comparison-method) checks: table extraction, citation resolution, bold / metric /
cross-table / improvement-claim checks, and the online part with an injected fake fetch (no network).

    python -m pytest tests/test_baselines.py
    python tests/test_baselines.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)
import baseline_check as bc  # noqa: E402
import geng_check as g  # noqa: E402

from test_smoke import _docx_with  # noqa: E402

TESTDOC = os.path.join(HERE, "..", "testdoc")


def _tables(tex, names=()):
    tables = bc.latex_rich_tables(tex, g.BASELINE_HELPERS)
    for t in tables:
        bc.analyze_table(t, list(names))
    return tables


def _write(d, name, text):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _by_type(findings, t):
    return [f for f in findings if f["type"] == t]


PAPER = r"""\documentclass{article}
\date{June 2026}
\begin{document}
We propose GengNet, a gated network for image classification.
Code is available at \url{https://github.com/foo/bar}. The data are archived at \url{https://zenodo.org/record/1}.
CIFAR-10 contains 60,000 colour images in 10 classes.
\begin{table}[t]
\caption{Top-1 accuracy (\%). $\dagger$ re-implemented by us with the official code.}
\begin{tabular}{lccc}
\toprule
Method & CIFAR-10 $\uparrow$ & CIFAR-100 $\uparrow$ & Params (M) \\
\midrule
ResNet-50~\cite{he2016} & 93.0 & 76.5 & 25.6 \\
ViT-B$^\dagger$~\cite{vit} & 92.1 & 75.3 & 86.0 \\
DeiT-S~\cite{deit} & 85.0 & 70.2 & 22.1 \\
EfficientNet-B0~\cite{effnet} & 80.1 & 71.0 & 5.3 \\
HyperNet-X~\cite{fake} & 91.0 & 74.4 & 30.2 \\
MLP-Mixer~\cite{mixer} & 90.4 & 73.9 & 59.9 \\
GhostNet~\cite{ghost} & 89.3 & 72.8 & 7.3 \\
\midrule
GengNet (ours) & \textbf{94.27} & \textbf{78.41} & 12.13 \\
\bottomrule
\end{tabular}
\end{table}
\begin{thebibliography}{9}
\bibitem{he2016} K. He, X. Zhang, S. Ren, and J. Sun. Deep residual learning for image recognition. In CVPR, 2016. arXiv:1512.03385.
\bibitem{vit} A. Dosovitskiy et al. An image is worth 16x16 words: Transformers for image recognition at scale. In ICLR, 2021. doi:10.48550/arXiv.2010.11929.
\bibitem{deit} H. Touvron et al. Training data-efficient image transformers and distillation through attention. In ICML, 2021. arXiv:2012.12877.
\bibitem{effnet} M. Tan and Q. Le. EfficientNet: Rethinking model scaling for convolutional neural networks. In ICML, 2019. doi:10.5555/effnet.
\bibitem{fake} Q. Nobody and R. Noone. Hypernetwork cascades for imaginary benchmark recognition. In NeurIPS, 2020.
\bibitem{mixer} I. Tolstikhin et al. MLP-Mixer: An all-MLP architecture for vision. In NeurIPS, 2021.
\bibitem{ghost} K. Han et al. GhostNet: More features from cheap operations. In CVPR, 2020. arXiv:1911.11907.
\end{thebibliography}
\end{document}
"""


# ---------------------------------------------------------------- extraction + citations


def test_latex_extraction_classification_and_citations():
    with tempfile.TemporaryDirectory() as d:
        result = g.analyze(_write(d, "paper.tex", PAPER))
    dossier = result["baselines"]
    assert dossier["proposedNames"] == ["GengNet"]
    assert dossier["paperYear"] == 2026
    assert len(dossier["tables"]) == 1
    table = dossier["tables"][0]
    assert table["table"] == "Table 1"
    assert [p["method"] for p in table["proposed"]] == ["GengNet (ours)"]
    assert table["proposed"][0]["bold"] == ["CIFAR-10", "CIFAR-100"]
    methods = {b["method"]: b for b in table["baselines"]}
    assert list(methods) == ["ResNet-50", "ViT-B", "DeiT-S", "EfficientNet-B0", "HyperNet-X", "MLP-Mixer", "GhostNet"]
    assert methods["ResNet-50"]["refs"] == ["key:he2016"] and methods["ResNet-50"]["values"]["CIFAR-10"] == "93.0"
    assert methods["ViT-B"]["disclosure"] == "rerun" and methods["ResNet-50"]["disclosure"] is None
    refs = dossier["references"]
    assert refs["key:he2016"]["arxivId"] == "1512.03385"
    assert refs["key:he2016"]["titleGuess"] == "Deep residual learning for image recognition"
    assert refs["key:vit"]["arxivId"] == "2010.11929"          # from the 10.48550/arXiv DOI
    assert refs["key:vit"]["titleGuess"].startswith("An image is worth")
    assert refs["key:effnet"]["doi"] == "10.5555/effnet" and "doi" in refs["key:effnet"]["retrieval"]
    assert "openalexSearch" in refs["key:fake"]["retrieval"]
    links = {link["url"]: link for link in dossier["links"]}
    assert links["https://github.com/foo/bar"]["kind"] == "code" and links["https://github.com/foo/bar"]["claimedAvailable"]
    assert links["https://zenodo.org/record/1"]["kind"] == "data"
    assert dossier["datasetClaims"][0]["datasets"] == ["CIFAR-10"]
    assert {c["value"] for c in dossier["datasetClaims"][0]["counts"]} == {"60,000", "10"}
    # bold values are the best of their columns -> nothing to flag offline
    assert not [f for f in result["findings"] if f["type"] in g.BASELINE_TYPES], result["findings"]
    assert result["stats"]["comparisonTables"] == 1 and result["stats"]["citedBaselines"] == 7


def _tc(text, bold=None):
    rpr = "" if bold is None else ("<w:rPr><w:b/></w:rPr>" if bold is True else '<w:rPr><w:b w:val="%s"/></w:rPr>' % bold)
    return '<w:tc><w:p><w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p></w:tc>' % (rpr, text)


def _p(text):
    return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text


def test_docx_bold_runs_numeric_citations_and_chinese_claim():
    rows = [
        [_tc("方法"), _tc("准确率 ↑"), _tc("F1")],
        [_tc("SVM [1]"), _tc("85.1"), _tc("80.2")],
        [_tc("ResNet [2]"), _tc("90.3", bold=True), _tc("84.0")],
        [_tc("Transformer [3]"), _tc("89.0", bold="0"), _tc("83.1")],
        [_tc("本文方法"), _tc("91.2"), _tc("85.0", bold="1")],
    ]
    table = "<w:tbl>" + "".join("<w:tr>%s</w:tr>" % "".join(r) for r in rows) + "</w:tbl>"
    body = "".join([
        _p("我们提出了一种新的识别方法。本文方法的准确率比 ResNet 提升了 0.9 个百分点，比 Transformer 提高了 3.5 个百分点。"),
        _p("Table 1: Accuracy (%) and F1 on the test set."),
        table,
        _p("参考文献"),
        _p("[1] Cortes C, Vapnik V. Support-vector networks. Machine Learning, 1995. doi:10.1007/BF00994018"),
        _p("[2] He K, Zhang X, Ren S, Sun J. Deep residual learning for image recognition. CVPR, 2016."),
        _p("[3] Vaswani A, Shazeer N, Parmar N. Attention is all you need. NeurIPS, 2017."),
    ])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.docx")
        _docx_with(path, body)
        result = g.analyze(path)
    dossier = result["baselines"]
    table = dossier["tables"][0]
    assert table["table"] == "Table 1" and table["caption"].startswith("Table 1")
    assert [p["method"] for p in table["proposed"]] == ["本文方法"] and table["proposed"][0]["bold"] == ["F1"]
    by_method = {b["method"]: b for b in table["baselines"]}
    assert by_method["SVM"]["cites"] == ["num:1"] and by_method["SVM"]["refs"] == ["num:1"]
    assert by_method["ResNet"]["bold"] == ["准确率 ↑"] and by_method["Transformer"]["bold"] == []   # w:val="0" is not bold
    assert dossier["references"]["num:1"]["doi"] == "10.1007/BF00994018"
    assert dossier["references"]["num:2"]["titleGuess"] == "Deep residual learning for image recognition"
    bold = _by_type(result["findings"], "best_marked_not_best")
    assert len(bold) == 1 and bold[0]["stat"]["bold"] == "90.3" and bold[0]["stat"]["better"] == "91.2", bold
    claims = _by_type(result["findings"], "improvement_claim_mismatch")
    assert [c["stat"]["claimed"] for c in claims] == ["3.5"], claims   # 0.9 matches 91.2 - 90.3; 3.5 matches nothing


# ---------------------------------------------------------------- offline checks


def _one_col(header, cells):
    rows = "\n".join("%s & %s & 1.0 \\\\" % (chr(65 + i), c) for i, c in enumerate(cells))
    return "\\begin{tabular}{lcc}\nMethod & %s & Other \\\\\n%s\n\\end{tabular}" % (header, rows)


def test_best_marked_not_best_directions_and_ties():
    cases = [
        ("Acc $\\uparrow$", ["90.1", "\\textbf{91.5}", "92.0"], True),     # higher-better, bold not max
        ("Error (\\%)", ["9.9", "\\textbf{8.7}", "8.0"], True),            # lower-better, bold not min
        ("Acc", ["91.2", "\\textbf{91.20}", "90.5"], False),               # tie within rounding
        ("Score", ["5.0", "\\textbf{6.0}", "7.0"], True),                  # unknown direction, bold in the middle
        ("Score", ["5.0", "6.0", "\\textbf{7.0}"], False),                 # unknown direction, bold extreme
        ("mAP", ["40.1", "41.0", "\\textbf{43.5}"], False),                # correct bold
        ("Params", ["\\textbf{1.2}", "3.5", "9.9"], False),                # lower-better, correct
    ]
    for header, cells, flagged in cases:
        t = _tables(_one_col(header, cells))[0]
        found = bc.check_best_marked(t, "x")
        assert bool(found) == flagged, (header, cells, found)
        if found:
            assert found[0]["severity"] == "medium" and not found[0]["stat"]["wholeRowBold"]
    whole_row = _tables("\\begin{tabular}{lcc}\nMethod & Acc & F1 \\\\\nA & 90.0 & 80.0 \\\\\nB & 91.0 & 82.0 \\\\\n"
                        "Ours & \\textbf{90.5} & \\textbf{81.0} \\\\\n\\end{tabular}")[0]
    found = bc.check_best_marked(whole_row, "x")
    assert found and all(f["severity"] == "low" and f["stat"]["wholeRowBold"] for f in found)


def test_metric_inconsistent():
    tex = r"""\begin{tabular}{lccccc}
Method & Precision & Recall & F1 & Acc & AUC \\
A & 80.0 & 70.0 & 85.0 & 101.2 & 0.91 \\
B & 80.0 & 70.0 & 74.0 & 88.0 & 0.93 \\
C & 80.0 & 70.0 & 74.7 & 87.0 & 92.1 \\
D & 60.0 & 50.0 & 54.5 & 86.0 $\pm$ -1.2 & 0.95 \\
\end{tabular}"""
    findings = bc.check_metric_consistency(_tables(tex)[0], "x")
    got = sorted((f["stat"].get("row") or f["location"].split(": ")[-1], f["stat"]["check"], f["severity"]) for f in findings)
    assert got == [("A", "bounded", "medium"), ("A", "f1", "medium"), ("B", "f1", "low"),
                   ("C", "bounded", "medium"), ("D", "negative_sd", "medium")], got
    macro = r"""\begin{tabular}{lccc}
Method & Macro-P & Macro-R & Macro-F1 \\
A & 80.0 & 70.0 & 60.0 \\
B & 80.0 & 70.0 & 74.0 \\
C & 0.80 & 0.70 & 0.86 \\
\end{tabular}"""
    findings = bc.check_metric_consistency(_tables(macro)[0], "x")
    # macro F1 may be below both P and R and need not equal 2PR/(P+R); above both is impossible for any averaging
    assert [(f["stat"]["row"], f["severity"]) for f in findings] == [("C", "medium")], findings


def test_baseline_value_inconsistent_across_tables():
    def table(acc, header="CIFAR-10 Acc"):
        return ("\\begin{tabular}{lc}\nMethod & %s \\\\\nResNet-50~\\cite{he2016} & %s \\\\\nViT-B~\\cite{vit} & 92.1 \\\\\n"
                "Ours & 94.0 \\\\\n\\end{tabular}\n" % (header, acc))
    for tex, expected in ((table("93.0") + table("92.4"), 1), (table("93.0") + table("93.00"), 0),
                          (table("93.0") + table("0.930"), 0), (table("93.0", "Acc") + table("92.4", "Acc"), 0)):
        tables = _tables(tex)
        found = bc.check_cross_table_values(tables, "x")
        assert len(found) == expected, (tex, found)
    found = bc.check_cross_table_values(_tables(table("93.0") + table("92.4")), "x")[0]
    assert found["severity"] == "medium" and [v["value"] for v in found["stat"]["values"]] == ["93.0", "92.4"]


def test_improvement_claims_english_and_chinese():
    tex = ("\\begin{tabular}{lc}\nMethod & CIFAR-10 Acc \\\\\nResNet & 90.0 \\\\\nViT & 91.5 \\\\\n"
           "Ours & 93.2 \\\\\n\\end{tabular}")
    tables = _tables(tex)
    text = ("Our method improves accuracy by 1.7 points over ViT. Our method outperforms ResNet by 3.6%. "
            "We improve over the best baseline by 2.5 points. ViT improves over ResNet by 9.9%.\n\n"
            "本文方法比 ResNet 提升了 3.2 个百分点。所提方法的准确率提高了 5.1%。")
    found = bc.check_improvement_claims(text, tables, [], "x")
    assert sorted(f["stat"]["claimed"] for f in found) == ["2.5", "5.1"], found
    assert all(f["severity"] == "low" and f["stat"]["closest"] for f in found)
    # no proposed row -> claims cannot be checked
    no_ours = _tables(tex.replace("Ours", "Other"))
    assert bc.check_improvement_claims(text, no_ours, [], "x") == []


def test_no_false_positives_on_testdocs():
    for name in ("test_paper.tex", "test_paper.docx"):
        result = g.analyze(os.path.join(TESTDOC, name))
        assert not [f for f in result["findings"] if f["type"] in g.BASELINE_TYPES], (name, result["findings"])
        assert result["baselines"]["tables"], name   # the comparison table is still in the dossier


# ---------------------------------------------------------------- online part (fake fetch)


def _html(body):
    return "<html><head><title>x</title></head><body><article class='ltx_document'>%s</article></body></html>" % body


def _atom(aid, title, year="2016"):
    return ("<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/%sv2</id><title>%s</title>"
            "<summary>An abstract.</summary><published>%s-01-01T00:00:00Z</published></entry></feed>" % (aid, title, year))


EMPTY_FEED = "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"

ROUTES = [
    ("export.arxiv.org/api/query?id_list=1512.03385", _atom("1512.03385", "Deep Residual Learning for Image Recognition", "2015")),
    ("arxiv.org/html/1512.03385", _html("<table><tr><td>ResNet-50</td><td>93.0</td><td>76.5</td></tr></table>"
                                        "<p>An extension reaches 94.27 and 78.41 with 12.13M parameters.</p>")),
    ("export.arxiv.org/api/query?id_list=2010.11929", _atom("2010.11929", "An Image is Worth 16x16 Words", "2020")),
    ("arxiv.org/html/2010.11929", _html("<p>Vision Transformer ViT-B/16 is pre-trained on JFT.</p>")),
    ("export.arxiv.org/api/query?id_list=2012.12877", _atom("2012.12877", "Training data-efficient image transformers", "2020")),
    ("arxiv.org/html/2012.12877", _html("<p>We introduce DeiT.</p><p>On CIFAR-10 the small model obtains 98.1 after fine-tuning.</p>")),
    ("api.openalex.org/works/doi:10.5555/effnet", json.dumps({
        "id": "https://openalex.org/W1", "display_name": "EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks",
        "publication_year": 2019, "locations": [{"landing_page_url": "https://arxiv.org/abs/1905.11946"}]})),
    ("arxiv.org/html/1905.11946", _html("<table><tr><td>EfficientNet-B0</td><td>98.1</td><td>87.2</td><td>5.3M</td></tr></table>")),
    ("api.openalex.org/works?search=Hypernetwork", json.dumps({"results": [{"display_name": "Protein folding dynamics in yeast cells"}]})),
    ("api.openalex.org/works?search=MLP", json.dumps({"results": [{
        "id": "https://openalex.org/W2", "display_name": "MLP-Mixer: An all-MLP Architecture for Vision", "publication_year": 2021,
        "abstract_inverted_index": {"Mixer": [0], "uses": [1], "only": [2], "MLPs": [3]}}]})),
    ("export.arxiv.org/api/query?max_results", EMPTY_FEED),
    ("export.arxiv.org/api/query?id_list=1911.11907", _atom("1911.11907", "Squeeze-and-Excitation Networks", "2017")),
    ("arxiv.org/html/1911.11907", _html("<table><tr><td>SENet-154</td><td>1.11</td></tr></table><p>Squeeze-and-excitation blocks.</p>")),
    ("api.github.com/repos/foo/bar", (404, None)),
    ("zenodo.org/record/1", (200, b"")),
]


def fake_fetch(url, method="GET"):
    fake_fetch.calls.append((method, url))
    for key, value in ROUTES:
        if key in url:
            return value if isinstance(value, tuple) else (200, value)
    return 404, None


def test_online_checks_with_fake_fetch():
    fake_fetch.calls = []
    with tempfile.TemporaryDirectory() as d:
        result = g.analyze(_write(d, "paper.tex", PAPER), fetch_baselines=True, baseline_fetch=fake_fetch, baseline_out_dir=d)
        dossier = result["baselines"]
        files = [s.get("file") for s in dossier["sources"].values() if s.get("file")]
        assert len(files) == 6 and all(os.path.isfile(os.path.join(d, f)) for f in files), files
        with open(os.path.join(d, dossier["sources"]["key:deit"]["file"]), encoding="utf-8") as f:
            header = f.read(2000)
        assert "# url: https://arxiv.org/html/2012.12877" in header and "# retrieved:" in header
    by_type = {}
    for f in result["findings"]:
        by_type.setdefault(f["type"], []).append(f)

    def one(t, method):
        hits = [f for f in by_type.get(t, []) if f["stat"].get("method") == method or method in f["location"]]
        assert len(hits) == 1, (t, method, by_type.get(t))
        return hits[0]

    assert one("baseline_value_not_in_source", "ViT-B")["severity"] == "info"          # disclosed re-run (dagger)
    deit = one("baseline_value_not_in_source", "DeiT-S")
    assert deit["severity"] == "medium" and any("CIFAR-10" in s for s in deit["stat"]["snippets"]), deit
    eff = one("baseline_value_differs_from_source", "EfficientNet-B0")
    assert eff["severity"] == "high" and eff["stat"]["columns"][0]["sourceLines"], eff
    assert one("cited_baseline_not_found", "key:fake")["severity"] == "medium"
    assert one("baseline_source_abstract_only", "key:mixer")["severity"] == "info"
    assert one("baseline_name_not_in_source", "GhostNet")["severity"] == "low"
    copied = one("proposed_results_in_cited_work", "key:he2016")
    assert copied["severity"] == "high" and sorted(copied["stat"]["valuesFound"]) == ["12.13", "78.41", "94.27"]
    link = one("claimed_link_unreachable", "github.com/foo/bar")
    assert link["severity"] == "medium" and link["stat"]["status"] == 404
    assert not [f for f in result["findings"] if f["type"].startswith("baseline_value") and "ResNet-50" in f["location"]]
    online = dossier["online"]
    assert online["papersRequested"] == 7 and online["papersFetched"] == 6 and online["fullText"] == 5
    assert online["unresolved"] == ["key:fake"]
    assert {c["result"] for c in dossier["linkChecks"]} == {"dead", "ok"}
    assert ("HEAD", "https://zenodo.org/record/1") in fake_fetch.calls
    assert all("_text" not in s for s in dossier["sources"].values())
    assert result["stats"]["baselinesFetchedOnline"] is True
    assert [x["id"] for x in dossier["findingIds"]] == [f["id"] for f in result["findings"] if f["type"] in g.BASELINE_TYPES]


def test_network_errors_never_crash():
    def broken(url, method="GET"):
        raise OSError("network down")

    with tempfile.TemporaryDirectory() as d:
        result = g.analyze(_write(d, "paper.tex", PAPER), fetch_baselines=True, baseline_fetch=broken, baseline_out_dir=d)
    dossier = result["baselines"]
    assert dossier["fetchErrors"] and dossier["online"]["papersFetched"] == 0
    # unanswered lookups are not evidence that a reference does not exist
    assert not [f for f in result["findings"] if f["type"] in ("cited_baseline_not_found", "claimed_link_unreachable")]


def test_value_matching_rules():
    src = bc.source_numbers("error 6.43 (6.61±0.16), acc 0.9357, 643 images, 4 layers, 93.57")
    v = bc.cell_value
    assert bc.value_in_source(v("6.43"), src, True) == "6.43"
    assert bc.value_in_source(v("6.4"), src, True) == "6.43"                    # rounding
    assert bc.value_in_source(v("93.6"), src, True) == "93.57"
    assert bc.value_in_source(v("0.0643"), src, True) == "6.43 (量纲换算)"       # 6.43 % = 0.0643
    assert bc.value_in_source(v("6.43"), bc.source_numbers("643 images"), True) is None   # an integer is not 6.43
    assert bc.value_in_source(v("93.57"), bc.source_numbers("test error 6.43"), True) == "6.43 (100 − 原值，准确率/错误率互换)"
    assert bc.value_in_source(v("93.57"), bc.source_numbers("test error 6.43"), False) is None   # only for acc/err columns
    assert bc.value_in_source(v("4.00"), src, False) == "4"
    assert v("25.6M")["value"] == 25.6 and v("91.2 ± 0.3")["sd"] == 0.3 and v("91.2†")["raw"] == "91.2" and v("--") is None


# ---------------------------------------------------------------- report


def test_report_has_baseline_section_and_type_labels():
    data = {"source": "p.tex", "severityCounts": {"high": 1},
            "findings": [{"id": "data-0001", "type": "baseline_value_differs_from_source", "severity": "high",
                          "location": "p.tex#Table 1 行6: DenseNet", "stat": {}, "explanation": "x < y"}],
            "baselines": {"tables": [{"baselines": [{"refs": ["key:a"]}]}], "online": {"enabled": True, "papersFetched": 1,
                                                                                       "papersRequested": 1, "fullText": 1}}}
    verdicts = {"overallAssessment": "ok", "baselineVerdicts": [{
        "table": "Table 1", "method": "DenseNet-BC (k=40)", "ref": "[5]", "column": "CIFAR-10", "reportedHere": "4.83",
        "reportedInSource": "3.46 (Table 2)", "source": "https://arxiv.org/abs/1608.06993", "verdict": "基线被压低",
        "severity": "high", "comment": "本文把原文 3.46 写成 4.83"}]}
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "r.html")
        g.generate_html_report(data, verdicts, out)
        with open(out, encoding="utf-8") as f:
            html = f.read()
        assert "对比方法核查" in html and "基线被压低" in html and "3.46 (Table 2)" in html
        assert "基线数值与原文不同" in html and "x &lt; y" in html
        assert "联网取回被引原文 1/1 篇" in html
        pdf = g.generate_pdf_report(data, verdicts, os.path.join(d, "r.pdf"))
        if pdf:   # reportlab installed
            assert os.path.getsize(pdf) > 1000


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("[ok]", name)
    print("ALL BASELINE TESTS PASSED")
