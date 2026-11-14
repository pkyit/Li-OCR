# Li-OCR R1.0

金融单据（发票 / 回单 / 合同 / 收据 / 结单 / 报销单）的本地 OCR 识别 + 人工复核 + 数据沉淀闭环。

R1.0 的目标不是"上线"，而是**证明这条链路在自己的显卡上能跑通、效果可接受、并且越用越准**：

- 一次上传十几份文件，后台排队识别，结果落库；
- 把"可疑字段"按原因归类，给复核人员一个原件/字段并排的工作台；
- 人工改正的数据沉淀为 Gold 样本，可导出为训练集；同时自动扩充领域词表，立刻改善后续抽取。

> 算力口径：本机 RTX 4060 8GB 上跑通即视为可行。日均 1000 份属于公司算力采购问题，不在 R1.0 验收范围内。

---

## 1. 快速开始

```powershell
# 1) 准备虚拟环境（已装 PaddleOCR 的 GB 级依赖时可直接复用）
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2) 配置（口令等敏感项只放 .env，禁止入库）
Copy-Item .env.example .env

# 3) 启动
powershell -File scripts\start.ps1 -Port 8080
# 打开 http://127.0.0.1:8080/
```

已知本机可用的解释器路径示例：`D:\Documents\qwen-agent\cu53ekjkj4\default\.venv_paddleocr\Scripts\python.exe`
（`scripts\start.ps1 -Python <该路径>` 即可）。

首次启动会加载 PP-OCRv5 模型（约 5~8 秒），之后每页识别稳定在 0.2~1.1 秒。

---

## 2. 使用流程

1. **上传**：拖拽多份文件到首页（单次 ≤50 份，单文件 ≤200MB），SHA256 相同自动去重。
2. **识别**：后台队列异步处理，列表页自动刷新进度；同一页面可看到类型、页数、可疑字段数。
3. **看可疑问题**："可疑问题"页按「字段 × 原因」聚合，一眼看出这批单据错在哪。
4. **复核**：进入复核页，左侧原件、右侧字段；点字段高亮定位框，改完点"确认并下一单"。
5. **沉淀**：确认后的样本进入 Gold 数据集（"统计与飞轮"页可导出），领域词表自动扩充。

复核页支持键盘流：`Tab/↑/↓` 流转、`Enter` 下一项、`Ctrl+Enter` 提交整单。

---

## 3. 处理链路

```
上传 → 归档(SHA256) → 逐页判别 → 取证 → 抽取 → 校验 → 决策 → 复核/落库 → 数据集
```

| 环节 | 实现 | 说明 |
|---|---|---|
| 页面判别 | `backend/app/pipeline/io.py:triage_kind` | 文本层可信 / 扫描 / 拍摄 |
| 确定性抽取 | `io.py:_lines_from_pdf_text` | PDF 文本层直接按字符坐标重建行，不跑模型 |
| 主识别 | `engines.py:PaddleEngine` | PP-OCRv5 mobile（可切 server），GPU |
| 旁路识别 | `engines.py:RapidEngine` | CPU、独立实现与独立权重，制造"第二意见" |
| 手写/低质量兜底 | `engines.py:VLEngine` | 页面质量门触发 PaddleOCR-VL 补文本证据，用完即卸 |
| 字段抽取 | `pipeline/extract.py` | 标签几何 + 类型可信性 + 别名优先级 |
| 无标签强模式 | `extract.py:_pattern_fields` | 卡号/税号缺失时按 Luhn/校验位"自证"，标 `INFERRED` |
| 可疑判定 | `pipeline/validate.py` | 10 类信号，全部带原因码 |
| 复核留痕 | `review.py` | 校正 append-only，永不覆盖原值 |
| 数据集 | `datasets.py` | 只导出人工确认样本 |

关键设计取舍：

- **交叉验证不产生真值**。两引擎一致只作为证据；冲突一定进人工复核。
- **同源风险**。PaddleOCR 与 RapidOCR 同属 PP-OCR 系，字形混淆会同时错；因此还有规则/校验位/勾稽三道独立防线。
- **金额勾稽**：`不含税 + 税额 = 价税合计`，不平直接标红。
- **税号校验**：统一社会信用代码按 GB32100-2015 算校验位，识别串字能当场抓出。
- **手写不硬扛**：页面质量门（字符量 / 平均置信度 / 低置信占比）触发 VL 兜底；VL 只提供文本证据、不参与数值裁定，低质量页一律强制人工复核。
- **不猜字段**：卡号无标签时只接受能通过 Luhn 的候选，并以 `INFERRED` 标明"推断而来"；宁可 MISSING，也不凭空造值。

### 可疑原因码

