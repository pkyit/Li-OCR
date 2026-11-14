"""文件落盘：原始件按 SHA256 归档，页面图与 OCR 结果按单据分目录。"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from .config import settings

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
MAX_FILE_BYTES = 200 * 1024 * 1024


class StorageError(Exception):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_upload(src: Path, filename: str, sha: str, move: bool = True) -> Path:
    """归档上传文件。move=False 时保留源文件（本地批量导入用，避免误删用户目录）。"""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise StorageError(f"不支持的文件类型: {ext or '未知'}")
    if src.stat().st_size > MAX_FILE_BYTES:
        raise StorageError("文件超过 200MB 限制")
    sub = settings.raw_dir / datetime.now().strftime("%Y%m")
    sub.mkdir(parents=True, exist_ok=True)
    dest = sub / f"{sha}{ext}"
    if not dest.exists():
        if move:
            shutil.move(str(src), dest)
        else:
            shutil.copy2(str(src), dest)
    elif move:
        src.unlink(missing_ok=True)
    return dest


def page_dir(doc_id: str) -> Path:
    d = settings.pages_dir / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def ocr_dir(doc_id: str) -> Path:
    d = settings.ocr_dir / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(path: Path) -> str:
    """存相对路径，便于整个 data 目录搬迁。"""
    try:
        return str(path.relative_to(settings.data_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def abs_of(rel_path: str) -> Path:
    p = Path(rel_path)
    return p if p.is_absolute() else settings.data_dir / p
