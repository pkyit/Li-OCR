---
name: liocr-deploy-verify
description: Li-OCR（FastAPI + PaddleOCR/RapidOCR）在本地 GPU 与 K8s CPU 环境下的启动、部署、验收检查清单。
---

# Li-OCR 启动与验收套路

## 本地（GPU，首选）

```powershell
# 解释器：复用已装 PaddleOCR 的 venv
powershell -File scripts\start.ps1 -Python D:\...\.venv_paddleocr\Scripts\python.exe -Port 8080
```

验收（按顺序，任一步失败就停）：

1. `GET /api/health` → `{"ok":true}`
2. `GET /api/config` → `engines.paddle.ready=true`、`engines.rapid.enabled=true`
3. 导入若干份：`python -m tools.ingest_local samples --sync`
4. `python -m tools.inspect_doc --all` 看字段与 flags 是否符合预期
5. 浏览器：列表页有数据 → 复核页左侧有定位框（≥3 个）→ 点输入框高亮框跟随
6. 提交一次复核，`/api/stats` 的 `corrected_fields` 增加
7. `POST /api/datasets/export` → 检查 `data/datasets/<version>/` 四个产物齐全

## 性能基线（RTX 4060 8GB，2MP 页面）

| 场景 | 期望 |
|---|---|
| 模型冷启动 | 5~8 s（首个文档） |
| PP-OCRv5 mobile 单页 | 0.2~1.1 s |
| RapidOCR CPU 单页 | 1.4~2.0 s |
| 单文档端到端（双引擎） | 1.8~5.0 s |
| 文本层 PDF | 0.1~0.5 s |

明显超出 → 先查 GPU 是否被其它进程占用（`nvidia-smi`），再查是否在重复加载模型
（`run` 表里 `ocr_ms` 若接近 5 s 且每单都如此，说明实例没复用）。

## K8s（CPU）

```powershell
powershell -File deploy\k8s\deploy.ps1 -Token "<强随机口令>"
# http://<node-ip>:30080/
```

要点：

- 集群无 GPU 设备插件 → 只能 CPU；流水线会自动降级到 RapidOCR 单引擎。
- 无镜像仓库时走 ConfigMap + initContainer 装依赖；**改代码必须重新 apply**。
- 数据在 PVC `liocr-data`，删 Pod 不丢；删 PVC 才丢。
- 首次启动要现场 pip 安装，1~3 分钟；`rollout status` 超时就 `kubectl -n li-ocr logs deploy/li-ocr -c install-deps`。

## 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动即退出，报 paddle 相关 | CUDA/cuDNN 版本不匹配 | 设 `LIOCR_DEVICE=cpu` 或换匹配的 paddle 版本 |
| 所有单据 FAILED | 模型下载失败 | 设 `PADDLE_PDX_MODEL_SOURCE=modelscope`，确认缓存目录有模型 |
| 401 | 设置了 `LIOCR_API_TOKEN` | 前端会弹框输入，或用 `X-Api-Token` 头 |
| 页面无数据 | 静态目录未挂载 | 确认 `frontend/` 存在且与 `backend/` 同级 |
