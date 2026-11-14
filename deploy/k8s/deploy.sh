#!/usr/bin/env bash
# Li-OCR R1.0 部署到 K8s（无镜像仓库的 PoC 路径）
# 用法：TOKEN=可选口令 bash deploy/k8s/deploy.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
NS="${NAMESPACE:-li-ocr}"
TGZ="${TMPDIR:-/tmp}/liocr-code.tgz"

echo "==> 打包源码"
tar -czf "$TGZ" -C "$ROOT" backend frontend deploy/requirements-serve.txt
ls -lh "$TGZ"

echo "==> 命名空间与 ConfigMap"
kubectl apply -f "$HERE/00-namespace.yaml"
kubectl -n "$NS" create configmap liocr-src --from-file=code.tgz="$TGZ" \
  --dry-run=client -o yaml | kubectl apply -f -

if [[ -n "${TOKEN:-}" ]]; then
  echo "==> 写入访问口令 Secret"
  kubectl -n "$NS" create secret generic liocr-secret \
    --from-literal=api-token="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
fi

echo "==> 应用工作负载"
kubectl apply -f "$HERE/10-pvc.yaml"
kubectl apply -f "$HERE/20-deployment.yaml"
kubectl apply -f "$HERE/30-service.yaml"
# 源码在 ConfigMap 里，Pod 模板不变也会更新文件；必须显式重启才能加载新代码
kubectl -n "$NS" rollout restart deploy/li-ocr

kubectl -n "$NS" rollout status deploy/li-ocr --timeout=900s
kubectl -n "$NS" get pod,svc -o wide
