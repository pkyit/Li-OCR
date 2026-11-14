"""OCR 引擎适配层：GPU 主力（PaddleOCR）+ CPU 旁路（RapidOCR）。

两条独立实现只产出统一 Line 列表，抽取与校验逻辑完全复用，
这样"交叉验证"是一种取证手段，而不是第二套业务代码。
"""
from __future__ import annotations

import os

# 必须在 import paddleocr 之前设置：走国内模型源，避免海外通道卡死
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .base import Line

log = logging.getLogger("liocr.engines")


@dataclass
class EngineResult:
    lines: list[Line] = field(default_factory=list)
    engine: str = ""
    elapsed_ms: int = 0
    error: str | None = None
    meta: dict = field(default_factory=dict)


def _poly_to_box(poly) -> list[float]:
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


class PaddleEngine:
    """GPU 主力识别：PP-OCRv5（mobile / server 可切换）。"""

    _instance: "PaddleEngine | None" = None
    _lock = threading.Lock()
    _available: bool | None = None

    def __init__(self, model: str, device: str) -> None:
        self.model = model
        self.device = device
        self._ocr = None
        self._infer_lock = threading.Lock()

    @classmethod
    def available(cls) -> bool:
        """Paddle 是否可导入。CPU-only 容器里没装 Paddle 时，整条流水线降级到 RapidOCR。"""
        if cls._available is None:
            try:
                import paddleocr  # noqa: F401
                cls._available = True
            except Exception:
                cls._available = False
        return cls._available

    @classmethod
    def get(cls, model: str, device: str) -> "PaddleEngine":
        if cls._instance is None:
            cls._instance = PaddleEngine(model, device)
        return cls._instance

    def _ensure(self):
        if self._ocr is not None:
            return self._ocr
        from paddleocr import PaddleOCR

        pairs = {
            "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
            "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
        }
        det, rec = pairs.get(self.model, pairs["mobile"])
        self._ocr = PaddleOCR(
            text_detection_model_name=det,
            text_recognition_model_name=rec,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=self.device,
        )
        self.det_name, self.rec_name = det, rec
        return self._ocr

    def warmup(self) -> None:
        """预热：把模型加载与显存分配放到启动阶段，避免首个请求超时。"""
        try:
            ocr = self._ensure()
            from PIL import Image
            tmp = Path(os.environ.get("TEMP", ".")) / "liocr_warmup.png"
            Image.new("RGB", (640, 320), "white").save(tmp)
            ocr.predict(str(tmp))
            tmp.unlink(missing_ok=True)
        except Exception:  # 预热失败不阻塞服务，首次请求时再报错
            pass

    def recognize(self, image_path: Path) -> EngineResult:
        t0 = time.time()
        try:
            with self._infer_lock:
                ocr = self._ensure()
                res = ocr.predict(str(image_path))
            lines: list[Line] = []
            for item in res:
                texts = item.get("rec_texts") or []
                scores = item.get("rec_scores") or []
                polys = item.get("rec_polys")
                boxes = item.get("rec_boxes")
                for i, text in enumerate(texts):
                    if not str(text).strip():
                        continue
                    if polys is not None and i < len(polys):
                        box = _poly_to_box(polys[i])
                    elif boxes is not None and i < len(boxes):
                        b = boxes[i]
                        box = [float(b[0]), float(b[1]), float(b[2]), float(b[3])]
                    else:
                        continue
                    score = float(scores[i]) if i < len(scores) else 0.0
                    lines.append(Line(text=str(text), box=box, score=score, source="paddle"))
            return EngineResult(lines=lines, engine=f"paddle-{self.model}",
                                elapsed_ms=int((time.time() - t0) * 1000),
                                meta={"det": getattr(self, "det_name", ""), "rec": getattr(self, "rec_name", "")})
        except Exception as exc:
            return EngineResult(engine=f"paddle-{self.model}", elapsed_ms=int((time.time() - t0) * 1000),
                                error=str(exc))


class RapidEngine:
    """CPU 旁路识别：独立实现 + 独立权重，用来制造"第二意见"。"""

    _instance: "RapidEngine | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._ocr = None
        self._infer_lock = threading.Lock()
        self.available = False
        self.reason = ""

    @classmethod
    def get(cls) -> "RapidEngine":
        if cls._instance is None:
            inst = RapidEngine()
            try:
                from rapidocr import RapidOCR  # noqa: F401
                inst.available = True
            except Exception as exc:
                inst.reason = f"rapidocr 未安装: {exc}"
            cls._instance = inst
        return cls._instance

    def _ensure(self):
        if self._ocr is None:
            from rapidocr import RapidOCR

            self._ocr = RapidOCR()
        return self._ocr

    def recognize(self, image_path: Path) -> EngineResult:
        if not self.available:
            return EngineResult(engine="rapid", error=self.reason)
        t0 = time.time()
        try:
            with self._infer_lock:
                out = self._ensure()(str(image_path))
            lines: list[Line] = []
            txts = getattr(out, "txts", None) or []
            scores = getattr(out, "scores", None) or []
            boxes = getattr(out, "boxes", None)
            if boxes is None:
                boxes = []
            for i, text in enumerate(txts):
                if not str(text).strip() or i >= len(boxes):
                    continue
                score = float(scores[i]) if i < len(scores) else 0.0
                lines.append(Line(text=str(text), box=_poly_to_box(boxes[i]), score=score, source="rapid"))
            return EngineResult(lines=lines, engine="rapid", elapsed_ms=int((time.time() - t0) * 1000))
        except Exception as exc:
            return EngineResult(engine="rapid", elapsed_ms=int((time.time() - t0) * 1000), error=str(exc))


