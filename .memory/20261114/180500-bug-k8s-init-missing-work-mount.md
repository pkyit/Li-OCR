# bug：K8s 里 initContainer 装了依赖，主容器却找不到代码

- 时间：2026-11-14 18:05 (+08:00)
- 类型：bug
- 影响范围：`deploy/k8s/20-deployment.yaml`
- 关联提交：R1.0 初始实现内修复

## 现象

Pod 反复 `CrashLoopBackOff`，app 容器报：

```
ModuleNotFoundError: No module named 'backend'
```

但 initContainer 日志显示 `pip install -r /work/deploy/requirements-serve.txt` 成功。

## 根因

initContainer 的 `volumeMounts` 漏了 `work` 这个 emptyDir：

```yaml
# 错误版本
volumeMounts:
  - {name: src, mountPath: /src, readOnly: true}
  - {name: deps, mountPath: /deps}
```

于是 `mkdir -p /work && tar -xzf /src/code.tgz -C /work` 全都写进了 init 容器自己的可写层，
`pip` 也确实从那读到了 `requirements-serve.txt`（所以没有任何报错），
但层在 init 结束时被丢弃，主容器挂载的 `/work` 仍是空目录。

验证方式（关键证据）：

```
$ kubectl -n li-ocr logs deploy/li-ocr -c app
PYTHONPATH=/work:/deps
ls: cannot access '/work/backend': No such file or directory
$ kubectl get pod -o jsonpath='{...initContainers[0].volumeMounts}'
[{"mountPath":"/src","name":"src"},{"mountPath":"/deps","name":"deps"}]
```

## 修复

给 initContainer 补 `- {name: work, mountPath: /work}`。
同时把"改代码"的生效路径写清楚：源码在 ConfigMap 里，Pod 模板不变，必须 `rollout restart`。
`deploy.ps1` / `deploy.sh` 已加入 `kubectl rollout restart deploy/li-ocr`。

## 回归验证

- `kubectl rollout status` 成功；
- 通过 NodePort 上传发票：6~9 s 返回 `AUTO_PASSED`，13 个字段全部正确；
- `/api/config` 显示 `paddle.ready=false`、`rapid.primary=true`（CPU 降级生效）。

## 教训

容器间共享前提是**同一个 volume 挂到两个容器**，不是"路径相同"。
init 阶段"看起来成功"的操作（pip 能读到文件）不能证明共享卷生效——
必须从**消费方容器**里验证产物存在。
