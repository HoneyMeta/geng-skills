---
name: geng-skills
description: >-
  论文投稿前的科研诚信自检（self-check）。从【数值数据异常】【报告统计量】【参考文献】【对比方法】【图像 PS 痕迹】
  几个方向，检查 LaTeX / Word 论文中的常见问题信号——末位数字异常、Benford 偏离、等差数列、重复数据块/行、
  GRIM/GRIMMER 均值与标准差不自洽、正文与表格数字不一致、原始数据（xlsx/csv）中的重复行列与末位异常、t/F/r/χ2/z 检验的 p 值对不上、p 值在 0.05 下方堆积、扭曲短语、引用编号与参考文献不符、
  虚构/错误 DOI 和被撤稿文献（可选联网）；对比表里加粗却不是最优、P/R/F1 自相矛盾、提升幅度算不出来，
  并由模型亲自检索被引的对比方法（baseline）原文，核对基线数值是否被压低、引用是否张冠李戴、文献是否虚构、
  代码链接是否失效（可选联网取回原文）；以及图片的复制重用（含旋转/镜像/缩放后的近重复）、拼接、
  克隆/涂抹等痕迹——最终导出 PDF 报告。
  灵感来自"耿同学"的学术打假方法。当用户要检查论文数据/图片/对比实验是否有造假或虚报风险、做投稿前诚信自查、
  或要生成数据完整性/图像取证报告时使用。
license: MIT
compatibility: Python 3.9+. 数据与文本检查零依赖；Pillow（图片尺寸/拼版/近重复检测）、reportlab（PDF 报告）、PyMuPDF（PDF 矢量图预览、被引论文 PDF 转文本）均为可选；--verify-refs 需要联网访问 api.crossref.org；--fetch-baselines 需要联网访问 export.arxiv.org / arxiv.org、api.openalex.org、api.crossref.org、api.github.com 及文中的代码/数据链接。两者都必须先征得用户同意。
---

# geng-skills · 论文诚信自检

> 灵感来自"耿同学"的学术打假。"耿"既是致敬，也取"耿直"之意。
> 本 skill 面向**作者本人的投稿前自检**：在被别人打假之前，先用同样的方法检查自己的论文。
> 它**不下"造假"结论**——脚本只负责筛信号，是否有问题由你（模型，含视觉能力）判读。

## 适用场景

- 用户给出 `.docx` / `.tex` 文件或 LaTeX 工程目录，想检查数据/图片是否有造假风险。
- 用户想在投稿/送审前做一次科研诚信自查。
- 用户想要一份数据完整性 + 图像取证的 PDF 报告。
- 用户想确认对比实验里引用的基线数值是否与原文一致、有没有虚报（v1.4：对比方法核查）。

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
- `grim_inconsistent` / `grimmer_inconsistent`（medium）：正文里 `M = 3.47, SD = 1.20, n = 20`、`3.47 ± 1.20（n = 20）`、
  `均分为 3.47（SD = 1.20，n = 20）` 这类报告，**只在**该句或前两句说明是整数数据（李克特/量表/评分/计数）时检验；
  单题均分/多题均分、样本/总体标准差都试过仍不可能才报。你要确认变量确实是整数数据：是就按 high，
  是连续数据（反应时、比例、体重）就判"可解释"。
- `text_table_mismatch`（medium）：提到表格的句子里有个数（如 95.3）在所有表格里都找不到，而表格里恰有相邻两位对调的数（93.5），
  多为抄写笔误；请作者核对哪个是对的。

**原始数据文件（可选，先问用户有没有）**：如果用户有论文背后的原始数据（`.xlsx / .csv / .tsv`），一起传进来：
```
python scripts/geng_check.py data --input <...> --out findings.json --data-file raw.xlsx [--data-file other.csv]
```
会逐列做末位数字检验（只查取值范围足够宽的列）、整表 Benford、含小数的重复行（`duplicate_row`）、逐行相同的两列
（`duplicate_column`）、连续数据列里的重复片段（`duplicate_data_block`）。编号列自动跳过；位置写成 `文件#工作表!列(列名)`，
行号与 Excel 里一致。`.xls` 请用户先另存为 `.xlsx`。原始数据只在本地读取，不会上传。

**联网核对参考文献（可选，先征得用户同意）**：
```
python scripts/geng_check.py data --input <...> --out findings.json --verify-refs [--mailto 用户提供的邮箱]
```
会把参考文献的 DOI 和条目文本发送到 Crossref 公共 API：DOI 不存在（`reference_doi_not_found`，
可能是虚构/幻觉引用或 DOI 抄错）、DOI 与标题不符（`reference_doi_mismatch`）、被引论文有撤稿/撤回/关注声明
（`cited_retracted_work`）、无 DOI 的英文条目在 Crossref 找不到（`reference_not_found`，low，书籍/会议常查不到）。
不要替用户编造 `--mailto`。

