# Li-OCR R1.0 deploy to Kubernetes (PoC path, no image registry required)
# Usage:
#   powershell -ExecutionPolicy Bypass -File deploy\k8s\deploy.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\k8s\deploy.ps1 -Token "<strong-token>"
param(
  [string]$Token = "",
  [string]$Namespace = "li-ocr"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = (Resolve-Path (Join-Path $here "..\..")).Path
$tgz = Join-Path $env:TEMP "liocr-code.tgz"
$cm = Join-Path $env:TEMP "liocr-cm.yaml"

Write-Host "==> pack source (backend / frontend / requirements)"
if (Test-Path $tgz) { Remove-Item $tgz -Force }
tar -czf $tgz -C $root backend frontend deploy/requirements-serve.txt
Write-Host ("    archive: {0:N0} KB" -f ((Get-Item $tgz).Length / 1KB))

Write-Host "==> namespace + source configmap"
kubectl apply -f (Join-Path $here "00-namespace.yaml")
kubectl -n $Namespace create configmap liocr-src --from-file=code.tgz=$tgz `
  --dry-run=client -o yaml | Set-Content -Path $cm -Encoding ascii
kubectl apply -f $cm

if ($Token -ne "") {
  Write-Host "==> write api token secret (never stored in repo)"
  kubectl -n $Namespace create secret generic liocr-secret `
    --from-literal=api-token=$Token --dry-run=client -o yaml | Set-Content -Path "$cm.secret" -Encoding ascii
  kubectl apply -f "$cm.secret"
  Remove-Item "$cm.secret" -Force
}

Write-Host "==> apply pvc / deployment / service"
kubectl apply -f (Join-Path $here "10-pvc.yaml")
kubectl apply -f (Join-Path $here "20-deployment.yaml")
kubectl apply -f (Join-Path $here "30-service.yaml")

# 源码在 ConfigMap 里，Pod 模板不变也会更新文件；必须显式重启才能加载新代码
Write-Host "==> restart to pick up source changes"
kubectl -n $Namespace rollout restart deploy/li-ocr

Write-Host "==> wait for rollout (first run installs deps, 1-3 min)"
kubectl -n $Namespace rollout status deploy/li-ocr --timeout=900s
kubectl -n $Namespace get pod,svc -o wide

$ip = kubectl get nodes -o jsonpath="{.items[1].status.addresses[0].address}"
Write-Host ""
Write-Host "URL: http://${ip}:30080/"
if ($Token -eq "") {
  Write-Host "WARNING: no api token set; every /api/* is open. Set -Token for any shared environment."
}
