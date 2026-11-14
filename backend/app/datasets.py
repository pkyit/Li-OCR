"""数据集导出：把人工确认过的样本沉淀成可训练格式。

只导出 Gold（人工确认）样本；双引擎一致但无人确认的样本只用于评测候选，
绝不直接当监督标签——否则模型会学习自身偏差。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime

from PIL import Image

from . import db
from .config import settings
from .pipeline.base import iou
from .storage import abs_of, rel


def _observed_text(doc_id: str, page_no: int, box: list[float]) -> str | None:
    """取该 bbox 在页面上"实际显示"的文本。

    识别模型训练要求图像与标签一致：如果人工把 8X 改成 BX，图里仍是 8X，
    直接用校正值当标签会教坏模型。因此 rec 标签只取观测文本。
    """
    row = db.query_one("SELECT ocr_json FROM page WHERE doc_id=? AND page_no=?", (doc_id, page_no))
    if not row or not row["ocr_json"]:
        return None
    path = abs_of(row["ocr_json"])
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lines = payload.get("pages", {}).get(f"p{page_no}", {}).get("lines", [])
    except Exception:
        return None
    best, best_iou = None, 0.0
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    for ln in lines:
        b = ln.get("box")
        if not b:
            continue
        score = iou(b, box)
        if score > best_iou:
            best, best_iou = ln.get("text"), score
    if best and best_iou >= 0.5:
        return best
    for ln in lines:  # 兜底：命中包围盒中心的那一行
        b = ln.get("box")
        if b and b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
            return ln.get("text")
    return None


def _crop(page_image: str, box: list[float], dest, pad: int = 4) -> bool:
    try:
        img = Image.open(abs_of(page_image))
        x0, y0, x1, y1 = box
        x0, y0 = max(0, int(x0) - pad), max(0, int(y0) - pad)
        x1, y1 = min(img.width, int(x1) + pad), min(img.height, int(y1) + pad)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return False
        img.crop((x0, y0, x1, y1)).save(dest, format="PNG")
        return True
    except Exception:
        return False


def export_dataset(note: str = "", include_crops: bool = True) -> dict:
    version = datetime.now().strftime("ds-%Y%m%d-%H%M%S")
    root = settings.datasets_dir / version
    crops_dir = root / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    docs = db.query_all(
        "SELECT d.*, r.action FROM document d JOIN review r ON r.doc_id = d.id "
        "WHERE r.action = 'CONFIRMED' ORDER BY d.created_at")
    if not docs:
        return {"version": None, "docs": 0, "fields": 0, "crops": 0,
                "message": "暂无可导出样本：需要先在复核页确认至少一份单据"}

    fields_out: list[dict] = []
    corrections_out: list[dict] = []
    rec_lines: list[str] = []
    crops = 0
    field_count = 0

    for doc in docs:
        doc_id = doc["id"]
        pages = {p["page_no"]: p["image_path"] for p in db.query_all(
            "SELECT page_no, image_path FROM page WHERE doc_id=?", (doc_id,))}
        corr_rows = db.query_all("SELECT * FROM correction WHERE doc_id=? ORDER BY id", (doc_id,))
        corrected_keys = set()
        for c in corr_rows:
            corrections_out.append(dict(c))
            corrected_keys.add(c["key"])
        fields = db.query_all("SELECT * FROM field WHERE doc_id=? ORDER BY id", (doc_id,))
        for f in fields:
            value = f["value_norm"] or f["value_raw"]
            if not value:
                continue
            field_count += 1
            bbox = db.loads(f["bbox"], None)
            item = {
                "doc_id": doc_id, "doc_type": doc["doc_type"], "page_no": f["page_no"],
                "key": f["key"], "label": f["label"], "value": value,
                "value_raw": f["value_raw"], "confidence": f["confidence"],
                "source": f["source"], "corrected": f["key"] in corrected_keys,
                "group": f["group_name"], "bbox": bbox,
            }
            fields_out.append(item)
            if include_crops and bbox and pages.get(f["page_no"]):
                name = f"{doc_id[:8]}_{f['page_no']}_{f['key'].replace('.', '_')}.png"
                dest = crops_dir / name
                if _crop(pages[f["page_no"]], bbox, dest):
                    crops += 1
                    observed = _observed_text(doc_id, f["page_no"], bbox) or f["value_raw"] or value
                    observed = observed.strip()
                    # 人工改过的字段图像与标签不一致，不进识别训练集（业务值仍在 fields.jsonl）
                    if observed and f["key"] not in corrected_keys and "\t" not in observed:
                        rec_lines.append(f"crops/{name}\t{observed}")

    (root / "fields.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in fields_out), encoding="utf-8")
    (root / "corrections.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in corrections_out), encoding="utf-8")
    (root / "rec_labels.txt").write_text("\n".join(rec_lines), encoding="utf-8")
    manifest = {
        "version": version, "created_at": db.now_iso(), "note": note,
        "docs": len(docs), "fields": field_count, "crops": crops,
        "label_policy": "仅人工确认（Gold）样本；未确认样本不得作为监督标签",
        "rec_label_policy": "识别训练标签取图像观测文本；人工改过的字段不进入 rec 训练集",
        "versions": {"pipeline": "r1.0", "ocr_model": settings.ocr_model},
        "files": ["fields.jsonl", "corrections.jsonl", "rec_labels.txt", "crops/"],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")

    db.execute("INSERT INTO dataset_export (id, version, path, docs, fields, crops, note, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (version, version, rel(root), len(docs), field_count, crops, note, db.now_iso()))
    manifest["path"] = rel(root)
    return manifest


def list_datasets() -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM dataset_export ORDER BY created_at DESC LIMIT 50")]


def delete_dataset(version: str) -> bool:
    row = db.query_one("SELECT * FROM dataset_export WHERE version=?", (version,))
    if not row:
        return False
    shutil.rmtree(settings.data_dir / row["path"], ignore_errors=True)
    db.execute("DELETE FROM dataset_export WHERE version=?", (version,))
    return True