正文里写全 `M / SD / n` 的整数数据已自动做了 GRIM/GRIMMER；写法不规范（n 在别的段落、表格里的均值 ± SD 配表头的 n）时，
对可疑者手动补跑 GRIM / GRIMMER / SPRITE-lite：
```
# GRIM：报告均值在该样本量+小数位下是否数学上可能（多题量表均分加 --items 题数）
python scripts/geng_check.py grim   --mean 3.45 --n 20 --decimals 2

# GRIMMER：整数数据的均值和标准差能否同时成立（SD 默认按样本标准差，--population 按总体；多题均分加 --items）
python scripts/geng_check.py grimmer --mean 3.00 --sd 0.85 --n 5 --decimals 2

# SPRITE-lite：有界数据（量表/百分比等）的报告标准差是否超过理论上界
python scripts/geng_check.py sprite --mean 2.0 --sd 3.0 --n 20 --min 1 --max 7
```
手动的 GRIM / GRIMMER / SPRITE-lite 是"失误无法解释"的数学不可能型硬信号（前提是数据确为整数/有界），发现不一致按 high 处理。
GRIM 输出 `informative=false` 时（n×题数 ≥ 10^小数位）任何均值都能通过，不要把"通过"当作证据。

### 步骤 1c · 对比方法核查（Baseline verification，v1.4）

`data` 已经离线做了对比表检查，并在 `findings.json` 旁写出**对比方法档案** `<out 文件名>.baselines.json`
（也在 findings.json 的 `baselines` 字段）：每张对比表的列与指标方向、本文方法行、每个对比方法的数值/引用/对应参考文献/
复现声明、检索链接，以及正文里的数据集描述（样本数、类别数、划分）和代码/数据链接。离线信号：
- `best_marked_not_best`（medium）：加粗的值不是本列最好（按表头 ↑/↓ 和指标名判断方向）；
- `metric_inconsistent`（medium/low）：F1 不在 P、R 之间或偏离 2PR/(P+R)、准确率等有界指标超过 100% 或量纲混乱、标准差为负；
- `baseline_value_inconsistent`（medium）：同一个被引对比方法在两张表的同一列数值不同；
- `improvement_claim_mismatch`（low）：正文"提升了 3.2 个百分点 / improves by 3.2%"在表里任何一对数都算不出来。

**联网取回被引原文（可选，先征得用户同意）**：
```
python scripts/geng_check.py data --input <...> --out findings.json --fetch-baselines [--max-baseline-papers 30] [--mailto 用户提供的邮箱]
```
会把被引对比方法的 arXiv 编号 / DOI / 标题发到 arXiv、OpenAlex、Crossref 检索，把能公开获取的全文（arXiv HTML/PDF、开放获取 PDF）
存到 `findings.json` 同目录的 `baseline_sources/*.txt`，并检查文中代码/数据链接（含 GitHub API）。不要替用户编造 `--mailto`。新增信号：
`baseline_value_not_in_source`（本文给该基线的数在原文全文里找不到）、`baseline_value_differs_from_source`（原文有该方法的结果行但数不同；
本文把基线报得更差时为 high）、`baseline_name_not_in_source`、`cited_baseline_not_found`（疑似虚构引用）、`baseline_source_abstract_only`、
`proposed_results_in_cited_work`（本文结果出现在被引文献里，high）、`claimed_link_unreachable`、`baseline_newer_than_paper`。
已声明复现/重新运行（†、表注、正文）的基线，找不到原文数值时降为 info。

**然后你亲自核对**（详见 [references/baseline_red_flags.md](references/baseline_red_flags.md)）：
1. 读档案。按优先级逐个看对比方法：有信号的、每张表里**最强的基线**、决定"本文最优"的那一列。打开 `baseline_sources/` 里的原文；
   没有原文或只有摘要、而你有网页检索/抓取工具时，**自己去找原文**（arXiv、OpenReview、ACL Anthology、CVF 开放获取、出版社页面）。
   在原文里找**同一数据集、同一划分、同一指标、同一设置**（骨干网络、预训练、额外数据、输入尺寸、shot 数等）的结果，记下表号和版本。
2. 判读为：一致 / 基线被压低 / 基线被抬高 / 设置不同（不可直接比） / 原文无此结果 / 引用张冠李戴 / 无法核实。
   先排除正当解释：作者重新运行过并已说明、划分或协议不同、top-1 与 top-5、macro 与 micro、错误率与准确率、arXiv v1 与正式版数字不同、四舍五入。
3. 再筛查：同一基准上明显更强、在本文之前已发表的公认结果被遗漏（只有能确证并写出来源时才提）；不公平比较（本文用更大骨干、额外数据却没说明）；
   `datasetClaims` 里的数据集统计与原始数据集论文/官方页面不符或自相矛盾；代码链接失效或是空仓库；本文结果照搬被引文献。
