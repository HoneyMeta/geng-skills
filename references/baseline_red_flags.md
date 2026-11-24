# 对比方法核查 · 判读参考（v1.4）

论文里"本文方法优于 X、Y、Z"的结论，取决于对比表里 X、Y、Z 的数是不是真的。常见问题是：基线数值被压低（报得比原文差）、
引用张冠李戴、设置不同却放在一起比、提升幅度算错，以及引用的论文根本不存在。
`geng_check.py data` 先离线筛出候选信号，并写出**对比方法档案** `<out>.baselines.json`；加 `--fetch-baselines` 时再联网取回被引原文核对数值。
**最后的结论由模型亲自对照原文得出**，写进 `verdicts.json` 的 `baselineVerdicts`。

---

## 一、对比方法档案（`<out>.baselines.json`，也在 findings.json 的 `baselines` 字段）

| 字段 | 内容 |
|---|---|
| `tables[]` | 每张对比表：编号（Table N）、表题、表注、列（名称、指标方向 higher/lower、是否为参数量/耗时这类代价列） |
| `tables[].proposed[]` | 本文方法行（"Ours / Proposed / 本文 / 所提 / 我们的"，或正文"We propose X"里的 X）及各列数值、加粗列 |
| `tables[].baselines[]` | 对比方法行：方法名、各列数值、引用（单元格里的 `\cite` / `[12]` / 作者-年份；没有时到正文找"X~\cite{…}"）、对应参考文献、†/* 标记、披露（`rerun` 重新运行/复现，`quoted` 取自原文；`(table)` 表示表题/表注整体声明，`(paper)` 表示正文声明） |
| `tables[].variants[]` | 消融变体行（w/o、+、仅…），不与原文核对 |
| `references{}` | 被引条目：原文、DOI、arXiv 编号、年份、猜测的标题、检索链接（doi.org、arXiv、OpenAlex、Semantic Scholar、Google Scholar） |
| `datasetClaims[]` | 正文里带数据集名和数量的句子（"contains 60,000 images in 10 classes"、"共 1200 个样本"、训练/测试划分） |
| `links[]` | 正文里的代码/数据链接（GitHub、Zenodo、figshare…）及上下文，`claimedAvailable` 表示句子声称可获取 |
| `sources{}` | （联网后）每篇被引文献的解析结果：标题、年份、来源、是否拿到全文、缓存文件 `baseline_sources/*.txt`、请求记录 |
| `tables[].baselines[].valueChecks` | （联网后）每个数值在原文中是否找到、匹配到原文哪个数 |
| `linkChecks[]` / `fetchErrors[]` / `online` | （联网后）链接检查结果、失败的请求、汇总 |
| `findingIds[]` | 本节相关 finding 的 id，方便回到 findings.json |

`baseline_sources/*.txt` 文件头写明引用方式、标题、年份、URL、全文来源（arxiv-html / arxiv-pdf / oa-pdf）和取回日期；
arXiv HTML 的表格每行一行，单元格用 ` | ` 分隔，便于对照。

---

## 二、离线信号（`data` 总会运行）

### 1. `best_marked_not_best` — 加粗的值不是本列最好（medium；整行加粗时 low）

**为什么重要**：加粗通常表示"最优"。加粗一个不是最优的数，会让读者以为本文方法全面领先。

**判定**：按表头确定方向——`↑/↓`、acc / precision / recall / F1 / AUC / mAP / AP / IoU / BLEU / ROUGE / PSNR / SSIM / R² / 准确率 / 精确率 / 召回率 越高越好；
error / loss / MAE / MSE / RMSE / FID / EER / WER / CER / latency / params / FLOPs / 误差 / 损失 / 错误率 / 耗时 越低越好；
表头看不出时用表题；仍不知道方向时，只在加粗值既非最大也非最小时才报。四舍五入后相等算并列，不报。
同一列有多个加粗值且表格分了组（组标题行）时，按组内比较。

**正当原因**：分组各自加粗最好（未用组标题行分组时脚本看不出来）、整行加粗只是标记本文方法、加粗表示"显著优于"而不是最大值（需在表注说明）。

### 2. `metric_inconsistent` — 指标之间数学上自相矛盾

| 检查 | 严重度 |
|---|---|
| F1 大于 P、R 中的较大者（任何平均方式都不可能） | medium |
| F1 小于 P、R 中的较小者：表头/表题写 micro/二分类 → medium；未写 → low；写 macro/weighted → 不报（macro 平均可能出现） | medium / low |
| F1 与 2PR/(P+R) 相差超过四舍五入（只在未写 macro 且单元格不是"均值 ± SD"时） | low |
| 有界指标（准确率、F1、AUC、IoU…）超过 100%、为负，或本列其他值都是 0–1 小数而这个值大于 1 | medium |
| 标准差为负 | medium |

**正当原因**：多次运行分别算 F1 再取平均（不等于均值 P、R 的调和平均）、macro 平均没写在表头、列错位（P、R 写反不影响区间检查）。

### 3. `baseline_value_inconsistent` — 同一基线在两张表里数值不同（medium）

同名、同引用的对比方法，在两张表的同一列（列名需含数据集等具体信息，纯"Acc"这种列名不比）给出不同数值。
**正当原因**：两张表设置不同（数据预处理、训练轮数、输入尺寸）——必须在表注写明；否则至少有一处抄错。

### 4. `improvement_claim_mismatch` — 正文的提升幅度在表里找不到出处（low）

抓取"improves … by 3.2%"、"outperforms … by 2.1 points"、"a 19.8% relative error reduction"、"提升了 3.2 个百分点"、"提高 3.2%"等，
且句子是在说本文方法（we / our / proposed / 本文 / 我们 / 所提 / 本文方法名）。
用本文方法行与其他每一行、每一列的**绝对差**、**相对变化**（相对对比方法或相对本文）以及**各列平均差**逐一比对（允许表中数和声称数的四舍五入）；
"points / 个百分点"只按绝对差，"relative / 相对"只按相对变化，"%"两种都试。都对不上才报，`stat.closest` 给出最接近的几种解释。
**模型要做的**：确认这句话指哪两个数；数字可能来自图、附录或另一实验——那就请作者在句中写明出处。

---

## 三、联网信号（`data --fetch-baselines`，先征得用户同意）

检索顺序：参考文献里的 arXiv 编号 → arXiv API；DOI → OpenAlex（失败再 Crossref）；都没有 → 用标题在 OpenAlex、arXiv 搜索，
**标题词重合度 ≥ 0.8 且年份相差不超过 2 年**才认为是同一篇。OpenAlex 记录里有 arXiv 版本时也会取 arXiv 全文。
全文优先 arXiv HTML，其次 arXiv PDF / OpenAlex 开放获取 PDF（需 PyMuPDF），都没有就只有题录和摘要。
默认最多 30 篇（`--max-baseline-papers`），arXiv 请求间隔 3 秒、其他 1 秒；网络错误只记进 `fetchErrors`，不会中断。

### 5. `baseline_value_not_in_source` — 本文给该基线的数值在原文全文里找不到

**判定**：只看性能列（参数量、FLOPs、耗时等代价列不参与）。匹配时允许四舍五入（原文 93.03 → 本文 93.0）、
百分数与小数互换（0.930 ↔ 93.0）、准确率与错误率互换（93.57 ↔ 6.43，只用于准确率/错误率列）。
**严重度**：全部找不到 → medium；只有部分找不到 → low；本行有 †/* 且表注说明是复现/重新运行，或表题/正文声明重新运行了基线 → info。
`stat.snippets` 是原文中提到该方法/数据集/指标的片段。

**正当原因**：作者确实重新运行并已声明；数字在原文的图里而不是正文/表格里；引用的是 arXiv v1 而脚本取到的是新版（或反之）；
数据划分/协议不同（top-1 vs top-5、macro vs micro、不同测试集）；原文是扫描版或数字在图片表格里，文本抽取不到。

### 6. `baseline_value_differs_from_source` — 原文有该方法的结果行，但数不一样

**判定**：原文中找到包含该方法名（优先用最具体的写法，如 `DenseNet-BC (k=40)`，找不到才退回 `DenseNet-BC`）的行（PDF 里分散到多行的单元格会合并），
行内有与本文数值同量级的数，却没有本文的数。
**严重度**：medium；若本列方向已知、且无论对应原文行里哪个数，本文都把基线报得更差（差距 > max(0.5, 10%)）→ **high（基线疑似被压低）**；有复现声明 → info。
**模型必须**：打开 `baseline_sources/*.txt` 或原文，确认是同一数据集、同一划分、同一指标、同一设置（骨干、预训练、额外数据、输入尺寸、shot 数），
再判"基线被压低 / 设置不同 / 版本不同"。

### 7. `baseline_name_not_in_source` — 原文全文里没有这个方法名（low）

方法名、它的主要词（含大写/数字的部分，如 `ResNet-110` 的 `ResNet`）、常见缩写展开（SVM → support vector…）都找不到。
**可能**：引用张冠李戴（引了别的论文）；也可能原文用了不同名字（如方法名是后人起的）。只在拿到全文时检查。

### 8. `cited_baseline_not_found` — 被引基线文献查无此文（medium）

arXiv / OpenAlex / Crossref 都给出了明确答复但没有相符记录，且条目像英文学术论文。可能是标题/DOI 写错，或 **AI 幻觉虚构的引用**——
那样表中该方法的数值就没有出处。网络失败（没有任何服务答复）时不报。模型应再用网页检索确认一次。

### 9. `baseline_source_abstract_only` — 只拿到题录/摘要（info）

付费墙、没有 arXiv 版本时常见。`stat.toCheck` 列出要核对的方法和数值；模型有检索/抓取工具时应自己打开出版社页面、
OpenReview、CVF / ACL Anthology 开放获取页面核对。

### 10. `proposed_results_in_cited_work` — 本文方法的结果出现在被引文献里（high）

本文方法行里至少 3 个有效数字 ≥ 3 位的数值，**原样**同时出现在同一篇被引文献里。可能是把别人的结果当成自己的；
也可能本文是该文的扩展版/同一作者的前作或会议版——模型要核对作者名单和本文是否已声明。

### 11. `claimed_link_unreachable` — 代码/数据链接失效

GitHub 链接用 `api.github.com/repos/owner/repo` 检查（404 → 失效；仓库大小为 0 → 空仓库）；其他链接先 HEAD 再 GET，404/410/域名解析失败算失效。
声称"代码可获取"的代码链接失效 → medium；其他 → low。被限流（403/429）或超时只在 `linkChecks` 记为 `unchecked`，不报。

### 12. `baseline_newer_than_paper` — 基线发表年份晚于本文（info）

本文年份来自 LaTeX `\date{}` 或 `--paper-year`。提示引用对象可能不对，或本文年份未更新。

---

## 四、模型核查流程（SKILL.md 步骤 1c 的细化）

1. **排优先级**：先看有信号的基线；再看每张表里**最强的基线**；再看决定"本文最优"的那一列（本文与第二名差距最小的列）。
2. **找原文的同一个数**：同一数据集、同一划分（test/val）、同一指标（top-1/top-5、macro/micro、error/accuracy）、同一设置
   （骨干网络、预训练数据、额外数据、输入尺寸、训练轮数、few-shot 的 shot 数、是否数据增强）。记下原文表号/页码和版本（arXiv v1/v2、正式版）。
3. **分类**：

| 判读 | 含义 |
|---|---|
| 一致 | 原文有同设置下的同一个数（四舍五入、单位换算后） |
| 基线被压低 | 原文同设置下的结果更好，本文报得更差，且未声明重新运行 |
| 基线被抬高 | 本文报得比原文还好（少见；可能抄错行，也可能引用了更强的变体） |
| 设置不同（不可直接比） | 原文数字是另一设置/数据增强/预处理下的结果；不算造假，但应注明 |
| 原文无此结果 | 原文没有在该数据集/指标上报告过；若本文未声明自己跑的，需要作者说明来源 |
| 引用张冠李戴 | 方法与所引论文对不上 |
| 无法核实 | 拿不到原文、数字只在图里、或文献查不到——**不要编造原文数值** |

4. **排除正当解释**：作者已声明重新运行（†/表注/正文）、划分或协议不同、top-1 vs top-5、macro vs micro、error 与 accuracy 互换、
   arXiv v1 与正式版数字不同、四舍五入。
5. **再筛查**：
   - **遗漏更强的公认结果**：只有在能确证（原文/排行榜页面、发表早于本文）时才提，并写出来源；
   - **不公平比较**：本文用了更大的骨干、额外数据、更多训练轮数或预训练，而基线没有，且文中未说明；
   - **数据集描述**：`datasetClaims` 里的样本数、类别数、划分与数据集原始论文/官方页面是否一致，与本文其他地方是否自洽；
   - **代码/数据链接**：失效、空仓库、仓库与论文无关；
   - **结果照搬**：`proposed_results_in_cited_work`。
6. **写入** `verdicts.json` 的 `baselineVerdicts`（格式见 [report_format.md](report_format.md)）。`reportedInSource` 只写在原文里亲眼找到的数，
   找不到就写"无法核实"。

---

## 五、限制

- 付费墙论文、扫描版 PDF、数字只出现在图里的结果，脚本核对不了，只能由模型（有检索工具时）或作者手动核对。
- 排行榜（Papers with Code 等）上的数字可能与论文不同，以论文原文为准，并注明版本。
- 原文文本抽取会丢失表格结构（尤其 PDF），`baseline_value_differs_from_source` 的"同一行"只是近似，必须看原文。
- 方法名匹配基于字符串，换了名字（论文里叫 "Ours-L"、后人叫 "XNet"）会漏检或误报 `baseline_name_not_in_source`。
- 只核对能对应到参考文献的基线；表中没有引用、正文也没写"X [n]"的基线只出现在档案里。
- 中文期刊多数没有 arXiv / OpenAlex 全文，联网核对的覆盖面主要是英文开放获取文献。
