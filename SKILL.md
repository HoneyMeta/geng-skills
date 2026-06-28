---
name: geng-skills
description: >-
  论文投稿前的科研诚信自检（self-check）。从【数值数据异常】和【图像 PS 痕迹】两个方向，
  检查 LaTeX / Word 论文中的常见造假信号——末位数字异常、Benford 偏离、等差数列、重复数据块/行、
  GRIM 均值不自洽，以及图片的复制重用、拼接、克隆/涂抹等痕迹——最终导出 PDF 报告。
  灵感来自"耿同学"的学术打假方法。当用户要检查论文数据/图片是否有造假风险、做投稿前诚信自查、
  或要生成数据完整性/图像取证报告时使用。
license: MIT
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
pip install -r requirements.txt   # Pillow（图片尺寸/拼版）、reportlab（PDF）
```
缺 `reportlab` 时报告自动降级为 HTML；缺 `Pillow` 时跳过尺寸/拼版但仍能抽图。

### 步骤 1 · 数据取证
```
python scripts/geng_check.py data --input <paper.docx|paper.tex|latex_dir> --out findings.json
```
产出 `findings.json`，每条 finding 含 `type / severity / location / stat / explanation`。
检查项见 [references/data_red_flags.md](references/data_red_flags.md)：
末位数字均匀性、Benford、等差数列、重复数据块、重复行、小数位异常不一致（弱信号）。

如正文里出现"mean ± sd, n=…"这类报告统计量，对可疑者补跑 GRIM / SPRITE-lite：
```
# GRIM：报告均值在该样本量+小数位下是否数学上可能
python scripts/geng_check.py grim   --mean 3.45 --n 20 --decimals 2

# SPRITE-lite：有界数据（量表/百分比等）的报告标准差是否超过理论上界
python scripts/geng_check.py sprite --mean 2.0 --sd 3.0 --n 20 --min 1 --max 7
```
GRIM 与 SPRITE-lite 都需手动给参数（从自由文本自动抠 mean/sd/n/bounds 不可靠），
是"失误无法解释"的数学不可能型硬信号，发现不一致按 high 处理。

### 步骤 2 · 图像抽取 + 视觉判读
```
python scripts/geng_check.py images --input <...> --out-dir extracted_images --contact-sheet
```
产出 `extracted_images/`（所有图片）、`manifest.json`（清单 + 尺寸 + sha1 + `exactDuplicateFiles`）、
`_contact_sheet.png`（缩略图拼版，便于横向比对）。

然后**你亲自看图**：逐张打开 `extracted_images/` 下的图片（以及拼版图），按
[references/image_red_flags.md](references/image_red_flags.md) 的清单判断：
- western blot / 凝胶条带的复制重用（同条带跨图、跨样本、旋转/翻转后再用）
- 拼接缝、背景不连续、克隆/图章/涂抹痕迹、异常锐利或重复的纹理
- 显微/流式/统计图中重复出现的区域

`manifest.json` 里 `exactDuplicateFiles`（sha1 完全一致）是脚本免费送的硬信号，优先核对。

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

- **high**：跨样本重复数据块/重复行、末位数字极端偏斜（p<1e-6）、GRIM 不一致、SPRITE-lite 标准差不可能、图片像素级重复使用 → 强烈建议复核。
- **medium**：完美等差数列、Benford 显著偏离、可疑拼接缝 → 需要解释。
- **low / info**：小数位异常不一致、轻度偏离，多半有正当原因（如去尾零），提醒留意即可。

不要仅因为"数据很整齐""图片很干净"就判高风险；也不要放过"不小心无法解释"的硬信号——
这正是耿同学的判断准则：图片可能误用，但不可能"不小心 PS"；数据相似到那种程度，也不是失误能解释的。

## 限制

- 图像只做**篇内**比对（本文档内图片之间），不做跨论文/全库比对（那需要外部数据库）。
- 数据检查依赖能从 docx 表格 / LaTeX `tabular` 中解析出数值；图片里的数据、公式渲染的数值无法读取。
- 所有"造假"判断都需人工/模型确认；本工具只提供线索与报告。
