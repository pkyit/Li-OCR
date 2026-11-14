"""文本行模型、归一化与几何工具。

统一约定：box = [x0, y0, x1, y1]，单位是页面渲染图的像素坐标（左上角原点）。
不同引擎（文本层 / PaddleOCR / RapidOCR）都先转成 Line，后续逻辑只认 Line，
这样"交叉验证"只需换一份 Line 列表，不需要改抽取代码。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass
class Line:
    text: str
    box: list[float]
    score: float = 1.0
    source: str = ""

    @property
    def x0(self) -> float:
        return self.box[0]

    @property
    def y0(self) -> float:
        return self.box[1]

    @property
    def x1(self) -> float:
        return self.box[2]

    @property
    def y1(self) -> float:
        return self.box[3]

    @property
    def yc(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def h(self) -> float:
        return max(1.0, self.box[3] - self.box[1])

    def to_dict(self) -> dict:
        return {"text": self.text, "box": [round(v, 1) for v in self.box],
                "score": round(self.score, 4), "source": self.source}


def to_halfwidth(text: str) -> str:
    """全角转半角，NFKC 兼顾数字、字母与常见标点。"""
    return unicodedata.normalize("NFKC", text)


def squash(text: str) -> str:
    """比较用：去空白、统一全半角、统一标点。"""
    t = to_halfwidth(text)
    t = "".join(t.split())
    return t.translate(str.maketrans({
        "，": ",", "。": ".", "；": ";", "：": ":", "、": ",",
        "（": "(", "）": ")", "－": "-", "—": "-", "–": "-",
    }))


_CUR = "¥￥$€£元圆"


def parse_amount(text: str) -> tuple[Decimal | None, int]:
    """金额解析。返回 (数值, 小数位数)；解析失败返回 (None, 0)。

    只接受纯数字形态，避免把 "1,130.83元" 之外的噪声当金额。
    """
    if not text:
        return None, 0
    t = squash(text)
    t = t.replace(" ", "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = re.sub(r"^(?:人民币|RMB|CNY|HKD|USD|USD\$|港币|美元)", "", t, flags=re.I)
    t = t.strip(_CUR).strip()
    t = t.replace(",", "")
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None, 0
    dec = len(t.split(".")[1]) if "." in t else 0
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None, 0
    return (-v if neg else v), dec


_DATE_PATTERNS = [
    ("%Y-%m-%d", r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    ("%Y/%m/%d", r"(\d{4})/(\d{1,2})/(\d{1,2})"),
    ("%Y.%m.%d", r"(\d{4})\.(\d{1,2})\.(\d{1,2})"),
    ("%Y年%m月%d日", r"(\d{4})年(\d{1,2})月(\d{1,2})日?"),
    ("%Y%m%d", r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),
]


def parse_date(text: str) -> date | None:
    if not text:
        return None
    t = squash(text).strip()
    t = re.sub(r"^(?:日期|开票日期|交易日期)[:：]?", "", t)
    for _, rx in _DATE_PATTERNS[:4]:
        m = re.search(rx, t)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    m = re.search(_DATE_PATTERNS[4][1], t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_W = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc_valid(code: str) -> bool:
    """统一社会信用代码 GB32100-2015 校验位验证。"""
    c = squash(code).upper()
    if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", c):
        return False
    total = 0
    for i in range(17):
        idx = _USCC_CHARS.find(c[i])
        if idx < 0:
            return False
        total += idx * _USCC_W[i]
    check = 31 - total % 31
    if check == 31:
        check = 0
    return _USCC_CHARS[check] == c[17]


def digits_len(text: str) -> tuple[int, int]:
    """返回 (数字个数, 星号个数)，用于判断掩码账号。"""
    d = sum(ch.isdigit() for ch in text)
    star = text.count("*") + text.count("×") + text.count("x")
    return d, star


def luhn_ok(digits: str) -> bool:
    """银行卡号 Luhn 校验：16~19 位数字才判定，其余返回 False。"""
    if not digits.isdigit() or not (16 <= len(digits) <= 19):
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def card_variants(raw: str) -> list[tuple[str, bool]]:
    """从 OCR 片段里还原卡号候选。

    OCR 常把数字 1 认成 / 或 l、把 0 认成 O，因此对一个片段给出多个变体，
    由调用方用 Luhn 选最可信的那个——"校验位可验证"优先于"模型自评分"。
    """
    cleaned = re.sub(r"[^\d/]", "", raw)
    variants: list[tuple[str, bool]] = []
    base = re.sub(r"\D", "", cleaned)
    if base:
        variants.append((base, False))
    if "/" in cleaned:
        repaired = re.sub(r"\D", "", cleaned.replace("/", "1"))
        if repaired:
            variants.append((repaired, True))
    # l→1、O→0 的常见混淆
    alt = raw.replace("l", "1").replace("I", "1").replace("O", "0").replace("o", "0")
    alt_digits = re.sub(r"\D", "", alt)
    if alt_digits and alt_digits not in {v[0] for v in variants}:
        variants.append((alt_digits, True))
    return variants


@dataclass
class Row:
    yc: float
    lines: list[Line] = field(default_factory=list)


def build_rows(lines: Iterable[Line], tol_ratio: float = 0.6) -> list[Row]:
    """按纵向中心聚类成行，用于"标签在左、值在右"的取值。"""
    items = sorted(lines, key=lambda l: (l.yc, l.x0))
    rows: list[Row] = []
    for ln in items:
        tol = max(6.0, ln.h * tol_ratio)
        if rows and abs(rows[-1].yc - ln.yc) <= tol:
            rows[-1].lines.append(ln)
            ys = [x.yc for x in rows[-1].lines]
            rows[-1].yc = sum(ys) / len(ys)
        else:
            rows.append(Row(yc=ln.yc, lines=[ln]))
    for r in rows:
        r.lines.sort(key=lambda l: l.x0)
    return rows


def median_height(lines: list[Line]) -> float:
    if not lines:
        return 12.0
    hs = sorted(l.h for l in lines)
    return hs[len(hs) // 2]


def union_box(boxes: list[list[float]]) -> list[float]:
    xs0 = [b[0] for b in boxes]
    ys0 = [b[1] for b in boxes]
    xs1 = [b[2] for b in boxes]
    ys1 = [b[3] for b in boxes]
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


def iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def today() -> date:
    return datetime.now().date()
