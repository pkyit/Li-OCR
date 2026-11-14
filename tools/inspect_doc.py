"""排查工具：按单据打印字段、可疑信号与耗时。

用法：
  python -m tools.inspect_doc               # 最近一份
  python -m tools.inspect_doc <doc_id>
  python -m tools.inspect_doc --all         # 所有单据的字段
"""
from __future__ import annotations

import argparse
import sys

from backend.app import db


def show_doc(doc_id: str) -> None:
    doc = db.query_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not doc:
        print(f"单据不存在: {doc_id}")
        return
    print(f"\n=== {doc['filename']} ({doc['id']}) ===")
    print(f"状态={doc['status']} 类型={doc['doc_type']} 页数={doc['page_count']} "
          f"错误={doc['error'] or '-'}")
    for p in db.query_all("SELECT * FROM page WHERE doc_id=? ORDER BY page_no", (doc_id,)):
        print(f"  第{p['page_no']}页 {p['page_kind']} {p['engine']} {p['elapsed_ms']}ms "
              f"文本层字符={p['text_layer_chars']}")
    run = db.query_one("SELECT * FROM run WHERE doc_id=? ORDER BY started_at DESC", (doc_id,))
    if run:
        print(f"  版本={run['versions']}\n  耗时={run['timings']}\n  告警={run['warnings']}")
    rows = db.query_all("SELECT * FROM field WHERE doc_id=? ORDER BY id", (doc_id,))
    print(f"  字段 {len(rows)} 个")
    print(f"  {'key':<13}{'value':<32}{'norm':<22}{'conf':>6}  {'status':<8}flags")
    for r in rows:
        flags = db.loads(r["flags"], [])
        cand = db.loads(r["candidate"], {})
        extra = ""
        if cand and cand.get("cross"):
            extra = f"  <rapid:{cand['cross']['value']}>"
        print(f"  {r['key']:<13}{str(r['value_raw'])[:30]:<32}{str(r['value_norm'])[:20]:<22}"
              f"{(r['confidence'] or 0):>6.3f}  {r['status']:<8}{flags}{extra}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id", nargs="?")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    db.init_db()
    if args.all:
        for r in db.query_all("SELECT id FROM document ORDER BY created_at DESC"):
            show_doc(r["id"])
    elif args.doc_id:
        show_doc(args.doc_id)
    else:
        row = db.query_one("SELECT id FROM document ORDER BY created_at DESC LIMIT 1")
        if row:
            show_doc(row["id"])
        else:
            print("库里还没有单据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
