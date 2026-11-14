"""校验与可疑判定。

可疑信号只做"提请人工看一眼"，不做自动改写；每个信号都有原因码，
原因码直接用于可疑问题聚合页与后续规则改进归因。
"""
from __future__ import annotations

from datetime import date, timedelta

from .base import luhn_ok, parse_amount, parse_date, squash, uscc_valid
from .extract import FieldValue, compare_key, to_decimal

REASON_TEXT = {
    "LOW_CONF": "识别置信度低",
    "UNPARSEABLE": "格式无法解析",
    "FORMAT": "格式不规范",
    "CHECKSUM_FAIL": "校验位不通过",
    "DATE_OUT_OF_RANGE": "日期超出合理范围",
    "MISSING": "必填字段未找到",
    "CROSS_ENGINE_DIFF": "双引擎结果不一致",
    "SUM_MISMATCH": "金额勾稽不通过",
    "LEXICON_FIXED": "词表已纠错",
    "SINGLE_ENGINE": "仅单引擎证据",
    "INFERRED": "无标签模式推断",
    "VL_FALLBACK": "来自 VL 兜底",
}

_SEVERITY = {
    "CROSS_ENGINE_DIFF": 3,
    "MISSING": 3,
    "SUM_MISMATCH": 3,
    "CHECKSUM_FAIL": 2,
    "UNPARSEABLE": 2,
    "DATE_OUT_OF_RANGE": 2,
    "LOW_CONF": 1,
    "FORMAT": 1,
    "SINGLE_ENGINE": 0,
    "LEXICON_FIXED": 0,
    "INFERRED": 1,
    "VL_FALLBACK": 1,
}


def _threshold(kind: str, base: float, amount: float) -> float:
    return amount if kind == "amount" else base


def validate_fields(fields: list[FieldValue], cross: dict[str, FieldValue] | None,
                    base_conf: float, amount_conf: float) -> list[FieldValue]:
    by_key = {f.key: f for f in fields}
    today = date.today()

    for fv in fields:
        # 保留抽取阶段给出的"来源"标记，只清掉上一轮校验产生的信号
        fv.flags = [f for f in fv.flags if f in ("MISSING", "INFERRED", "VL_FALLBACK")]
        if not fv.value_norm:
            if fv.required and "MISSING" not in fv.flags:
                fv.flags.append("MISSING")
        else:
            if fv.kind in ("amount", "date", "id", "account", "rate") and not _is_parseable(fv):
                fv.flags.append("UNPARSEABLE")
            elif fv.kind == "amount":
                # 用原始值判断小数位，归一后的值恒为两位小数，看不出"12000"这种缺小数的问题
                v, dec = parse_amount(fv.value_raw or "")
                if v is not None and dec != 2:
                    fv.flags.append("FORMAT")
            elif fv.kind == "date":
                d = parse_date(fv.value_norm)
                if d and not (date(1990, 1, 1) <= d <= today + timedelta(days=3650)):
                    fv.flags.append("DATE_OUT_OF_RANGE")
            elif fv.kind == "id":
                code = (fv.value_norm or "").upper()
                if len(code) == 18 and not uscc_valid(code):
                    fv.flags.append("CHECKSUM_FAIL")
                elif len(code) not in (15, 17, 18, 20) and len(code) < 8:
                    fv.flags.append("FORMAT")
            elif fv.kind == "account":
                raw = fv.value_norm or ""
                digits = sum(ch.isdigit() for ch in raw)
                if digits < 8:
                    fv.flags.append("FORMAT")
                elif digits >= 16 and raw.isdigit() and not luhn_ok(raw):
                    fv.flags.append("CHECKSUM_FAIL")

            if fv.confidence and fv.confidence < _threshold(fv.kind, base_conf, amount_conf):
                fv.flags.append("LOW_CONF")

            if cross is not None:
                other = cross.get(fv.key)
                if other and other.value_norm:
                    if not compare_key(fv.kind, fv.value_norm, other.value_norm):
                        fv.flags.append("CROSS_ENGINE_DIFF")
                        fv.candidate = dict(fv.candidate or {})
                        fv.candidate["cross"] = {
                            "engine": other.source or "rapid",
                            "value": other.value_raw or other.value_norm,
                            "norm": other.value_norm,
                            "confidence": round(other.confidence, 4),
                            "page_no": other.page_no,
                            "bbox": other.bbox,
                        }

    _check_sum(by_key)
    for fv in fields:
        fv.flags = list(dict.fromkeys(fv.flags))
    return fields


def _is_parseable(fv: FieldValue) -> bool:
    """value_norm 已由抽取阶段归一；此处只判断归一是否成功。"""
    if fv.kind == "amount":
        v, _ = parse_amount(fv.value_norm or "")
        return v is not None
    if fv.kind == "date":
        return parse_date(fv.value_norm or "") is not None
    return bool(fv.value_norm)


def _check_sum(by_key: dict[str, FieldValue]) -> None:
    total = to_decimal(by_key.get("amount_total").value_norm) if by_key.get("amount_total") else None
    net = to_decimal(by_key.get("amount_net").value_norm) if by_key.get("amount_net") else None
    tax = to_decimal(by_key.get("amount_tax").value_norm) if by_key.get("amount_tax") else None
    if total is not None and net is not None and tax is not None:
        if abs(net + tax - total) > __import__("decimal").Decimal("0.02"):
            by_key["amount_total"].flags.append("SUM_MISMATCH")


def field_status(fv: FieldValue) -> str:
    blocking = [f for f in fv.flags if _SEVERITY.get(f, 1) > 0]
    if not fv.value_norm:
        return "MISSING"
    return "SUSPECT" if blocking else "OK"


def doc_needs_review(fields: list[FieldValue]) -> bool:
    return any(field_status(f) != "OK" for f in fields)


def group_issues(fields: list[FieldValue]) -> dict[str, int]:
    """按原因码聚合，供"可疑问题"页面。"""
    counter: dict[str, int] = {}
    for fv in fields:
        for flag in fv.flags:
            counter[flag] = counter.get(flag, 0) + 1
    return counter


def severity_of(flag: str) -> int:
    return _SEVERITY.get(flag, 1)
