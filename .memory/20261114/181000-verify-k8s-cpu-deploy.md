# 实测：K8s（CPU）部署可用性

- 时间：2026-11-14 18:10 (+08:00)
- 类型：verify
- 环境：`li-ocr` namespace，k8s-node2，NodePort 30080

## 部署方式

源码 ConfigMap + initContainer 现场 `pip install --target /deps` + NFS PVC，
不依赖镜像仓库（本机无 Docker CLI，集群内无 registry）。

## 结果

| 检查项 | 结果 |
|---|---|
| Pod | `1/1 Running`，无重启 |
| 引擎 | `paddle.ready=false`，`rapid.primary=true`（自动降级） |
| 上传 → 结果 | 发票 1 页，6~9 s 完成，`AUTO_PASSED` |
| 字段 | 10 个关键字段全对（金额 9,605.00 / 税额 1,105.00 / 不含税 8,500.00） |
| 统计接口 | `/api/stats` 正常返回 |

访问地址：`http://10.10.10.211:30080/`

## 结论

- 服务在 CPU 上可跑，但只有单引擎，交叉验证能力失效；
- 单页约 6~9 s（含 RapidOCR CPU 推理），比本机 GPU 慢 3~5 倍；
- 仅作"服务可部署"的证明，业务验收仍在本机 GPU 完成。

## 待办

1. 该部署当前**未设置访问口令**，任何能访问 30080 的人都可以读写单据；
   共享环境必须 `deploy.ps1 -Token <强随机>`。
2. 集群没有 GPU 设备插件，想要 GPU 加速需先装 nvidia-device-plugin + 对应 runtime。
3. 源码走 ConfigMap 只是 PoC，生产要换成镜像仓库。
