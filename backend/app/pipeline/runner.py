"""处理流水线编排 + 进程内任务队列。

交互风格：上传同步应答，处理全异步（GPU 单并发），状态由前端轮询/推送展示。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path

from .. import db
from ..config import settings
from ..storage import abs_of, ocr_dir, rel
from .base import Line
from .engines import PaddleEngine, RapidEngine, VLEngine
from .extract import detect_doc_type, extract_fields
from .io import load_pages, triage_kind
from .validate import doc_needs_review, field_status, validate_fields

log = logging.getLogger("liocr.pipeline")

_QUEUE: "queue.Queue[str]" = queue.Queue()
_WORKERS: list[threading.Thread] = []
_STARTED = False
_STATE_LOCK = threading.Lock()
_CURRENT: dict[str, float] = {}


def _set_state(doc_id: str, status: str | None = None, stage: str | None = None,
               progress: float | None = None, error: str | None = None) -> None:
    sets, params = ["updated_at = ?"], [db.now_iso()]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if stage is not None:
        sets.append("stage = ?")
        params.append(stage)
    if progress is not None:
        sets.append("progress = ?")
        params.append(progress)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    params.append(doc_id)
    db.execute(f"UPDATE document SET {', '.join(sets)} WHERE id = ?", params)
    with _STATE_LOCK:
        if progress is not None:
            _CURRENT[doc_id] = progress


def enqueue(doc_id: str) -> None:
    _QUEUE.put(doc_id)
    db.execute("UPDATE document SET status='QUEUED', stage='排队中', progress=0.02, updated_at=? WHERE id=? AND status IN ('RECEIVED','QUEUED','FAILED')",
               (db.now_iso(), doc_id))


def queue_size() -> int:
    return _QUEUE.qsize()


def _save_ocr_json(doc_id: str, payload: dict) -> str:
    path = ocr_dir(doc_id) / "lines.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return rel(path)


def _page_quality(lines: list[Line]) -> dict:
    """页面识别质量：字符量 + 平均置信度 + 低置信占比。

    手写页面在行识别上会"大面积低分甚至整行丢失"，这三个指标会同时恶化，
    因此用它来决定是否启用 VL 兜底，而不是靠单据类型猜。
    """
    scores = [l.score for l in lines if l.score > 0]
    chars = sum(len(l.text) for l in lines)
    mean = sum(scores) / len(scores) if scores else 0.0
    low_ratio = sum(1 for s in scores if s < 0.6) / len(scores) if scores else 1.0
    low = chars < 40 or (mean > 0 and mean < 0.80) or low_ratio > 0.35
    return {"chars": chars, "mean_conf": round(mean, 3), "low_ratio": round(low_ratio, 3),
            "low": low}


def process_document(doc_id: str) -> None:
    row = db.query_one("SELECT * FROM document WHERE id = ?", (doc_id,))
    if not row:
        return
    started = time.time()
    run_id = uuid.uuid4().hex[:12]
    timings: dict[str, int] = {}
    warnings: list[str] = []
    versions = {
        "pipeline": "r1.0",
        "ocr_model": settings.ocr_model,
        "device": settings.device,
        "rules": "rules-2026.11.14",
        "render_dpi": settings.render_dpi,
    }

    try:
        _set_state(doc_id, status="PROCESSING", stage="渲染页面", progress=0.05, error="")
        t0 = time.time()
        pages = load_pages(abs_of(row["raw_path"]), doc_id, settings.render_dpi)
        timings["render_ms"] = int((time.time() - t0) * 1000)
        if not pages:
            raise RuntimeError("文件没有可解析页面")

        paddle_ok = PaddleEngine.available()
        paddle = PaddleEngine.get(settings.ocr_model, settings.device) if paddle_ok else None
        rapid = RapidEngine.get()
        crosscheck = bool(paddle_ok and rapid.available
                          and settings.crosscheck not in ("off", "0", "false", "no"))
        vl = VLEngine.get()
        vl_enabled = bool(vl.available and settings.device.startswith("gpu")
                          and settings.vl_fallback not in ("off", "0", "false", "no"))
        if not paddle_ok:
            versions["ocr_model"] = "rapidocr-cpu"
            warnings.append("PaddleOCR 不可用，已降级为 CPU 单引擎")

        lines_by_page: dict[int, list[Line]] = {}
        cross_by_page: dict[int, list[Line]] = {}
        page_rows: list[dict] = []
        ocr_payload: dict[str, dict] = {}
        ocr_ms_total = 0
        cross_ms_total = 0
        vl_ms_total = 0
        low_quality_pages: list[int] = []

        for idx, page in enumerate(pages):
            _set_state(doc_id, stage=f"识别第 {page.page_no}/{len(pages)} 页",
                       progress=0.05 + 0.75 * (idx / len(pages)))
            kind = triage_kind(page)
            engine_name, elapsed = "textlayer", 0
            if kind == "TEXT_PDF" and page.text_lines:
                lines = page.text_lines
            else:
                res = paddle.recognize(page.image_path) if paddle else rapid.recognize(page.image_path)
                if res.error:
                    raise RuntimeError(f"OCR 引擎错误: {res.error}")
                lines, elapsed = res.lines, res.elapsed_ms
                engine_name = res.engine
                ocr_ms_total += elapsed

            quality = _page_quality(lines)
            primary_engine = engine_name
            # 手写/模糊页：行识别会整段丢掉，用 VL 补一份文本证据（不参与数值裁定）
            if kind != "TEXT_PDF" and quality["low"]:
                low_quality_pages.append(page.page_no)
                if vl_enabled:
                    _set_state(doc_id, stage=f"第 {page.page_no} 页质量低，启用 VL 兜底",
                               progress=0.05 + 0.75 * (idx / len(pages)))
                    vres = vl.recognize(page.image_path, settings.device)
                    vl_ms_total += vres.elapsed_ms
                    if vres.error:
                        warnings.append(f"第{page.page_no}页 VL 兜底失败: {vres.error}")
                    elif vres.lines:
                        lines = lines + vres.lines
                        engine_name = f"{engine_name}+vl"
                        kind = "HANDWRITE_LOW_QUALITY"
                        versions["vl"] = "PaddleOCR-VL"
                else:
                    kind = "LOW_QUALITY"
                    warnings.append(f"第{page.page_no}页识别质量低且未启用 VL，字段可能缺失")

            cross_lines: list[Line] = []
            if crosscheck and primary_engine != "textlayer":
                tc = time.time()
                cres = rapid.recognize(page.image_path)
                cross_ms_total += int((time.time() - tc) * 1000)
                if cres.error:
                    warnings.append(f"第{page.page_no}页 CPU 旁路失败: {cres.error}")
                else:
                    cross_lines = cres.lines

            lines_by_page[page.page_no] = lines
            cross_by_page[page.page_no] = cross_lines
            page_rows.append({
                "page_no": page.page_no, "image_path": rel(page.image_path),
                "width": page.width, "height": page.height, "page_kind": kind,
                "text_layer_chars": page.text_chars, "engine": engine_name,
                "elapsed_ms": elapsed,
            })
            ocr_payload[f"p{page.page_no}"] = {"kind": kind, "engine": engine_name,
                                               "quality": quality,
                                               "lines": [l.to_dict() for l in lines],
                                               "cross_lines": [l.to_dict() for l in cross_lines]}

        _set_state(doc_id, stage="字段抽取与校验", progress=0.86)
        t0 = time.time()
        all_text = [l.text for lines in lines_by_page.values() for l in lines]
        doc_type = detect_doc_type(all_text)
        lexicon = load_lexicon()
        fields = extract_fields(pages, lines_by_page, doc_type, lexicon)

        cross_fields = {}
        if crosscheck:
            cross_result = extract_fields(pages, cross_by_page, doc_type, lexicon)
            cross_fields = {f.key: f for f in cross_result if f.value_norm}
            versions["crosscheck"] = "rapidocr"
        fields = validate_fields(fields, cross_fields if crosscheck else None,
                                 settings.conf_threshold, settings.conf_threshold_amount)
        timings["extract_validate_ms"] = int((time.time() - t0) * 1000)

        ocr_path = _save_ocr_json(doc_id, {
            "doc_id": doc_id, "run_id": run_id, "doc_type": doc_type, "pages": ocr_payload,
        })

        t0 = time.time()
        _persist(doc_id, run_id, doc_type, pages, page_rows, fields, ocr_path)
        timings["persist_ms"] = int((time.time() - t0) * 1000)
        timings["ocr_ms"] = ocr_ms_total
        timings["cross_ms"] = cross_ms_total
        timings["vl_ms"] = vl_ms_total
        # 手写/低质量页即使用 VL 补到字段，也不允许自动通过：证据链不足
        status = ("NEED_REVIEW" if (doc_needs_review(fields) or low_quality_pages)
                  else "AUTO_PASSED")
        if low_quality_pages:
            warnings.append("低质量页: " + ",".join(str(p) for p in low_quality_pages))
        timings["total_ms"] = int((time.time() - started) * 1000)
        db.execute(
            "INSERT INTO run (id, doc_id, versions, status, started_at, finished_at, timings, warnings) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, doc_id, db.dumps(versions), "OK", db.now_iso(), db.now_iso(),
             db.dumps(timings), db.dumps(warnings)))
        db.execute("UPDATE document SET run_id=? WHERE id=?", (run_id, doc_id))
        db.execute("UPDATE document SET first_decision=? WHERE id=? AND first_decision IS NULL",
                   (status, doc_id))
        _set_state(doc_id, status=status, stage="完成" if status == "AUTO_PASSED" else "待复核",
                   progress=1.0)
        log.info("doc %s done: type=%s pages=%d fields=%d status=%s ocr_ms=%d",
                 doc_id, doc_type, len(pages), len(fields), status, ocr_ms_total)

    except Exception as exc:  # 失败不吞：落库 + 可重跑
        log.exception("doc %s failed", doc_id)
        timings["total_ms"] = int((time.time() - started) * 1000)
        db.execute(
            "INSERT INTO run (id, doc_id, versions, status, started_at, finished_at, timings, warnings) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, doc_id, db.dumps(versions), "FAILED", db.now_iso(), db.now_iso(),
             db.dumps(timings), db.dumps(warnings + [str(exc)])))
        _set_state(doc_id, status="FAILED", stage="失败", progress=1.0, error=str(exc)[:800])


def _persist(doc_id: str, run_id: str, doc_type: str, pages, page_rows, fields, ocr_path: str) -> None:
    reviewed = db.query_one("SELECT 1 FROM review WHERE doc_id = ?", (doc_id,))
    with db.tx() as conn:
        conn.execute("DELETE FROM page WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM field WHERE doc_id = ?", (doc_id,))
        for pr in page_rows:
            conn.execute(
                "INSERT INTO page (doc_id, page_no, image_path, width, height, page_kind, "
                "text_layer_chars, engine, elapsed_ms, ocr_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (doc_id, pr["page_no"], pr["image_path"], pr["width"], pr["height"],
                 pr["page_kind"], pr["text_layer_chars"], pr["engine"], pr["elapsed_ms"], ocr_path))
        now = db.now_iso()
        for fv in fields:
            conn.execute(
                "INSERT INTO field (doc_id, page_no, group_name, key, label, value_raw, value_norm, "
                "confidence, bbox, source, status, flags, required, candidate, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, fv.page_no, fv.group, fv.key, fv.label, fv.value_raw, fv.value_norm,
                 float(fv.confidence or 0.0),
                 db.dumps(fv.bbox) if fv.bbox else None, fv.source, field_status(fv),
                 db.dumps(fv.flags), 1 if fv.required else 0,
                 db.dumps(fv.candidate) if fv.candidate else None, now))
        conn.execute("UPDATE document SET page_count=?, doc_type=?, updated_at=? WHERE id=?",
                     (len(pages), doc_type, now, doc_id))
        if reviewed:
            conn.execute("DELETE FROM review WHERE doc_id = ?", (doc_id,))


def load_lexicon() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in db.query_all("SELECT term, kind FROM lexicon WHERE weight >= 0.5"):
        out.setdefault(r["kind"], set()).add(r["term"])
    return out


def _worker_loop(name: str) -> None:
    while True:
        doc_id = _QUEUE.get()
        try:
            process_document(doc_id)
        except Exception:
            log.exception("worker %s crashed on %s", name, doc_id)
        finally:
            VLEngine.get().release()
            _QUEUE.task_done()


def start_workers() -> None:
    global _STARTED
    with _STATE_LOCK:
        if _STARTED:
            return
        _STARTED = True
    for i in range(settings.workers):
        t = threading.Thread(target=_worker_loop, args=(f"w{i}",), daemon=True, name=f"liocr-worker-{i}")
        t.start()
        _WORKERS.append(t)
    # 崩溃恢复：重启后把中断的任务重新排队
    for r in db.query_all("SELECT id FROM document WHERE status IN ('QUEUED','PROCESSING','RECEIVED') "
                          "ORDER BY created_at"):
        _QUEUE.put(r["id"])


def warmup_async() -> None:
    def _run():
        if PaddleEngine.available():
            PaddleEngine.get(settings.ocr_model, settings.device).warmup()
        RapidEngine.get()
    threading.Thread(target=_run, daemon=True, name="liocr-warmup").start()
