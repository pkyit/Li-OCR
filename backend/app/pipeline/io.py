"""文件 → 页面资产（渲染图 + 文本层行）。

约定：所有页面统一渲染成 PNG 落盘，坐标以渲染图像素为准。
PDF 有可信文本层时直接产出精确文本与坐标（确定性优先），否则走 OCR。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..storage import page_dir
from .base import Line, union_box


@dataclass
class PageAsset:
    page_no: int
    image_path: Path
    width: int
    height: int
    text_lines: list[Line] = field(default_factory=list)
    text_chars: int = 0
    cjk_chars: int = 0
    layer_trusted: bool = False


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


def _lines_from_pdf_text(textpage, page_w: float, page_h: float, scale: float) -> list[Line]:
    """按字符坐标重建文本行，保留 bbox 供复核高亮。"""
    n = textpage.count_chars()
    if n <= 0:
        return []
    chars: list[tuple[str, list[float]]] = []
    for i in range(n):
        ch = textpage.get_text_range(i, 1)
        if not ch or ch in "\r\n":
            continue
        try:
            left, bottom, right, top = textpage.get_charbox(i)
        except Exception:
            continue
        if right - left <= 0 or top - bottom <= 0:
            continue
        box = [left * scale, (page_h - top) * scale, right * scale, (page_h - bottom) * scale]
        chars.append((ch, box))
    if not chars:
        return []

    chars.sort(key=lambda c: ((c[1][1] + c[1][3]) / 2, c[1][0]))
    rows: list[list[tuple[str, list[float]]]] = []
    for item in chars:
        yc = (item[1][1] + item[1][3]) / 2
        h = max(1.0, item[1][3] - item[1][1])
        if rows:
            last_yc = sum((c[1][1] + c[1][3]) / 2 for c in rows[-1]) / len(rows[-1])
            last_h = sorted(max(1.0, c[1][3] - c[1][1]) for c in rows[-1])[len(rows[-1]) // 2]
            if abs(yc - last_yc) <= max(5.0, last_h * 0.6):
                rows[-1].append(item)
                continue
        rows.append([item])

    lines: list[Line] = []
    for row in rows:
        row.sort(key=lambda c: c[1][0])
        buf: list[str] = []
        boxes: list[list[float]] = []
        prev_right = None
        widths = [(c[1][2] - c[1][0]) for c in row if c[0].strip()]
        avg_w = sum(widths) / len(widths) if widths else 6.0
        for ch, box in row:
            # 仅在明显的大间隔且两侧都是 ASCII 时补空格；中文按字排布，补空格会污染字段值
            if (prev_right is not None and box[0] - prev_right > avg_w * 0.95
                    and buf and buf[-1] != " " and ch.isascii() and buf[-1].isascii()):
                buf.append(" ")
            buf.append(ch)
            boxes.append(box)
            prev_right = box[2]
        text = "".join(buf).strip()
        if not text:
            continue
        lines.append(Line(text=text, box=union_box(boxes), score=1.0, source="textlayer"))
    return lines


def load_pages(raw_path: Path, doc_id: str, dpi: int = 200) -> list[PageAsset]:
    """把上传文件拆成页面资产，页面图写入 pages/<doc_id>/。"""
    out_dir = page_dir(doc_id)
    ext = raw_path.suffix.lower()
    pages: list[PageAsset] = []

    if ext == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(raw_path))
        scale = dpi / 72.0
        try:
            for idx in range(len(pdf)):
                page = pdf[idx]
                w_pt, h_pt = page.get_size()
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil().convert("RGB")
                img_path = out_dir / f"p{idx + 1:03d}.png"
                pil.save(img_path, format="PNG", optimize=True)

                lines: list[Line] = []
                cjk = 0
                try:
                    textpage = page.get_textpage()
                    lines = _lines_from_pdf_text(textpage, w_pt, h_pt, scale)
                except Exception:
                    lines = []
                text = "".join(l.text for l in lines)
                cjk = sum(1 for ch in text if _is_cjk(ch))
                trusted = cjk >= 20 and len(text) >= 60
                pages.append(PageAsset(
                    page_no=idx + 1, image_path=img_path, width=pil.width, height=pil.height,
                    text_lines=lines, text_chars=len(text), cjk_chars=cjk, layer_trusted=trusted))
        finally:
            pdf.close()
    else:
        img = Image.open(raw_path)
        pil = img.convert("RGB")
        img_path = out_dir / "p0001.png"
        pil.save(img_path, format="PNG", optimize=True)
        pages.append(PageAsset(page_no=1, image_path=img_path, width=pil.width, height=pil.height,
                               text_chars=0, cjk_chars=0, layer_trusted=False))
    return pages


def triage_kind(page: PageAsset) -> str:
    """页面类型判定：只用于路由与展示，不作为准确率承诺。"""
    if page.layer_trusted:
        return "TEXT_PDF"
    w, h = page.width, page.height
    ratio = w / h if h else 0
    if ratio < 0.62:  # 竖向长图更像手机拍摄/长截图
        return "PHOTO"
    if page.text_chars > 0:
        return "SCAN_MIXED"
    return "SCAN"
