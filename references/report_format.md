# 报告格式规范

`geng_check.py report` 合并 `findings.json`（脚本筛出的数据信号）与 `verdicts.json`（模型判读）
生成报告。优先 PDF（reportlab），无 reportlab 时降级 HTML。

## 必备元素

1. **标题**：`geng-skills · 论文诚信自检报告`
2. **元信息区**：文档名、生成时间、检测类型（数据完整性 + 图像取证 · 投稿前自检）、风险统计（高/中/低/图像问题计数）。
3. **整体结论**（`verdicts.overallAssessment`）：一段给作者的复核建议。
4. **数据异常 + 模型判读表**：列 `ID / 风险 / 类型 / 位置 / 信号说明+统计 / 模型判读`，按严重度排序，高风险行高亮。
5. **图像取证表**（`verdicts.imageVerdicts`）：列 `图片 / 风险 / 问题 / 说明`。
6. **免责声明**（必须）：
   > 本报告为投稿前科研诚信【自检】参考，不构成造假结论，亦不对应任何官方检测系统。

## PDF 技术要求

- 用 `reportlab` 直接生成，不依赖 LibreOffice / Office。
- A4，合理页边距；中文字体自动探测（msyh / SimHei / SimSun），找不到则回退 Helvetica（中文可能缺字）。
- 表头跨页重复（`repeatRows=1`）；长文本自动换行；页脚带页码。

## 降级要求

无 reportlab 时输出 HTML，并在终端明确提示"已降级生成 HTML"。HTML 保留同样的元信息、卡片、两张表与免责声明。

## verdicts.json 约定

```json
{
  "overallAssessment": "string",
  "verdicts": [
    {"id": "data-0001", "verdict": "需复核|确认问题|可解释", "comment": "string"}
  ],
  "imageVerdicts": [
    {"file": "image1.png", "severity": "high|medium|low|info", "issue": "string", "comment": "string"}
  ]
}
```
`verdicts[].id` 必须对应 `findings.json` 中的 finding id；未列出的 finding 在报告里判读列显示 `—`。
