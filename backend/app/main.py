"""Li-OCR R1.0 HTTP 服务。

边界（沿用架构设计）：
- 上传同步应答、处理全异步；
- 前端只访问本服务，不直连推理/数据库；
- 口令从环境变量读取，绝不写进代码或仓库。
"""
from __future__ import annotations

import logging
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import datasets, db, review, stats
from .config import settings
from .pipeline import runner
from .pipeline.engines import engines_summary
from .pipeline.validate import REASON_TEXT
from .storage import StorageError, abs_of, rel, save_upload, sha256_of

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("liocr.api")

MAX_FILES_PER_REQUEST = 50
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    db.init_db()
    runner.start_workers()
    runner.warmup_async()
    log.info("Li-OCR R1.0 started: device=%s model=%s data=%s",
             settings.device, settings.ocr_model, settings.data_dir)
    yield


app = FastAPI(title="Li-OCR R1.0", version="1.0.0", lifespan=lifespan)

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["*"], allow_headers=["*"])


def auth(x_api_token: str | None = Header(default=None, alias="X-Api-Token"),
         token: str | None = Query(default=None)) -> None:
    """可选口令鉴权：设置 LIOCR_API_TOKEN 后生效。"""
    if not settings.api_token:
        return
    supplied = x_api_token or token or ""
    if not secrets.compare_digest(supplied, settings.api_token):
        raise HTTPException(status_code=401, detail="口令无效")


def _doc_dict(row, extra: dict | None = None) -> dict:
    d = {
        "id": row["id"], "filename": row["filename"], "doc_type": row["doc_type"],
        "status": row["status"], "stage": row["stage"], "progress": row["progress"],
        "page_count": row["page_count"], "size": row["size"], "error": row["error"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "run_id": row["run_id"], "ext": row["ext"],
    }
    if extra:
        d.update(extra)
    return d


def _field_dict(row) -> dict:
    return {
        "id": row["id"], "page_no": row["page_no"], "group": row["group_name"],
        "key": row["key"], "label": row["label"], "value_raw": row["value_raw"],
        "value_norm": row["value_norm"], "confidence": row["confidence"],
        "bbox": db.loads(row["bbox"], None), "source": row["source"], "status": row["status"],
        "flags": db.loads(row["flags"], []), "required": bool(row["required"]),
        "candidate": db.loads(row["candidate"], None),
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "queue": runner.queue_size(), "version": "1.0.0"}


@app.get("/api/config", dependencies=[Depends(auth)])
def api_config() -> dict:
    return {
        "engines": engines_summary(settings.ocr_model, settings.device, settings.crosscheck,
                                   settings.vl_fallback),
        "config": {"device": settings.device, "model": settings.ocr_model,
                   "render_dpi": settings.render_dpi, "workers": settings.workers},
        "auth_required": bool(settings.api_token),
        "max_files": MAX_FILES_PER_REQUEST,
    }


@app.post("/api/documents", dependencies=[Depends(auth)])
def upload_documents(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"单次最多 {MAX_FILES_PER_REQUEST} 份")
    tmp_dir = settings.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for uf in files:
        name = Path(uf.filename or "upload.bin").name
        tmp = tmp_dir / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
        try:
            with tmp.open("wb") as fh:
                shutil.copyfileobj(uf.file, fh)
            sha = sha256_of(tmp)
            existing = db.query_one("SELECT * FROM document WHERE sha256=?", (sha,))
            if existing:
                tmp.unlink(missing_ok=True)
                results.append({"doc_id": existing["id"], "filename": name,
                                "duplicate": True, "status": existing["status"]})
                continue
            dest = save_upload(tmp, name, sha)
            doc_id = uuid.uuid4().hex[:16]
            now = db.now_iso()
            db.execute(
                "INSERT INTO document (id, sha256, filename, ext, size, raw_path, status, stage, "
                "progress, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, sha, name, dest.suffix.lower(), dest.stat().st_size, rel(dest),
                 "RECEIVED", "待处理", 0.02, now, now))
            runner.enqueue(doc_id)
            results.append({"doc_id": doc_id, "filename": name, "duplicate": False, "status": "QUEUED"})
        except StorageError as exc:
            tmp.unlink(missing_ok=True)
            results.append({"filename": name, "error": str(exc)})
        except Exception as exc:  # 单个文件失败不影响整批
            tmp.unlink(missing_ok=True)
            log.exception("upload failed %s", name)
            results.append({"filename": name, "error": str(exc)})
    return {"count": len(results), "items": results}


