# Bug：手写页识别太差、银行卡号完全抽不到

- 时间：2026-11-14 18:35 (+08:00)
- 类型：bug / 决策
- 影响范围：手写页、无标签卡号、VL 兜底链路、显存
- 状态：已修复并回归

## 1. 现象

用户复核截图（`handpdf_p1.png`，汇丰万事达卡手写页）指出"手写识别太差，最起码的银行卡号都没找出来"。
当时该单只产出 2 个字段：`doc_date=MISSING` + 一条 `LOW_CONF` 的附加字段。

## 2. 根因（四个独立问题叠加）

1. **R1.0 没有手写路径**：`runner` 只走 PP-OCRv5，手写页置信度 0.64、低置信占比 0.4，
   但当时没有任何"质量门 → 换模型"的逻辑，直接按普通扫描页处理。
2. **没有无标签取值**：手写页压根没有"卡号："这种标签，`extract.py` 只做标签几何匹配，数字写在行里就永远抽不到。
3. **VL 在服务工作线程里初始化失败**：`is_bfloat16_supported(): ... Invoked with: Place(undefined:0)`。
   原因：warmup 线程跑完 PaddleOCR 后，进程处于静态/PIR 模式（`in_dygraph_mode()=False`），
   而 `paddle.amp.is_bfloat16_supported()` 无参调用会走 `_current_expected_place()` → `core.Place()`（undefined）。
   修复：`VLEngine._ensure()` 里先 `paddle.disable_static()` 再 `paddle.set_device(device)`，再建 `PaddleOCRVL`。
   证据日志：`VL init: dygraph=False pir=True place=Place(undefined:0)` → `VL init ready: dygraph=True pir=False place=Place(gpu:0)`。
4. **VL 常驻吃满显存**：加载后实测 7893/8188 MiB，后续主引擎识别有 OOM 风险。
   修复：`VLEngine.release()` 在 worker 每单结束后卸载并 `empty_cache()`，显存回落到 ~4.0GB。

## 3. 修复内容

- `pipeline/base.py`：`luhn_ok()`、`card_variants()`（`/`→`1`，`O/o`→`0`，`l/I`→`1`）。
- `pipeline/engines.py`：新增 `VLEngine`（懒加载、兼容 `res.markdown` 为 str/dict、回退 `parsing_res_list`、
  动态图态修复、`release()`）；`engines_summary` 增加 `vl` 且受 `LIOCR_VL_FALLBACK` 控制。
- `pipeline/runner.py`：`_page_quality()`（字符量<40 / 均置信度<0.80 / 低置信占比>0.35）；
  非文本层且低质量页触发 VL，合并行并把 `page_kind` 记为 `HANDWRITE_LOW_QUALITY`；
  低质量页一律 `NEED_REVIEW`；记录 `vl_ms`；worker 每单 finally 释放 VL。
- `pipeline/extract.py`：VL 行禁止几何邻近取值（无坐标）；`_pattern_fields()` 在 `account_no` 缺失时
  扫 16~19 位数字串，**只接受通过 Luhn 的候选**（`/`→`1` 修复后重算），标 `INFERRED`/`VL_FALLBACK`。
- `pipeline/validate.py`：保留来源标记；账号 16~19 位纯数字再算一次 Luhn，失败标 `CHECKSUM_FAIL`。
- 前端：`FLAG_LABEL` 增加"推断/VL兜底"，复核页对低质量页显示提示横幅。

## 4. 验证（本机 RTX 4060 8GB）

手写单 `4dd0314bb74d486e`：

- `page_kind=HANDWRITE_LOW_QUALITY`，`engine=paddle-mobile+vl`
- `account_no = 5413 7508 4201 5703`（VL 读出 `541420...` 形态，`/` 修复后 Luhn 通过）
- `flags=INFERRED,VL_FALLBACK`，单据 `NEED_REVIEW`
- `vl_ms≈30~33s`（首次含 ~20s 装载），`total≈41~44s`；结束后显存 4.0GB

全量 18 份回归：

- 只有手写页触发 VL，其余 17 份 `vl_ms=0`，`page_kind` 不变；
- 无新增误报卡号（日期串/流水号过不了 Luhn，不会被当成卡号）；
- 字段总数 139（+1），首轮自动通过率仍 38.9%（7/18），可疑率 15.1%。

## 5. 经验

- **先证明模型读得到，再谈抽取**：VL 原始输出里卡号是有的（`54137508420/5703`），问题在"没有路径 + 没有无标签兜底"。
- **质量门用统计量，不用单据类型猜**：字符量/均置信度/低置信占比三个指标同时看，避免按模板写死。
- **推断值必须可自证**：卡号靠 Luhn、税号靠 GB32100，过不了就保持 MISSING，绝不"看起来像"就填。
- **重模型用完即卸**：8GB 卡上 VL 常驻会挤死主引擎；释放的 20s 重载成本远低于 OOM 事故。
- Paddle 的"当前设备"与静态/动态图状态是进程级全局量，后台线程里建 VL 前必须显式校正。

## 6. 文档同步（2026-11-14 18:45）

- `README.md` 新增 `5.1 手写页修复与 VL 兜底`：三个问题的根因/修复对照表、显存释放、质量门阈值、验证结论。
- `README.md` 同步更新：处理链路表（VLEngine、_pattern_fields）、可疑原因码（INFERRED/VL_FALLBACK）、
  实测耗时（VL 30~33s/页）、已知限制、环境变量 `LIOCR_VL_FALLBACK`、R1.2 路线图。
- `.env.example` 增加 `LIOCR_VL_FALLBACK=auto`；`.skill/financial-doc-field-extraction` 增加"无标签字段"套路与两个 VL 坑。
