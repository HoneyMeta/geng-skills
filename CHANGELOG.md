# Changelog

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
