# 实测：R1.0 端到端可行性验证结果

- 时间：2026-11-14 17:26 (+08:00)
- 类型：verify
- 影响范围：整条链路
- 环境：Windows + RTX 4060 8GB + PaddleOCR 3.7.0 + RapidOCR 3.10.0

## 样本

18 份文件：

- 14 份合成单据（`tools/make_samples.py` 生成）：增值税发票 5（含税号校验位错、勾稽不平、缺日期、拍摄模糊、文本层 PDF）、银行回单 2（含缺日期）、收据、结单、采购订单 2（含金额无小数）、报销单、合同（文本层 PDF）；
- 4 份真实样本：汇丰结单 2 页（截图）、手写 PDF 页、报销单照片。

## 复现命令

```powershell
Remove-Item -Recurse -Force data
python -m tools.ingest_local samples "..\ocr_test\cases\hsbc_p1.png" "..\ocr_test\cases\hsbc_p2.png" `
  "..\ocr_test\cases\handpdf_p1.png" "..\ocr_test\test_receipt.png" --sync
python -m tools.inspect_doc --all
```

## 结果

| 指标 | 数值 |
|---|---|
| 端到端总耗时（18 份，含模型冷启动） | 42.8 s |
| 单页 OCR 均值（GPU） | 499 ms |
| 单文档（含 CPU 旁路） | 1.9~5.0 s |
| 文本层 PDF | 0.1~0.5 s |
| 字段总数 | 138 |
| 首次自动通过率 | 7/18 = 38.9% |
| 字段可疑率 | 12.3%（10 可疑 + 7 缺失） |
| 可疑信号分布 | LOW_CONF 8、MISSING 7、SUM_MISMATCH 1、CHECKSUM_FAIL 1、FORMAT 1 |

缺陷类样本全部按设计被拦下：

- 税号校验位错 → `CHECKSUM_FAIL` + `LOW_CONF`；
- 合计 ≠ 不含税 + 税额 → `SUM_MISMATCH`；
- 缺日期 → `MISSING`；
- 金额 `12,000`（无小数）→ `FORMAT`。

合成单据的 13 个字段（发票）与 5~6 个字段（合同/采购/收据）基本全对；
文本层 PDF 走确定性路径，零模型调用。

## 真实样本的退化（重要）

| 样本 | 结果 |
|---|---|
| 汇丰结单（英文表头 + 密集表格） | 只抽出 2~3 个字段，账号/日期 MISSING；金额有误取风险 |
| 手写 PDF 页 | 2 个垃圾字段，`LOW_CONF` |
| 报销单照片 | 字段齐全，但 `BX-` 被识别成 `8X-`，两引擎同时错、置信度 0.93，自动通过 |

最后一条是**同源共错的实证**：交叉验证对此无效，必须靠规则/校验/人工兜底。
已在 `174500-risk-known-gaps.md` 记录。

## 闭环验证

1. 在复核页把模糊发票的购买方由 `上海和贸易有限公司` 改成 `上海鼎和贸易有限公司`；
2. `lexicon` 表新增 `(上海鼎和贸易有限公司, buyer, 1.0)`；
3. 导出数据集 `ds-20261114-172636`：1 单 / 13 字段 / 13 张裁剪图，`fields.jsonl` 业务值正确，
   `rec_labels.txt` 使用观测文本；
4. `/api/stats` 的 `corrected_fields` 从 0 变 1，纠正率 0.7%。

结论：R1.0 的"识别 → 可疑 → 复核 → 沉淀 → 复用"闭环在本机可完整跑通。
