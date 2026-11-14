# 决策：K8s 侧只跑 CPU 版，并支持引擎自动降级

- 时间：2026-11-14 17:50 (+08:00)
- 类型：decision
- 影响范围：`deploy/`、`backend/app/pipeline/runner.py`、`engines.py`
- 关联提交：R1.0 初始实现

## 事实

- 集群 4 节点（1 master + 3 worker）全部 Ready，kubectl 可直连；
- `kubectl get nodes -o jsonpath={.status.allocatable.nvidia\.com/gpu}` 为空——**没有 GPU 设备插件**；
- 默认 StorageClass `nfs-client` 可用，PVC 能正常绑定；
- 节点能拉取 Docker Hub 公共镜像（busybox 实测 Running）；
- 本机没有 Docker CLI，集群内也没有镜像仓库。

## 结论

1. **K8s 部署只能跑 CPU 版**，GPU 仍由本机承担（可行性验证的算力就在本地）。
2. 流水线增加**引擎自动降级**：`paddleocr` 不可导入时，主引擎切到 RapidOCR，
   `versions.ocr_model` 记为 `rapidocr-cpu`，并在 `run.warnings` 落一条说明。
   理由：容器里装 Paddle 会让镜像膨胀数百 MB 且 CPU 推理并不占优，没必要。
3. 无镜像仓库时用 PoC 路径：源码打包进 ConfigMap（约 100KB，远低于 1MB 上限），
   initContainer 用 `python:3.11-slim` 现场 `pip install --target /deps`，数据落 NFS PVC。
   生产再换成 `deploy/Dockerfile` 构建的镜像。

## 否决项

- 在节点上手工 `ctr import` 镜像：不可复现、无版本记录，比 ConfigMap 更脏。
- 为 PoC 搭一套 Harbor：属于基础设施交付，不是 R1.0 目标。

## 风险

- ConfigMap 装载源码意味着**改代码必须重新 apply**，且不保留历史版本；仅限 PoC。
- initContainer 依赖节点出网（已实测可用），否则起不来。
- CPU 单引擎下没有交叉验证，可疑信号只剩规则/校验/置信度。

## 后续

有仓库后：`docker build -f deploy/Dockerfile` → push → 替换 Deployment 的 image，
并把 `LIOCR_CROSSCHECK` 与 `LIOCR_DEVICE` 按实际环境调整。
