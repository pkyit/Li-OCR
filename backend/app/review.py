"""复核与校正落库 + 词表学习。

原则：
- 校正 append-only，历史值永不丢失；
- 人工确认只是"高置信标签"，不等于真值（数据集里保留来源与置信度）；
- 只把人工确认过的值写进词表，避免把模型自身偏差学进来。
"""
from __future__ import annotations

from . import db
from .pipeline.extract import _normalize, _RULE_BY_KEY

_LEXICON_KEYS = {
    "seller": "seller",
    "buyer": "buyer",
    "bank_name": "bank",
    "currency": "currency",
}


def _norm_for(key: str, value: str) -> str | None:
    rule = _RULE_BY_KEY.get(key)
    kind = rule.kind if rule else "text"
    _, norm = _normalize(kind, value)
    return norm


def submit_review(doc_id: str, reviewer: str, action: str, note: str,
                  changes: list[dict]) -> dict:
    now = db.now_iso()
    applied = 0
    with db.tx() as conn:
        for ch in changes:
            field_id = int(ch.get("field_id") or 0)
            value = (ch.get("value") or "").strip()
            reason = (ch.get("reason") or "OTHER").strip() or "OTHER"
            row = conn.execute("SELECT * FROM field WHERE id=? AND doc_id=?", (field_id, doc_id)).fetchone()
            if not row:
                continue
            before = row["value_raw"] or ""
            norm = _norm_for(row["key"], value) if value else None
            if value == before and norm == row["value_norm"]:
                conn.execute("UPDATE field SET status='CONFIRMED', updated_at=? WHERE id=?", (now, field_id))
                continue
            conn.execute(
                "UPDATE field SET value_raw=?, value_norm=?, status='CONFIRMED', updated_at=? WHERE id=?",
                (value or None, norm, now, field_id))
            conn.execute(
                "INSERT INTO correction (doc_id, field_id, page_no, key, label, value_before, value_after, "
                "reason, reviewer, model_version, bbox, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, field_id, row["page_no"], row["key"], row["label"], before, value, reason,
                 reviewer, "r1.0", row["bbox"], now))
            applied += 1
            kind = _LEXICON_KEYS.get(row["key"])
            if kind and value:
                term = norm or value
                if 2 <= len(term) <= 60:
                    conn.execute(
                        "INSERT INTO lexicon (term, kind, weight, source, updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(term, kind) DO UPDATE SET weight = weight + 0.5, "
                        "source='correction', updated_at=excluded.updated_at",
                        (term, kind, 1.0, "correction", now))

        if action == "SAVE":
            conn.execute("UPDATE document SET updated_at=? WHERE id=?", (now, doc_id))
        else:
            if action == "CONFIRMED":
                conn.execute("UPDATE field SET status='CONFIRMED', updated_at=? WHERE doc_id=? AND status!='CONFIRMED'",
                             (now, doc_id))
            conn.execute(
                "INSERT INTO review (doc_id, reviewer, action, note, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET reviewer=excluded.reviewer, action=excluded.action, "
                "note=excluded.note, created_at=excluded.created_at",
                (doc_id, reviewer, action, note, now))
            status = "REVIEWED" if action == "CONFIRMED" else "REJECTED"
            conn.execute("UPDATE document SET status=?, stage=?, updated_at=? WHERE id=?",
                         (status, "已复核" if action == "CONFIRMED" else "已驳回", now, doc_id))
    return {"doc_id": doc_id, "applied": applied, "action": action}


def doc_corrections(doc_id: str) -> list[dict]:
    rows = db.query_all("SELECT * FROM correction WHERE doc_id=? ORDER BY id", (doc_id,))
    return [dict(r) for r in rows]
