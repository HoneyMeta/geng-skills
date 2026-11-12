---
name: geng-skills
description: >-
  论文投稿前的科研诚信自检（self-check）。从【数值数据异常】【报告统计量】【参考文献】【图像 PS 痕迹】
  几个方向，检查 LaTeX / Word 论文中的常见问题信号——末位数字异常、Benford 偏离、等差数列、重复数据块/行、
  GRIM 均值不自洽、t/F/r/χ2/z 检验的 p 值对不上、p 值在 0.05 下方堆积、扭曲短语、引用编号与参考文献不符、
  虚构/错误 DOI 和被撤稿文献（可选联网），以及图片的复制重用（含旋转/镜像/缩放后的近重复）、拼接、
  克隆/涂抹等痕迹——最终导出 PDF 报告。
  灵感来自"耿同学"的学术打假方法。当用户要检查论文数据/图片是否有造假风险、做投稿前诚信自查、
  或要生成数据完整性/图像取证报告时使用。
license: MIT
compatibility: Python 3.9+. 数据与文本检查零依赖；Pillow（图片尺寸/拼版/近重复检测）、reportlab（PDF 报告）、PyMuPDF（PDF 矢量图预览）均为可选；--verify-refs 需要联网访问 api.crossref.org。
---

# geng-skills · 论文诚信自检

> 灵感来自"耿同学"的学术打假。"耿"既是致敬，也取"耿直"之意。
> 本 skill 面向**作者本人的投稿前自检**：在被别人打假之前，先用同样的方法检查自己的论文。
> 它**不下"造假"结论**——脚本只负责筛信号，是否有问题由你（模型，含视觉能力）判读。

## 适用场景

- 用户给出 `.docx` / `.tex` 文件或 LaTeX 工程目录，想检查数据/图片是否有造假风险。
- 用户想在投稿/送审前做一次科研诚信自查。
- 用户想要一份数据完整性 + 图像取证的 PDF 报告。

## 核心原则（务必遵守）

1. **脚本筛信号，模型下判断。** `geng_check.py` 输出的是*候选异常*（带统计量），不是结论。
   你必须逐条判读，区分"真问题"与"正当原因"（单位换算、刻度、序号、四舍五入、有界量等），避免误报。
2. **图像判读用你自己的视觉能力。** 脚本只把图片抽出来 + 算哈希 + 拼缩略图。
   是否有 PS 痕迹/复制重用，由你直接看图思考，**不要**依赖任何 CV 库。
3. **定位是"自检"，不是"洗稿/逃避检测"。** 报告措辞中立，提示作者复核原始数据，不教人掩盖造假。
4. **不修改原文。** 只读输入，所有产物写到独立文件/目录。
5. **不冒充任何官方系统。** 报告须注明"仅供投稿前自检参考，不构成造假结论"。

## 工作流

### 步骤 0 · 环境
脚本零硬依赖即可跑数据检查（docx 用标准库解析）。可选增强：
```
pip install -r requirements.txt   # Pillow（图片尺寸/拼版）、reportlab（PDF）、PyMuPDF（可选）
```
缺 `reportlab` 时报告自动降级为 HTML；缺 `Pillow` 时跳过尺寸/拼版但仍能抽图；
缺 `PyMuPDF` 时 LaTeX 的 PDF 矢量图不渲染预览（仍会抽出原文件）。

### 步骤 1 · 数据取证
```
python scripts/geng_check.py data --input <paper.docx|paper.tex|latex_dir> --out findings.json
```
产出 `findings.json`，每条 finding 含 `type / severity / location / stat / explanation`。
LaTeX 多文件工程请直接把**主 .tex** 传给 `--input`（会自动展开 `\input` / `\include`）；
传目录时若发现多个含 `\documentclass` 的文件（旧版/备份稿），脚本会告警——它们会互相造成"重复数据"误报。
表格中的"均值 ± SD"/"均值 (SD)"单元格会拆开，SD 单独成列（位置标为 `列N(SD)`）参与检查。
检查项见 [references/data_red_flags.md](references/data_red_flags.md)：
末位数字均匀性、Benford、等差数列、重复数据块、重复行、小数位异常不一致（弱信号）。
参考文献里的年份、卷期、页码不参与末位/Benford 统计。