| 原因码 | 含义 | 处理建议 |
|---|---|---|
| `CROSS_ENGINE_DIFF` | 双引擎结果不一致 | 最高优先级复核 |
| `MISSING` | 必填字段没抽到 | 补录 / 补规则 |
| `SUM_MISMATCH` | 金额勾稽不平 | 检查列归属或数字 |
| `CHECKSUM_FAIL` | 税号校验位不过 | 多为识别串字 |
| `UNPARSEABLE` | 抽到了非本字段的文本 | 修别名或列归属 |
| `FORMAT` | 金额小数位/格式异常 | 规范口径 |
| `DATE_OUT_OF_RANGE` | 日期明显不合理 | 年份识别错误 |
| `LOW_CONF` | 模型置信度低 | 人工确认 |
| `INFERRED` | 无标签、由强模式推断 | 核对原件与推断值 |
| `VL_FALLBACK` | 该值来自 VL 兜底 | 与页面截图核对 |

---

## 4. 自迭代闭环（越用越准）

R1.0 实现两级，收益从快到慢：

1. **L1 词表纠错（小时级生效）**
   复核时改正的「销售方/购买方/开户行/币种」会写入 `lexicon` 表；
   后续抽取遇到近似值（相似度 ≥0.86）会自动纠正，并在字段上标 `LEXICON_FIXED`。
2. **Gold 数据集沉淀（下个训练周期生效）**
   "统计与飞轮 → 导出数据集"产出：
   - `fields.jsonl`：业务字段真值（含来源、置信度、是否人工改过）；
   - `corrections.jsonl`：校正事件全量留痕；
   - `rec_labels.txt` + `crops/`：字段裁剪图与**图像观测文本**，可直接用于识别模型微调；
   - `manifest.json`：版本、口径、样本量。

**硬规则**：只有人工确认（Gold）样本能进训练集；双引擎一致但无人确认的样本只能进评测集。
人工改过的字段不写入 `rec_labels.txt`——图里还是错的字，拿改后的值当标签会教坏模型。

---

## 5. 实测结果（本机 RTX 4060 8GB）

18 份文件 = 14 份合成单据（覆盖发票/回单/合同/收据/结单/报销单，含税号错、勾稽不平、缺日期、金额格式异常、拍摄模糊）+ 4 份真实样本（汇丰结单 2 页、手写 PDF、报销单照片）。

| 指标 | 结果 |
|---|---|
| 单页识别（GPU，PP-OCRv5 mobile） | 0.19~1.1 s（2MP 页面） |
| 单文档端到端（含 CPU 旁路） | 1.8~5.0 s |
| 文本层 PDF（确定性路径） | 0.14 s |
| 手写/低质量页（VL 兜底） | 30~33 s/页（含首次约 20 s 模型装载，用完释放显存） |
| 字段总数 | 139 |
| 文档自动通过率（首轮） | 38.9%（7/18） |
| 字段可疑率 | 15.1% |
| 单页平均识别耗时 | 1.0 s |
| 引擎 | PaddleOCR + RapidOCR（双引擎），手写页追加 PaddleOCR-VL |

合成单据字段基本全对；**真实样本明显变差**，这正是要暴露的信息：

- 汇丰结单（英文表头 + 密集表格）：中文标签规则命中不到账号/日期，金额列可能取错；
- 手写 PDF：VL 已能读出卡号 `5413 7508 4201 5703`（原识别把 `1` 看成 `/`，靠 Luhn 还原），
  但标注为 `INFERRED` + `VL_FALLBACK`，整单强制人工复核——推断值不进自动通过；
- 报销单照片：`BX-` 被识别成 `8X-`，且两引擎同时错、置信度 0.93，自动通过——**这是同源共错的真实案例**，靠校验/复核兜底。

---

### 5.1 手写页修复与 VL 兜底（2026-11-14）

起因：手写页（汇丰万事达卡）只能抽出 2 个垃圾字段，连银行卡号都识别不到。定位出三个问题：

| 问题 | 根因 | 修复 |
|---|---|---|
| 手写页没有专门路径 | 只有 PP-OCRv5，低质量页也按普通扫描页处理 | `runner._page_quality()` 质量门，触发后调 `VLEngine` 兜底 |
| 卡号抽不到 | 只做"标签→几何取值"，手写页没有"卡号："标签 | `extract._pattern_fields()` 全页扫数字串，`/`→`1` 混淆修复 + Luhn 自证，标 `INFERRED` |
| VL 一加载就报错 | 工作线程处于静态/PIR 模式，`is_bfloat16_supported()` 拿到 `Place(undefined:0)` | `VLEngine._ensure()` 先 `paddle.disable_static()` 再 `set_device()` |

另外发现 VL 常驻占 7893/8188 MiB 显存，会与主引擎抢资源，因此加 `VLEngine.release()`，每单处理完卸载并清空缓存（回落到约 4.0GB）。

VL 兜底逻辑：**只对非文本层且质量门判定为低的页面启用**（字符量 < 40，或均置信度 < 0.80，或低置信占比 > 0.35）；
VL 输出的行只提供文本证据、没有坐标，禁止几何邻近取值，也不参与金额勾稽等数值裁定；
命中过 VL 的页面记为 `HANDWRITE_LOW_QUALITY`，整单强制 `NEED_REVIEW`。

