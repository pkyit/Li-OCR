"""统计：把"越用越准"变成可观察的数字，而不是口号。

R1.0 只看三个能反映闭环是否转起来的指标：
- 文档自动通过率（无需人工的单据占比）
- 字段可疑率（被提请复核的字段占比）
- 人工纠正率（每百个字段被改写的次数）
"""
from __future__ import annotations

from . import db
from .config import settings
from .pipeline.engines import engines_summary
from .pipeline.validate import REASON_TEXT


def _one(sql: str, params=()) -> int:
    row = db.query_one(sql, params)
    return int(row[0]) if row and row[0] is not None else 0


def overview() -> dict:
    docs_total = _one("SELECT COUNT(*) FROM document")
    by_status = {r["status"]: r["c"] for r in db.query_all(
        "SELECT status, COUNT(*) c FROM document GROUP BY status")}
    fields_total = _one("SELECT COUNT(*) FROM field")
    suspect = _one("SELECT COUNT(*) FROM field WHERE status='SUSPECT'")
    missing = _one("SELECT COUNT(*) FROM field WHERE status='MISSING'")
    confirmed = _one("SELECT COUNT(*) FROM field WHERE status='CONFIRMED'")
    corrections = _one("SELECT COUNT(*) FROM correction")
    corrected_fields = _one("SELECT COUNT(DISTINCT field_id) FROM correction")

    reasons = [{"code": r["code"], "text": REASON_TEXT.get(r["code"], r["code"]), "count": r["c"]}
               for r in db.query_all("SELECT reason AS code, COUNT(*) c FROM correction GROUP BY reason "
                                     "ORDER BY c DESC LIMIT 10")]
    flag_counts: dict[str, int] = {}
    for row in db.query_all("SELECT flags FROM field WHERE flags IS NOT NULL AND flags != '[]'"):
        for f in db.loads(row["flags"], []) or []:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    flags = sorted(({"code": k, "text": REASON_TEXT.get(k, k), "count": v}
                    for k, v in flag_counts.items()), key=lambda x: -x["count"])

    per_key = []
    for r in db.query_all(
            "SELECT key, label, COUNT(*) total, "
            "SUM(CASE WHEN status IN ('SUSPECT','MISSING') THEN 1 ELSE 0 END) suspect, "
            "AVG(confidence) avg_conf FROM field GROUP BY key, label ORDER BY total DESC"):
        fixed = _one("SELECT COUNT(DISTINCT field_id) FROM correction WHERE key=?", (r["key"],))
        per_key.append({
            "key": r["key"], "label": r["label"], "total": r["total"],
            "suspect": r["suspect"] or 0, "corrected": fixed,
            "avg_confidence": round(r["avg_conf"] or 0, 3),
            "correction_rate": round(fixed / r["total"] * 100, 1) if r["total"] else 0.0,
        })

    daily = []
    for r in db.query_all(
            "SELECT substr(created_at,1,10) d, COUNT(*) c FROM document GROUP BY d ORDER BY d DESC LIMIT 14"):
        fixed = _one("SELECT COUNT(*) FROM correction WHERE substr(created_at,1,10)=?", (r["d"],))
        daily.append({"date": r["d"], "docs": r["c"], "corrections": fixed})
    daily.reverse()

    pages = _one("SELECT COUNT(*) FROM page")
    avg_ms = db.query_one("SELECT AVG(elapsed_ms) FROM page WHERE elapsed_ms > 0")
    need_review = by_status.get("NEED_REVIEW", 0)
    auto = by_status.get("AUTO_PASSED", 0)
    reviewed = by_status.get("REVIEWED", 0)
    # 自动通过率按"首次处理结论"统计：人工复核过的单据也计入当时的自动通过
    first_auto = _one("SELECT COUNT(*) FROM document WHERE first_decision='AUTO_PASSED'")

    return {
        "docs": {"total": docs_total, "by_status": by_status, "need_review": need_review,
                 "auto_passed": auto, "reviewed": reviewed, "first_auto_passed": first_auto,
                 "auto_pass_rate": round(first_auto / docs_total * 100, 1) if docs_total else 0.0},
        "fields": {"total": fields_total, "suspect": suspect, "missing": missing,
                   "confirmed": confirmed, "corrected_fields": corrected_fields,
                   "correction_rate": round(corrected_fields / fields_total * 100, 1) if fields_total else 0.0,
                   "suspect_rate": round((suspect + missing) / fields_total * 100, 1) if fields_total else 0.0,
                   "correction_events": corrections},
        "pages": {"total": pages, "avg_ocr_ms": int(avg_ms[0]) if avg_ms and avg_ms[0] else 0},
        "flags": flags, "reasons": reasons, "per_key": per_key, "daily": daily,
        "engines": engines_summary(settings.ocr_model, settings.device, settings.crosscheck),
        "config": {"device": settings.device, "model": settings.ocr_model,
                   "render_dpi": settings.render_dpi, "workers": settings.workers},
    }
