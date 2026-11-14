"""本地批量导入工具：绕过 HTTP 直接跑流水线，用于可行性验证与规则调优。

用法（在仓库根目录）：
  python -m tools.ingest_local ocr_samples\*.pdf --sync
  python -m tools.ingest_local D:\some\dir --sync --limit 20
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from backend.app import db
from backend.app.config import settings
from backend.app.storage import ALLOWED_EXT, save_upload, sha256_of, rel
from backend.app.pipeline import runner
from backend.app.pipeline.validate import REASON_TEXT


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in ALLOWED_EXT)
        elif p.exists():
            files.append(p)
        else:
            for f in sorted(Path().glob(raw)):
                files.append(f)
    return files


def ingest(path: Path) -> str | None:
    settings.ensure_dirs()
    sha = sha256_of(path)
    existing = db.query_one("SELECT id FROM document WHERE sha256=?", (sha,))
    if existing:
        print(f"  [跳过] 已存在 {path.name} -> {existing['id']}")
        return existing["id"]
    dest = save_upload(path, path.name, sha, move=False)
    doc_id = uuid.uuid4().hex[:16]
    now = db.now_iso()
    db.execute(
        "INSERT INTO document (id, sha256, filename, ext, size, raw_path, status, stage, progress, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, sha, path.name, dest.suffix.lower(), dest.stat().st_size, rel(dest),
         "RECEIVED", "待处理", 0.02, now, now))
    return doc_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--sync", action="store_true", help="同步处理（便于观察日志）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = collect(args.paths)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("没有找到可导入文件")
        return 1

    db.init_db()
    print(f"待导入 {len(files)} 份，device={settings.device} model={settings.ocr_model} "
          f"crosscheck={settings.crosscheck}")
    ids = []
    t0 = time.time()
    for f in files:
        print(f"- {f.name}")
        doc_id = ingest(f)
        if doc_id:
            ids.append(doc_id)

    if args.sync:
        for i, doc_id in enumerate(ids, 1):
            t1 = time.time()
            runner.process_document(doc_id)
            row = db.query_one("SELECT * FROM document WHERE id=?", (doc_id,))
            print(f"[{i}/{len(ids)}] {row['filename']} -> {row['status']} "
                  f"({(time.time() - t1):.1f}s) {row['error'] or ''}")
    else:
        runner.start_workers()
        while runner.queue_size() > 0:
            time.sleep(1)
        time.sleep(2)

    total = time.time() - t0
    print(f"\n完成 {len(ids)} 份，总耗时 {total:.1f}s")
    rows = db.query_all(
        "SELECT d.id, d.filename, d.status, d.doc_type, d.page_count, "
        "(SELECT COUNT(*) FROM field f WHERE f.doc_id=d.id) fields, "
        "(SELECT COUNT(*) FROM field f WHERE f.doc_id=d.id AND f.status!='OK') suspects "
        "FROM document d ORDER BY d.updated_at DESC LIMIT ?", (len(ids),))
    for r in rows:
        print(f"  {r['status']:<12} {r['doc_type'] or '-':<8} 页{r['page_count']} "
              f"字段{r['fields']} 可疑{r['suspects']}  {r['filename']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