class VLEngine:
    """PaddleOCR-VL：只在手写/低质量页兜底使用。

    代价明确：单页 12~40 s、接近占满 8GB 显存，因此不参与常规路径，
    也绝不用于数值裁定，只提供"可能漏掉的文本"这一份证据。
    """

    _instance: "VLEngine | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._pipe = None
        self._infer_lock = threading.Lock()
        self.available = False
        self.reason = ""

    @classmethod
    def get(cls) -> "VLEngine":
        if cls._instance is None:
            inst = VLEngine()
            try:
                from paddleocr import PaddleOCRVL  # noqa: F401
                inst.available = True
            except Exception as exc:
                inst.reason = f"PaddleOCR-VL 不可用: {exc}"
            cls._instance = inst
        return cls._instance

    def _ensure(self, device: str):
        if self._pipe is None:
            import paddle
            from paddle.base import framework as pf

            log.info("VL init: dygraph=%s pir=%s place=%s thread=%s",
                     pf.in_dygraph_mode(), pf.in_pir_mode(),
                     pf._current_expected_place(), threading.current_thread().name)
            # 静态/PIR 模式下 amp 的设备探测会拿到 Place(undefined:0)；
            # VL 是动态图模型，这里显式切回动态图并固定设备。
            if not pf.in_dygraph_mode():
                paddle.disable_static()
            paddle.set_device(device)
            log.info("VL init ready: dygraph=%s pir=%s place=%s",
                     pf.in_dygraph_mode(), pf.in_pir_mode(), pf._current_expected_place())
            from paddleocr import PaddleOCRVL
            self._pipe = PaddleOCRVL(device=device)
        return self._pipe

    def release(self) -> None:
        """用完即卸：VL 常驻约 7.9GB/8GB，会和主引擎抢显存，宁可下次重新加载。"""
        with self._infer_lock:
            if self._pipe is None:
                return
            self._pipe = None
        try:
            import paddle
            paddle.device.cuda.empty_cache()
        except Exception:
            pass

    def recognize(self, image_path: Path, device: str = "gpu:0") -> EngineResult:
        if not self.available:
            return EngineResult(engine="vl", error=self.reason)
        t0 = time.time()
        try:
            with self._infer_lock:
                out = self._ensure(device).predict(str(image_path))
            lines: list[Line] = []
            for res in out:
                md = getattr(res, "markdown", None)
                text = None
                if isinstance(md, str):
                    text = md
                elif isinstance(md, dict):
                    text = md.get("markdown_texts") or md.get("text")
                if not text:
                    # 兼容不同版本：markdown 缺失时退回版面解析块文本
                    blocks = getattr(res, "parsing_res_list", None) or []
                    text = "\n".join(str(b.get("block_content") or "") for b in blocks
                                     if isinstance(b, dict))
                if not text:
                    continue
                for raw in str(text).splitlines():
                    t = raw.strip().strip("|").strip()
                    if t:
                        # VL 不给逐行坐标，用退化框占位；抽取时只认"行内文本"，不做几何邻近
                        lines.append(Line(text=t, box=[0.0, 0.0, 0.0, 0.0], score=0.0, source="vl"))
            return EngineResult(lines=lines, engine="paddle-vl",
                                elapsed_ms=int((time.time() - t0) * 1000))
        except Exception as exc:
            log.exception("VL 兜底失败")
            return EngineResult(engine="vl", elapsed_ms=int((time.time() - t0) * 1000), error=str(exc))


def engines_summary(model: str, device: str, crosscheck: str,
                    vl_fallback: str = "auto") -> dict:
    rapid = RapidEngine.get()
    paddle_ok = PaddleEngine.available()
    vl = VLEngine.get()
    want = crosscheck not in ("off", "0", "false", "no")
    vl_want = vl_fallback not in ("off", "0", "false", "no")
    return {
        "paddle": {"model": model, "device": device, "ready": paddle_ok},
        "rapid": {"available": rapid.available,
                  "enabled": bool(rapid.available and (want or not paddle_ok)),
                  "primary": not paddle_ok,
                  "reason": rapid.reason},
        "vl": {"available": vl.available, "device": device,
               "enabled": bool(vl.available and vl_want and device.startswith("gpu")),
               "reason": vl.reason},
    }