@app.get("/api/documents", dependencies=[Depends(auth)])
def list_documents(status: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    sql = ("SELECT d.*, "
           "(SELECT COUNT(*) FROM field f WHERE f.doc_id=d.id AND f.status IN ('SUSPECT','MISSING')) suspect_fields, "
           "(SELECT COUNT(*) FROM field f WHERE f.doc_id=d.id) total_fields "
           "FROM document d ")
    params: list = []
    if status:
        sql += "WHERE d.status = ? "
        params.append(status)
    sql += "ORDER BY d.created_at DESC LIMIT ? OFFSET ?"
    params += [max(1, min(limit, 500)), max(0, offset)]
    rows = db.query_all(sql, params)
    items = [_doc_dict(r, {"suspect_fields": r["suspect_fields"], "total_fields": r["total_fields"]})
             for r in rows]
    counts = {r["status"]: r["c"] for r in db.query_all(
        "SELECT status, COUNT(*) c FROM document GROUP BY status")}
    return {"items": items, "counts": counts, "queue": runner.queue_size()}


@app.get("/api/documents/{doc_id}", dependencies=[Depends(auth)])
def get_document(doc_id: str) -> dict:
    row = db.query_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise HTTPException(status_code=404, detail="单据不存在")
    pages = [dict(r) for r in db.query_all(
        "SELECT page_no, image_path, width, height, page_kind, text_layer_chars, engine, elapsed_ms "
        "FROM page WHERE doc_id=? ORDER BY page_no", (doc_id,))]
    fields = [_field_dict(r) for r in db.query_all("SELECT * FROM field WHERE doc_id=? ORDER BY id", (doc_id,))]
    runs = [dict(r) for r in db.query_all("SELECT * FROM run WHERE doc_id=? ORDER BY started_at DESC", (doc_id,))]
    rv = db.query_one("SELECT * FROM review WHERE doc_id=?", (doc_id,))
    corr = review.doc_corrections(doc_id)
    next_row = db.query_one("SELECT id FROM document WHERE status='NEED_REVIEW' AND id != ? "
                            "ORDER BY created_at LIMIT 1", (doc_id,))
    return {
        "doc": _doc_dict(row),
        "pages": pages, "fields": fields, "runs": runs,
        "review": dict(rv) if rv else None,
        "corrections": corr,
        "next_review_id": next_row["id"] if next_row else None,
        "reason_text": REASON_TEXT,
    }


@app.post("/api/documents/{doc_id}/reprocess", dependencies=[Depends(auth)])
def reprocess(doc_id: str) -> dict:
    row = db.query_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise HTTPException(status_code=404, detail="单据不存在")
    runner.enqueue(doc_id)
    db.execute("UPDATE document SET status='QUEUED', stage='重新排队', progress=0.02, updated_at=? WHERE id=?",
               (db.now_iso(), doc_id))
    return {"doc_id": doc_id, "status": "QUEUED"}


@app.delete("/api/documents/{doc_id}", dependencies=[Depends(auth)])
def delete_document(doc_id: str) -> dict:
    row = db.query_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise HTTPException(status_code=404, detail="单据不存在")
    db.execute("DELETE FROM document WHERE id=?", (doc_id,))
    for sub in (settings.pages_dir / doc_id, settings.ocr_dir / doc_id):
        shutil.rmtree(sub, ignore_errors=True)
    # 原始件保留（不可变），只删索引；如确需删除再单独处理
    return {"deleted": doc_id}


@app.get("/api/documents/{doc_id}/pages/{page_no}/image", dependencies=[Depends(auth)])
def page_image(doc_id: str, page_no: int):
    row = db.query_one("SELECT image_path FROM page WHERE doc_id=? AND page_no=?", (doc_id, page_no))
    if not row:
        raise HTTPException(status_code=404, detail="页面不存在")
    path = abs_of(row["image_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="页面图缺失")
    return FileResponse(path, media_type="image/png")


@app.post("/api/documents/{doc_id}/review", dependencies=[Depends(auth)])
def submit_review(doc_id: str, payload: dict) -> dict:
    row = db.query_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise HTTPException(status_code=404, detail="单据不存在")
    action = (payload.get("action") or "CONFIRMED").upper()
    if action not in ("CONFIRMED", "REJECTED", "SAVE"):
        raise HTTPException(status_code=400, detail="非法复核动作")
    reviewer = (payload.get("reviewer") or "anonymous").strip()[:64]
    note = (payload.get("note") or "").strip()[:500]
    changes = payload.get("changes") or []
    if not isinstance(changes, list):
        raise HTTPException(status_code=400, detail="changes 必须是数组")
    return review.submit_review(doc_id, reviewer, action, note, changes)


@app.get("/api/issues", dependencies=[Depends(auth)])
def issues(limit: int = 200) -> dict:
    rows = db.query_all(
        "SELECT d.id doc_id, d.filename, d.doc_type, d.status doc_status, f.id field_id, f.key, f.label, "
        "f.value_raw, f.confidence, f.page_no, f.flags FROM field f JOIN document d ON d.id=f.doc_id "
        "WHERE f.status IN ('SUSPECT','MISSING') ORDER BY f.id DESC LIMIT ?", (max(1, min(limit, 1000)),))
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        flags = db.loads(r["flags"], []) or ["MISSING"]
        for flag in flags:
            gk = (r["key"], flag)
            g = groups.setdefault(gk, {"key": r["key"], "label": r["label"], "reason": flag,
                                       "reason_text": REASON_TEXT.get(flag, flag), "count": 0,
                                       "docs": []})
            g["count"] += 1
            if len(g["docs"]) < 20:
                g["docs"].append({"doc_id": r["doc_id"], "filename": r["filename"],
                                  "doc_type": r["doc_type"], "value": r["value_raw"],
                                  "confidence": r["confidence"], "page_no": r["page_no"],
                                  "field_id": r["field_id"]})
    items = sorted(groups.values(), key=lambda x: -x["count"])
    return {"items": items, "total": sum(g["count"] for g in items)}


@app.get("/api/stats", dependencies=[Depends(auth)])
def api_stats() -> dict:
    return stats.overview()


@app.post("/api/datasets/export", dependencies=[Depends(auth)])
def export_dataset(payload: dict | None = None) -> dict:
    note = (payload or {}).get("note", "")
    return datasets.export_dataset(note=note)


@app.get("/api/datasets", dependencies=[Depends(auth)])
def api_datasets() -> dict:
    return {"items": datasets.list_datasets()}


@app.delete("/api/datasets/{version}", dependencies=[Depends(auth)])
def api_delete_dataset(version: str) -> dict:
    if not datasets.delete_dataset(version):
        raise HTTPException(status_code=404, detail="数据集不存在")
    return {"deleted": version}


@app.get("/api/lexicon", dependencies=[Depends(auth)])
def api_lexicon() -> dict:
    rows = db.query_all("SELECT * FROM lexicon ORDER BY weight DESC, updated_at DESC LIMIT 200")
    return {"items": [dict(r) for r in rows]}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # 前端缺失时给出明确提示，而不是 404 谜题
    @app.get("/")
    def missing_frontend() -> dict:
        return {"error": "frontend 目录不存在", "path": str(FRONTEND_DIR)}
