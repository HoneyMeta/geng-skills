# Changelog

## 1.4.0 (2026-11-23)

- Baseline verification (comparison methods checked against their cited sources), new module
  `scripts/baseline_check.py`, always run by `data`:
  - comparison tables are re-parsed with their markup (LaTeX `\textbf` / `\cite` / `\multicolumn` / †*,
    DOCX bold runs, captions and table notes); rows are classified as proposed / baseline / ablation variant,
    and every baseline's citation (`\cite` key, `[12]`, author-year, or "X~\cite{…}" in the text) is resolved
    to its reference entry with DOI, arXiv id, year, title guess and retrieval links;
  - offline findings `best_marked_not_best`, `metric_inconsistent` (F1 vs P/R, bounded metrics, negative SD),
    `baseline_value_inconsistent` (same cited baseline, same column, two tables), `improvement_claim_mismatch`
    (English and Chinese improvement claims vs absolute / relative / averaged differences in the tables);
  - a baseline dossier in `findings.json` (`baselines`) and `<out>.baselines.json` (`--baselines-out`), with
    dataset statistics claimed in the text and code/data links.
- `data --fetch-baselines` (opt-in, network): cited baselines are resolved on arXiv / OpenAlex / Crossref,
  open full text (arXiv HTML, arXiv PDF or open-access PDF via PyMuPDF) is cached in `baseline_sources/`;
  findings `baseline_value_not_in_source`, `baseline_value_differs_from_source` (high when the baseline is
  reported worse than in its source and no re-run is disclosed), `baseline_name_not_in_source`,
  `cited_baseline_not_found`, `baseline_source_abstract_only`, `proposed_results_in_cited_work`,
  `claimed_link_unreachable` (GitHub API / HEAD), `baseline_newer_than_paper` (`--paper-year` or `\date`).
  Polite per-host pacing, `--max-baseline-papers` (default 30), network errors recorded in `fetchErrors`.
- SKILL.md step 1c: the model retrieves the cited papers itself, classifies each baseline (一致 / 基线被压低 /
  设置不同 / 原文无此结果 / 引用张冠李戴 / 无法核实 …) and writes `baselineVerdicts` into `verdicts.json`;
  new reference `references/baseline_red_flags.md`.
- Report: new "对比方法核查" section (PDF and HTML) with a summary of the automated signals; finding types are
  shown with Chinese names.
- Example `testdoc/baseline_paper.tex` (real CIFAR baselines with planted problems); tests in
  `tests/test_baselines.py` (offline, fake fetch).
- Fixes: `\_` in `\bibitem` DOIs; `.bib` entries keep `eprint` / `url`; Crossref user agent now `geng-skills/1.4`.

## 1.3.0 (2026-11-22)

- GRIMMER: `geng_check.py grimmer` tests whether a reported mean and SD can both come from integer data
  (sum parity and minimum sum of squares; sample or population SD; multi-item scales via `--items`).
- Automatic GRIM / GRIMMER on means reported in the text (`M = 3.47, SD = 1.20, n = 20`,
  `3.47 ± 1.20 (n = 20)`, `均分为 3.47（SD = 1.20，n = 20）`), only when the sentence or the two before it
  say the data are integer-valued (Likert, rating, count, 量表, 评分 …), and only when every reading
  (item mean or total, sample or population SD) is impossible. New finding types `grim_inconsistent` and
  `grimmer_inconsistent` (medium; high once the model confirms integer data).
- `text_table_mismatch`: a number in a sentence that cites a table, found in no table (rounding and
  % / fraction allowed), while a table holds the same digits with two neighbours swapped.
- Raw data files: `data --data-file raw.xlsx` (repeatable; .csv / .tsv / .txt / .xlsx, zero
  dependencies). Terminal digits per column (only when the range spans 50+ steps of the last digit),
  Benford per sheet, identical rows with decimals, identical columns (`duplicate_column`), repeated runs in
  continuous columns. ID columns are skipped; spreadsheet trailing zeros are restored per column.

## 1.2.0 (2026-11-12)

- statcheck-style p-value recomputation, p-value bunching, tortured phrases, reference-list checks with
  optional Crossref verification, perceptual near-duplicate image panels.

## 1.1.0 (2026-10-08)

- Fewer false positives in data forensics; image reuse detection in DOCX.