同一次 `data` 还会做以下文本检查（零依赖、离线）：
- `reported_p_inconsistent`：正文里写全的 `t(df)=…, p…`、`F(df1, df2)=…`、`r(df)=…`、`χ2(df)=…`、`z=…`
  按统计量和自由度重算 p 值（已考虑统计量四舍五入），对不上就报；显著性结论反转（决策性不一致）为 high，
  只有按单尾才对得上为 low。支持 LaTeX 写法与中文全角写法（`t（28）＝2.20，P＜0.05`）。
  只写了 `t = 2.20, p < 0.05` 没有自由度的无法重算，会被跳过。
- `p_value_bunching`：精确 p 值在 0.04–0.05 明显多于 0.03–0.04（弱信号，low）。
- `tortured_phrases`：洗稿工具产生的"扭曲短语"（如 profound learning = deep learning）。
- `citation_without_reference` / `reference_never_cited` / `duplicate_reference`：
  正文引用编号超出参考文献列表、列表条目从未被引用、同一文献列了两次；LaTeX 检查未定义的 `\cite` key。

**联网核对参考文献（可选，先征得用户同意）**：
```
python scripts/geng_check.py data --input <...> --out findings.json --verify-refs [--mailto 用户提供的邮箱]
```
会把参考文献的 DOI 和条目文本发送到 Crossref 公共 API：DOI 不存在（`reference_doi_not_found`，
可能是虚构/幻觉引用或 DOI 抄错）、DOI 与标题不符（`reference_doi_mismatch`）、被引论文有撤稿/撤回/关注声明
（`cited_retracted_work`）、无 DOI 的英文条目在 Crossref 找不到（`reference_not_found`，low，书籍/会议常查不到）。
不要替用户编造 `--mailto`。

如正文里出现"mean ± sd, n=…"这类报告统计量，对可疑者补跑 GRIM / SPRITE-lite：
```
# GRIM：报告均值在该样本量+小数位下是否数学上可能（多题量表均分加 --items 题数）
python scripts/geng_check.py grim   --mean 3.45 --n 20 --decimals 2

# SPRITE-lite：有界数据（量表/百分比等）的报告标准差是否超过理论上界
python scripts/geng_check.py sprite --mean 2.0 --sd 3.0 --n 20 --min 1 --max 7
```
GRIM 与 SPRITE-lite 都需手动给参数（从自由文本自动抠 mean/sd/n/bounds 不可靠），
是"失误无法解释"的数学不可能型硬信号，发现不一致按 high 处理。
GRIM 输出 `informative=false` 时（n×题数 ≥ 10^小数位）任何均值都能通过，不要把"通过"当作证据。

### 步骤 2 · 图像抽取 + 视觉判读
```
python scripts/geng_check.py images --input <...> --out-dir extracted_images --contact-sheet
```
产出 `extracted_images/`（所有图片）、`manifest.json`（清单 + 尺寸 + sha1 + 引用次数 +
`exactDuplicateFiles` + `reusedImages` + `nearDuplicates`）、`_contact_sheet.png`（缩略图拼版，便于横向比对）、
`_near_dup_*.png`（每对近重复区域并排对照，第二张已按检测到的旋转/镜像方向摆正）。
LaTeX 的 PDF 矢量图在装了 PyMuPDF 时会渲染出 `*.preview.png`，看预览图即可。

然后**你亲自看图**：逐张打开 `extracted_images/` 下的图片（以及拼版图），按
[references/image_red_flags.md](references/image_red_flags.md) 的清单判断：
- western blot / 凝胶条带的复制重用（同条带跨图、跨样本、旋转/翻转后再用）
- 拼接缝、背景不连续、克隆/图章/涂抹痕迹、异常锐利或重复的纹理
- 显微/流式/统计图中重复出现的区域

