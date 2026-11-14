"""Li-OCR R1.0 配置。

所有可变配置来自环境变量（支持 .env），代码中不硬编码任何凭据。
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv 为可选依赖，缺失也不影响启动
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


class Settings:
    """运行时配置快照。"""

    def __init__(self) -> None:
        self.host = _env("LIOCR_HOST", "127.0.0.1")
        self.port = _env_int("LIOCR_PORT", 8080)
        self.api_token = _env("LIOCR_API_TOKEN")
        self.cors_origins = [o for o in _env("LIOCR_CORS_ORIGINS").split(",") if o]
        self.env = _env("LIOCR_ENV", "dev")

        self.base_dir = Path(__file__).resolve().parents[2]
        self.data_dir = Path(_env("LIOCR_DATA_DIR", str(self.base_dir / "data"))).expanduser()
        self.db_path = Path(_env("LIOCR_DB_PATH", str(self.data_dir / "liocr.sqlite3"))).expanduser()

        self.device = _env("LIOCR_DEVICE", "gpu:0")
        self.ocr_model = _env("LIOCR_OCR_MODEL", "mobile")  # mobile | server
        self.workers = max(1, _env_int("LIOCR_WORKERS", 1))
        self.render_dpi = _env_int("LIOCR_RENDER_DPI", 200)
        self.crosscheck = _env("LIOCR_CROSSCHECK", "auto").lower()
        # 手写/低质量页是否用 PaddleOCR-VL 兜底：auto=GPU 可用时启用，off=关闭
        self.vl_fallback = _env("LIOCR_VL_FALLBACK", "auto").lower()
        self.conf_threshold = _env_float("LIOCR_CONF_THRESHOLD", 0.90)
        self.conf_threshold_amount = _env_float("LIOCR_CONF_THRESHOLD_AMOUNT", 0.95)

        self.raw_dir = self.data_dir / "raw"
        self.pages_dir = self.data_dir / "pages"
        self.ocr_dir = self.data_dir / "ocr"
        self.datasets_dir = self.data_dir / "datasets"
        self.eval_dir = self.data_dir / "eval"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.raw_dir, self.pages_dir, self.ocr_dir, self.datasets_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