4. 写进 `verdicts.json` 的 `baselineVerdicts`（见步骤 3）。**绝不编造原文数值**：没找到就写"无法核实"。

不联网时，档案里的 `retrieval` 链接和你自己的检索能力同样可以完成第 1–4 步；做不了就在整体结论里说明"对比方法未核对原文"。

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
  ],
  "baselineVerdicts": [
    {"table": "Table 2", "method": "ResNet-50", "ref": "[12]", "column": "CIFAR-10 Acc",
     "reportedHere": "91.2", "reportedInSource": "93.0 (Table 3, arXiv v2)", "source": "https://arxiv.org/abs/…",
     "verdict": "基线被压低|一致|基线被抬高|设置不同（不可直接比）|原文无此结果|引用张冠李戴|无法核实",
     "severity": "high|medium|low|info", "comment": "依据：原文哪张表、什么设置"}
  ]
}
```
`verdicts[].id` 对应 `findings.json` 里的 finding id。未列出的 finding 视为"未单独判读"。
`baselineVerdicts` 每个核对过的对比方法一行（一致的也写，severity 用 info），数据集描述、代码链接问题也可以写在这里（`table` 写"正文"）。

### 步骤 4 · 生成报告
```
python scripts/geng_check.py report --findings findings.json --verdicts verdicts.json --out report.pdf
```
得到 `report.pdf`（含中文字体、风险分级表、对比方法核查表、图像判读表、整体结论、免责声明）。
无 reportlab 时自动产出 `report.html`。报告格式见 [references/report_format.md](references/report_format.md)。

## 严重度判读尺度（建议）

- **high**：跨样本重复数据块/重复行（≥3 个数值，原始数据里含小数的重复行）、末位数字极端偏斜（p<1e-6）、已确认是整数数据的 GRIM/GRIMMER 不一致、SPRITE-lite 标准差不可能、
  报告 p 值的显著性结论与统计量矛盾、引用了已撤稿论文、
  对比方法的数值经核对原文确认被压低且未声明重新运行、本文结果与被引文献的结果雷同且非本人前作、
  图片像素级重复使用或同一图片代表不同条件（含旋转/镜像后再用）→ 强烈建议复核。
- **medium**：完美等差数列、Benford 显著偏离、两个数值完全相同的重复行（如消融表两行一致）、可疑拼接缝、
  p 值与统计量不符、多个扭曲短语、DOI 不存在或与标题不符、正文引用编号超出参考文献列表、
  正文与表格数字对调、原始数据两列完全相同、加粗的不是最优、F1 超出 P/R 范围、同一基线跨表数值不同、
  基线数值在原文找不到、被引基线文献查无此文、声称可获取的代码链接失效 → 需要解释。
- **low / info**：小数位异常不一致、轻度偏离，多半有正当原因（如去尾零），提醒留意即可；
  提升幅度对不上、设置不同但未注明、只拿到摘要的基线、已声明复现的基线数值不同。

不要仅因为"数据很整齐""图片很干净"就判高风险；也不要放过"不小心无法解释"的硬信号——
这正是耿同学的判断准则：图片可能误用，但不可能"不小心 PS"；数据相似到那种程度，也不是失误能解释的。

## 限制

- 图像只做**篇内**比对（本文档内图片之间），不做跨论文/全库比对（那需要外部数据库，如 ImageTwin、Proofig）。
  近重复检测只看照片类子图；子图内部的局部克隆（一小块区域被复制）仍要靠看图。
- p 值重算只覆盖正文中写全统计量和自由度的检验；表格里的统计量、只写 `p < 0.05` 的结果无法检查。
- Crossref 收录以外的文献（多数中文期刊、书籍、学位论文、网页）联网也核对不了，查不到≠不存在。
- 数据检查依赖能从 docx 表格 / LaTeX `tabular` 中解析出数值；图片里的数据、公式渲染的数值无法读取。
- 正文 GRIM/GRIMMER 只覆盖同一句里写明 n 的报告；正文与表格的比对只抓相邻数字对调这类笔误，不判断数字含义。
- 对比方法核查只覆盖能对应到参考文献的基线。付费墙论文、扫描版 PDF、数字只出现在图里的结果，脚本核对不了，要靠你检索或请作者核对；
  排行榜（Papers with Code 等）的数字可能与论文不同，以论文原文为准并注明版本；PDF 抽出的文本会丢表格结构，"原文同一行"只是近似；
  方法名换了叫法时可能漏检或误报"原文中没有该方法名"；多数中文期刊没有开放全文。
- 所有"造假"判断都需人工/模型确认；本工具只提供线索与报告。