`manifest.json` 里三类脚本自动给出的信号，优先核对：
- `nearDuplicates`（需 Pillow）：把每张图按空白分隔切成子图，只看照片类子图（blot、凝胶、显微、照片；
  折线/柱状图会被跳过），用 256 位差值哈希在 8 种旋转/镜像下两两比对，能发现缩放、重新保存、
  裁进拼图、旋转或镜像后再用的子图，`sameFigure=true` 表示同一张图内部的两块子图相似。
  **一定要打开对应的 `_near_dup_*.png` 看图确认**：相邻切片、同一样本的正当重复展示也会相似。
- `exactDuplicateFiles`：sha1 完全一致的不同图片文件。
- `reusedImages`：同一个图片文件在文中被插入多次（Word 会把相同图片只存一份，docx 里的"一图多用"只能靠它发现），
  附各处图注；核对它们是否被当成了不同样本/条件。

### 步骤 3 · 汇总判读为 verdicts.json
把你对数据信号和图片的判读写成 `verdicts.json`：
```json
{
  "overallAssessment": "一句话整体结论，给作者的复核建议。",
  "verdicts": [
    {"id": "data-0001", "verdict": "需复核|确认问题|可解释", "comment": "为什么"}
  ],
  "imageVerdicts": [
    {"file": "image1.png", "severity": "high|medium|low|info", "issue": "疑似重复使用", "comment": "依据"}
  ]
}
```
`verdicts[].id` 对应 `findings.json` 里的 finding id。未列出的 finding 视为"未单独判读"。

### 步骤 4 · 生成报告
```
python scripts/geng_check.py report --findings findings.json --verdicts verdicts.json --out report.pdf
```
得到 `report.pdf`（含中文字体、风险分级表、图像判读表、整体结论、免责声明）。
无 reportlab 时自动产出 `report.html`。报告格式见 [references/report_format.md](references/report_format.md)。

## 严重度判读尺度（建议）

- **high**：跨样本重复数据块/重复行（≥3 个数值）、末位数字极端偏斜（p<1e-6）、GRIM 不一致、SPRITE-lite 标准差不可能、
  报告 p 值的显著性结论与统计量矛盾、引用了已撤稿论文、
  图片像素级重复使用或同一图片代表不同条件（含旋转/镜像后再用）→ 强烈建议复核。
- **medium**：完美等差数列、Benford 显著偏离、两个数值完全相同的重复行（如消融表两行一致）、可疑拼接缝、
  p 值与统计量不符、多个扭曲短语、DOI 不存在或与标题不符、正文引用编号超出参考文献列表 → 需要解释。
- **low / info**：小数位异常不一致、轻度偏离，多半有正当原因（如去尾零），提醒留意即可。

不要仅因为"数据很整齐""图片很干净"就判高风险；也不要放过"不小心无法解释"的硬信号——
这正是耿同学的判断准则：图片可能误用，但不可能"不小心 PS"；数据相似到那种程度，也不是失误能解释的。

## 限制

- 图像只做**篇内**比对（本文档内图片之间），不做跨论文/全库比对（那需要外部数据库，如 ImageTwin、Proofig）。
  近重复检测只看照片类子图；子图内部的局部克隆（一小块区域被复制）仍要靠看图。
- p 值重算只覆盖正文中写全统计量和自由度的检验；表格里的统计量、只写 `p < 0.05` 的结果无法检查。
- Crossref 收录以外的文献（多数中文期刊、书籍、学位论文、网页）联网也核对不了，查不到≠不存在。
- 数据检查依赖能从 docx 表格 / LaTeX `tabular` 中解析出数值；图片里的数据、公式渲染的数值无法读取。
- 所有"造假"判断都需人工/模型确认；本工具只提供线索与报告。