验证：该手写页抽出 `account_no = 5413 7508 4201 5703`（`INFERRED` + `VL_FALLBACK`），耗时约 41s（VL 30~33s）；
全量 18 份回归只有这一页触发 VL，其余 17 份 `vl_ms=0`，首轮自动通过率仍 38.9%，无误报卡号。

## 6. 已知限制（R1.0 明确不做）

| 限制 | 影响 | 计划 |
|---|---|---|
| 未解析表格明细行 | 银行结单只抽表头字段，流水行不进系统 | R1.1：列归属 + 行级复核 |
| 英文/中英混排表头 | 账号、日期等可能 MISSING | R1.1：多语言别名表 |
| 手写、盖章遮挡 | 手写页已由 VL 兜底，但慢（30 s/页）且只能补文本；盖章遮挡仍不可用 | R1.2：VL 扩展到复杂表格 + 并发准入 |
| 字段 bbox 为整行 | 复核高亮粒度是行，不是值 | R1.2：词级坐标 |
| 双引擎同源 | 相关错误无法互相发现 | 需换一套非同源权重（NEEDS CHECK） |
| 无认证体系 | 仅有可选静态口令 | R1.1：接入公司 SSO |

---

## 7. 部署

### 本地（推荐，带 GPU）

```powershell
powershell -File scripts\start.ps1 -Python <venv python> -Port 8080
```

### K8s（CPU，已实测）

集群当前**没有 GPU 设备插件**，K8s 侧只能跑 CPU 版（自动降级为 RapidOCR 单引擎）：

```powershell
# 可选但强烈建议：设置访问口令（写入 Secret，不进仓库）
powershell -File deploy\k8s\deploy.ps1 -Token "your-strong-token"
# 访问 http://<node-ip>:30080/
```

PoC 路径不依赖镜像仓库：源码打包进 ConfigMap，initContainer 现场 `pip install` 到 emptyDir，
数据落 NFS PVC（`liocr-data`）。**改代码后必须重新执行脚本**（内含 `rollout restart`），
否则 Pod 模板没变、旧代码还在跑。

实测（`li-ocr` namespace，node2）：Pod 1/1 Running；上传一份发票 6~9 s 返回，
10 个关键字段全对；`/api/config` 显示 `paddle.ready=false / rapid.primary=true`。
CPU 单页约 6~9 s，比本机 GPU 慢 3~5 倍。

生产请用 `deploy/Dockerfile` 构建镜像后替换 Deployment 的 image。

### 环境变量

见 [.env.example](.env.example)。常用：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LIOCR_DEVICE` | `gpu:0` | 无 GPU 填 `cpu` |
| `LIOCR_OCR_MODEL` | `mobile` | 可切 `server`（更准更慢） |
| `LIOCR_WORKERS` | `1` | 8GB 显存建议 1 |
| `LIOCR_CROSSCHECK` | `auto` | `off` 关闭 CPU 旁路 |
| `LIOCR_VL_FALLBACK` | `auto` | `off` 关闭手写/低质量页的 VL 兜底 |
| `LIOCR_API_TOKEN` | 空 | 非空则所有 `/api/*` 需带 `X-Api-Token` |
| `LIOCR_DATA_DIR` | `./data` | 原始件与派生数据根目录 |

---

## 8. 安全约定

- **凭据只走环境变量**：`.env`、kubeconfig、密钥文件均在 `.gitignore` 中，禁止入库。
- **原始件不可变**：上传后按 SHA256 归档，只读不覆盖；派生数据可重跑重建。
- **数据不出域**：全部本地/私有云推理，不调用外部 API。
- **口令对比用常量时间**（`secrets.compare_digest`），避免时序侧信道。
- 删除单据只删索引与页面图，原始件保留（合规审计需要）。

---

## 9. 目录结构

```
backend/app/
  main.py          HTTP 接口与静态页挂载
  review.py        复核提交、校正留痕、词表学习
  datasets.py      Gold 数据集导出（含裁剪图与观测文本标签）
  stats.py         自动通过率/可疑率/纠正率
  pipeline/
    io.py          渲染、文本层重建、页面判别
    engines.py     PaddleOCR / RapidOCR / PaddleOCR-VL 适配
    extract.py     规则抽取（别名优先级、边界、类型可信性）
    validate.py    校验与可疑判定
    runner.py      队列、编排、落库
frontend/          无构建静态页（列表/复核/可疑/统计）
tools/             样例生成、批量导入、字段排查
deploy/            Dockerfile 与 K8s manifests
.memory/           决策/缺陷/风险记录（按日期时间归档）
.skill/            可复用套路
```

---

## 10. 路线图

- **R1.1**：表格明细解析与行级复核；多语言标签；BFF 鉴权；SSE 推送替代轮询。
- **R1.2**：VL 按页路由扩展到复杂表格，单卡串行队列 + 准入控制；词级 bbox。
- **R1.3**：识别模型微调闭环（数据版本、训练、影子、晋升门禁）与黄金评测集。
