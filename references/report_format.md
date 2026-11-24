# 报告格式规范

`geng_check.py report` 合并 `findings.json`（脚本筛出的数据信号）与 `verdicts.json`（模型判读）
生成报告。优先 PDF（reportlab），无 reportlab 时降级 HTML。

## 必备元素

1. **标题**：`geng-skills · 论文诚信自检报告`
2. **元信息区**：文档名、生成时间、检测类型（数据完整性 + 统计量与参考文献 + 对比方法核查 + 图像取证 · 投稿前自检）、风险统计（高/中/低/对比方法问题/图像问题计数）。
3. **整体结论**（`verdicts.overallAssessment`）：一段给作者的复核建议。
4. **数据异常 + 模型判读表**：列 `ID / 风险 / 类型 / 位置 / 信号说明+统计 / 模型判读`，按严重度排序，高风险行高亮。
   类型列显示中文名（如"基线数值与原文不同"），下方小字附 finding type 原名。
5. **对比方法核查表**（v1.4，`verdicts.baselineVerdicts`）：先一句脚本摘要（对比表数、对比方法数、联网取回篇数、各类自动信号数），
   再列 `表 / 方法 / 引用 / 指标列 / 本文所报 / 原文 / 判读（含风险） / 说明（含原文链接）`，按 severity 着色。
6. **图像取证表**（`verdicts.imageVerdicts`）：列 `图片 / 风险 / 问题 / 说明`。
7. **免责声明**（必须）：
   > 本报告为投稿前科研诚信【自检】参考，不构成造假结论，亦不对应任何官方检测系统。

## PDF 技术要求

- 用 `reportlab` 直接生成，不依赖 LibreOffice / Office。
- A4，合理页边距；中文字体自动探测（msyh / SimHei / SimSun），找不到则回退 Helvetica（中文可能缺字）。
- 表头跨页重复（`repeatRows=1`）；长文本自动换行；页脚带页码。

## 降级要求

无 reportlab 时输出 HTML，并在终端明确提示"已降级生成 HTML"。HTML 保留同样的元信息、卡片、三张表与免责声明。

## verdicts.json 约定

```json
{
  "overallAssessment": "string",
  "verdicts": [
    {"id": "data-0001", "verdict": "需复核|确认问题|可解释", "comment": "string"}
  ],
  "imageVerdicts": [
    {"file": "image1.png", "severity": "high|medium|low|info", "issue": "string", "comment": "string"}
  ],
  "baselineVerdicts": [
    {"table": "Table 2", "method": "ResNet-50", "ref": "[12]", "column": "CIFAR-10 Acc",
     "reportedHere": "91.2", "reportedInSource": "93.0 (Table 3, arXiv v2)", "source": "https://arxiv.org/abs/…",
     "verdict": "一致|基线被压低|基线被抬高|设置不同（不可直接比）|原文无此结果|引用张冠李戴|无法核实",
     "severity": "high|medium|low|info", "comment": "string"}
  ]
}
```
`verdicts[].id` 必须对应 `findings.json` 中的 finding id；未列出的 finding 在报告里判读列显示 `—`。
`baselineVerdicts[].reportedInSource` 只能写在原文中亲眼找到的数值，找不到写"无法核实"，不要编造。
数据集描述、代码链接等非表格问题也可以写进 `baselineVerdicts`（`table` 写"正文"）。
