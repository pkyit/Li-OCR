"""字段抽取：标签几何 + 正则/校验器，规则版本化。

R1.0 的策略是"确定性优先"：
1) 用标签别名在文本行里定位；
2) 同行右侧 / 下方邻近块取值（几何邻近，不依赖模型语义）；
3) 按字段类型归一（金额、日期、税号、账号）；
4) 领域词表纠错（人工校正自动沉淀的收益入口）。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .base import (Line, Row, build_rows, card_variants, digits_len, luhn_ok, median_height,
                   parse_amount, parse_date, squash, to_halfwidth, union_box, uscc_valid)
from .io import PageAsset


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    aliases: tuple[str, ...]
    kind: str = "text"  # text | amount | date | id | account | rate
    group: str = "关键字段"
    max_len: int = 80
    only_types: tuple[str, ...] = ()  # 仅在这些单据类型下生效（用于金额口径区分）


RULES: tuple[Rule, ...] = (
    Rule("doc_no", "单据编号", ("发票号码", "发票号", "单据编号", "单据号", "凭证号", "合同编号",
                            "订单编号", "交易编号", "流水号", "回单编号", "报销单号"), "id"),
    Rule("invoice_code", "发票代码", ("发票代码",), "id"),
    Rule("doc_date", "日期", ("开票日期", "报销日期", "交易日期", "签署日期", "打印日期",
                            "记账日期", "账单日期", "生成日期", "账期", "日期"), "date"),
    Rule("seller", "销售方/收款方", ("销售方名称", "销售方", "收款单位", "收款方名称", "收款方",
                              "供应商名称", "供应商", "甲方", "户名", "账户名称", "商户名称",
                              "单位名称"), "text"),
    Rule("buyer", "购买方/付款方", ("购买方名称", "购买方", "购方名称", "付款单位", "付款方名称",
                              "付款方", "乙方", "客户名称"), "text"),
    Rule("amount_total", "金额合计", ("价税合计", "合计金额", "金额合计", "总金额", "合同金额",
                                  "付款金额", "开票金额", "应付金额", "实付金额", "报销金额",
                                  "交易金额", "本期结余", "金额(小写)", "合计", "总计", "总价",
                                  "余额", "小写", "金额"), "amount"),
    Rule("amount_tax", "税额", ("税额合计", "增值税额", "税额"), "amount"),
    Rule("amount_net", "不含税金额", ("不含税金额", "税前金额", "本金"), "amount"),
    # 发票上的"合计金额/金额"是不含税口径，放在独立规则里避免与价税合计混淆
    Rule("amount_net", "不含税金额", ("合计金额", "金额"), "amount", only_types=("增值税发票",)),
    Rule("currency", "币种", ("币种", "币别", "货币"), "currency", max_len=12),
    Rule("account_no", "账号/卡号", ("银行账号", "收款账号", "付款账号", "本方账号", "对方账号",
                                "账号", "卡号", "账户"), "account"),
    Rule("tax_id", "纳税人识别号", ("纳税人识别号", "纳税人识别码", "统一社会信用代码", "税号"), "id"),
    Rule("tax_rate", "税率", ("征收率", "税率"), "rate"),
    Rule("valid_until", "有效期至", ("有效期至", "有效期", "到期日", "截止日期"), "date"),
    Rule("bank_name", "开户行", ("开户银行", "开户行", "银行名称"), "text"),
    Rule("contact", "联系电话", ("联系电话", "联系方式", "电话"), "text", max_len=30),
    Rule("address", "地址", ("注册地址", "经营地址", "单位地址", "地址"), "text"),
    Rule("remark", "备注", ("备注", "附言", "用途"), "text", max_len=120),
)

_RULE_BY_KEY = {r.key: r for r in RULES}
_RULE_ORDER = {id(r): i for i, r in enumerate(RULES)}

DOC_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("增值税发票", ("增值税", "发票代码", "发票号码", "价税合计", "税率", "税控")),
    ("银行结单", ("结单", "对账单", "综合账户", "本期结余", "月结单", "账户结单")),
    ("费用报销单", ("报销单", "差旅费", "报销部门", "费用报销")),
    ("银行回单", ("回单", "转账凭证", "汇款凭证", "电子回单", "付款凭证")),
    ("合同协议", ("合同", "协议", "甲方", "乙方", "签署")),
    ("采购订单", ("采购订单", "订单号", "供应商", "采购单")),
    ("收据", ("收据", "今收到", "收款收据")),
)

REQUIRED: dict[str, tuple[str, ...]] = {
    "增值税发票": ("doc_no", "doc_date", "amount_total", "seller", "tax_id"),
    "银行结单": ("account_no", "doc_date"),
    "费用报销单": ("doc_no", "doc_date", "amount_total"),
    "银行回单": ("amount_total", "doc_date", "account_no"),
    "合同协议": ("doc_no", "doc_date", "amount_total"),
    "采购订单": ("doc_no", "doc_date", "amount_total"),
    "收据": ("amount_total", "doc_date"),
    "未知单据": ("doc_date",),
}

_RE_SEP = re.compile(r"^[\s:：=｜|·•—\-]+")
_RE_AMOUNT = re.compile(r"-?\d[\d,]*(?:\.\d{1,2})?")
_RE_PCT = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%?")
_RE_KV = re.compile(r"^(?P<label>[\u4e00-\u9fa5A-Za-z()（）·]{2,12})\s*[:：]\s*(?P<val>\S.{0,48})$")
_RE_PUNCT = re.compile(r"^[\s:：,，.。;；()（）\-—_/|]+$")
_RE_SEAL = re.compile(r"^[（(]?(盖章|签字|签名|签章|公章|财务专用章)[）)]?$")

_NOISE_LABELS = {"说明", "注", "备注说明", "提示", "注意", "合计大写", "小写", "大写"}

# 币种是封闭集合，白名单可以有效拒绝"右侧恰好是另一个标签"（如 汇率）这类误取
CURRENCIES = {"人民币", "RMB", "CNY", "HKD", "港币", "港币HKD", "USD", "美元", "USD$",
              "EUR", "欧元", "JPY", "日元", "GBP", "英镑", "CHF", "瑞郎"}


@dataclass
class FieldValue:
    key: str
    label: str
    kind: str
    group: str = "关键字段"
    page_no: int = 1
    value_raw: str | None = None
    value_norm: str | None = None
    confidence: float = 0.0
    bbox: list[float] | None = None
    source: str = ""
    required: bool = False
    flags: list[str] = field(default_factory=list)
    candidate: dict | None = None


def detect_doc_type(texts: list[str]) -> str:
    joined = " ".join(texts)
    best, best_score = "未知单据", 0
    for name, kws in DOC_TYPES:
        score = sum(1 for kw in kws if kw in joined)
        if score > best_score:
            best, best_score = name, score
    return best


def _clean_value(text: str) -> str:
    return _RE_SEP.sub("", to_halfwidth(text)).strip()


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF


def _label_at(text: str, alias: str) -> int:
    """定位"像标签"的出现位置：前面不是汉字、后面不是汉字。

    这条边界规则可以避免 `付款方式` 被 `付款方` 命中、`由供应商承担` 被 `供应商` 命中。
    """
    start = 0
    while True:
        i = text.find(alias, start)
        if i < 0:
            return -1
        before_ok = i == 0 or not _is_cjk(text[i - 1])
        after = text[i + len(alias): i + len(alias) + 1]
        after_ok = (not after) or not _is_cjk(after)
        if before_ok and after_ok:
            return i
        start = i + 1


def _find_alias(text: str, index: list[tuple[str, Rule, int]]) -> tuple[Rule, str, int, int] | None:
    for alias, rule, ai in index:
        pos = _label_at(text, alias)
        if pos >= 0:
            return rule, alias, ai, pos
    return None


def _alias_index(doc_type: str) -> list[tuple[str, Rule, int]]:
    """按单据类型构建别名索引：长别名优先 → 类型限定规则优先 → 声明顺序。"""
    entries: list[tuple[str, Rule, int]] = []
    for rule in RULES:
        if rule.only_types and doc_type not in rule.only_types:
            continue
        for ai, alias in enumerate(rule.aliases):
            entries.append((alias, rule, ai))
    entries.sort(key=lambda e: (-len(e[0]), 0 if e[1].only_types else 1,
                                _RULE_ORDER[id(e[1])], e[2]))
    return entries


def _value_candidates(line: Line, text: str, alias: str, alias_pos: int, row: Row, rows: list[Row],
                      row_idx: int, page_w: float):
    """按"同行右侧 → 下方邻近"依次产出候选值，交给类型可信性判断筛选。"""
    idx = alias_pos if alias_pos >= 0 else text.find(alias)
    rest = text[idx + len(alias):] if idx >= 0 else ""
    rest_clean = _clean_value(rest)
    if rest_clean and not _RE_PUNCT.match(rest_clean):
        yield rest_clean, line.box

    # VL 兜底不给坐标，只能依赖"标签与值在同一行"，禁止几何邻近取值
    if line.source == "vl":
        return

    rights = [l for l in row.lines if l.x0 >= line.x1 - 6]
    for l in rights[:3]:
        val = _clean_value(l.text)
        if val:
            yield val, l.box

    h = max(median_height(row.lines), 8.0)
    for r in rows[row_idx + 1: row_idx + 4]:
        if r.yc - line.yc > h * 2.4:
            break
        for l in r.lines:
            if line.x0 - page_w * 0.02 <= l.x0 <= line.x0 + page_w * 0.42:
                val = _clean_value(l.text)
                if val:
                    yield val, l.box


def _plausible(kind: str, text: str) -> bool:
    """候选值是否符合该字段类型的形态，用于拒绝"右侧正好是另一个标签"这类误取。"""
    t = text.strip()
    if not t or _RE_PUNCT.match(t) or _RE_SEAL.match(t) or t.endswith((":", "：")):
        return False
    if kind == "amount":
        return bool(re.search(r"\d", t))
    if kind == "date":
        return parse_date(t) is not None
    if kind == "rate":
        return "%" in t or bool(re.fullmatch(r"\d{1,2}(?:\.\d+)?%?", t))
    if kind == "account":
        d, star = digits_len(t)
        return d + star >= 8
    if kind == "id":
        return len(re.sub(r"[^0-9A-Za-z]", "", squash(t))) >= 4
    if kind == "currency":
        return squash(t).upper() in CURRENCIES or squash(t) in CURRENCIES
    return len(t) >= 2


def _pick_value(line: Line, text: str, alias: str, alias_pos: int, row: Row, rows: list[Row],
                row_idx: int, page_w: float, kind: str) -> tuple[str, list[float]] | None:
    for cand, box in _value_candidates(line, text, alias, alias_pos, row, rows, row_idx, page_w):
        if _plausible(kind, cand):
            return cand, box
    return None


def _normalize(kind: str, raw: str) -> tuple[str, str | None]:
    """返回 (展示值, 归一值)。归一失败时归一值为 None，由校验器标可疑。"""
    raw = raw.strip()
    if kind == "amount":
        m = _RE_AMOUNT.search(squash(raw))
        if not m:
            return raw, None
        value, dec = parse_amount(m.group(0))
        if value is None:
            return raw, None
        return m.group(0), f"{value:.2f}"
    if kind == "date":
        d = parse_date(raw)
        if not d:
            return raw, None
        return raw, d.isoformat()
    if kind == "account":
        norm = re.sub(r"[\s\-]", "", squash(raw))
        return raw, norm or None
    if kind == "rate":
        m = _RE_PCT.search(squash(raw))
        if not m:
            return raw, None
        return m.group(0), m.group(1)
    if kind == "id":
        norm = re.sub(r"[\s\-]", "", squash(raw))
        return raw, norm or None
    return raw, squash(raw) or None


def extract_fields(pages: list[PageAsset], lines_by_page: dict[int, list[Line]],
                   doc_type: str, lexicon: dict[str, set[str]] | None = None) -> list[FieldValue]:
    required_keys = set(REQUIRED.get(doc_type, ()))
    index = _alias_index(doc_type)
    # key -> (别名优先级, 行置信度, 字段值)；别名优先级越小越权威（如"价税合计"优于"合计"）
    chosen: dict[str, tuple[int, float, FieldValue]] = {}
    consumed: set[tuple[int, int]] = set()  # (page_no, line index)

    for page in pages:
        lines = lines_by_page.get(page.page_no, [])
        if not lines:
            continue
        rows = build_rows(lines)

        for ri, row in enumerate(rows):
            for line in row.lines:
                cleaned = _clean_value(line.text)
                hit = _find_alias(cleaned, index)
                if not hit:
                    continue
                rule, alias, alias_priority, alias_pos = hit
                prev = chosen.get(rule.key)
                if prev and prev[0] == 0:
                    continue
                picked = _pick_value(line, cleaned, alias, alias_pos, row, rows, ri, page.width,
                                     rule.kind)
                if not picked:
                    continue
                raw, box = picked
                if len(raw) > rule.max_len:
                    raw = raw[:rule.max_len]
                display, norm = _normalize(rule.kind, raw)
                fv = FieldValue(key=rule.key, label=rule.label, kind=rule.kind, group=rule.group,
                                page_no=page.page_no, value_raw=display, value_norm=norm,
                                confidence=min(line.score, 1.0), bbox=box, source=line.source,
                                required=rule.key in required_keys)
                if prev is None or alias_priority < prev[0] or (
                        alias_priority == prev[0] and line.score > prev[1]):
                    chosen[rule.key] = (alias_priority, line.score, fv)
                for row_line in row.lines:
                    consumed.add((page.page_no, id(row_line)))

    ordered: list[FieldValue] = []
    for rule in RULES:
        if rule.key in chosen:
            fv = chosen[rule.key][2]
            if all(f.key != fv.key for f in ordered):
                ordered.append(fv)
        elif rule.key in required_keys:
            ordered.append(FieldValue(key=rule.key, label=rule.label, kind=rule.kind,
                                      group=rule.group, required=True, flags=["MISSING"]))

    ordered.extend(_pattern_fields(pages, lines_by_page, ordered, doc_type))
    ordered.extend(_extra_fields(pages, lines_by_page, consumed, doc_type))
    return _apply_lexicon(ordered, lexicon or {})


def _extra_fields(pages: list[PageAsset], lines_by_page: dict[int, list[Line]],
                  consumed: set[tuple[int, int]], doc_type: str) -> list[FieldValue]:
    """未被规则消费的"标签：值"行，作为附加字段保留，扩大可复核面。"""
    extras: list[FieldValue] = []
    seen: set[str] = set()
    limit = 12 if doc_type in ("合同协议", "未知单据") else 6
    for page in pages:
        for line in lines_by_page.get(page.page_no, []):
            if (page.page_no, id(line)) in consumed:
                continue
            m = _RE_KV.match(_clean_value(line.text))
            if not m:
                continue
            label = m.group("label").strip("()（） ")
            val = m.group("val").strip()
            if label in _NOISE_LABELS or len(val) < 2 or label in seen:
                continue
            if _RE_SEAL.match(val) or _RE_PUNCT.match(val):
                continue
            if any(label.endswith(suffix) for suffix in ("金额", "名称", "日期", "账号", "号码")):
                continue
            seen.add(label)
            extras.append(FieldValue(key=f"x.{label}", label=label, kind="text", group="附加字段",
                                     page_no=page.page_no, value_raw=val, value_norm=squash(val),
                                     confidence=line.score, bbox=line.box, source=line.source))
            if len(extras) >= limit:
                return extras
    return extras


_RE_CARD_SCAN = re.compile(r"(?<!\d)\d[\d\s\-/]{12,24}\d(?!\d)")
_RE_USCC_SCAN = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z])")


def _group_digits(d: str) -> str:
    return " ".join(d[i:i + 4] for i in range(0, len(d), 4))


def _pattern_fields(pages: list[PageAsset], lines_by_page: dict[int, list[Line]],
                    fields: list[FieldValue], doc_type: str) -> list[FieldValue]:
    """标签缺失时的兜底：直接在整页文本里找"自带校验位"的强模式。

    手写页经常根本不存在"卡号："这种标签，数字就写在一行里；
    这种情况只能靠格式 + Luhn 校验把候选收敛成一个能自证的答案，
    并明确标成 INFERRED，交给人工确认，而不是当成自动识别结果。
    """
    have = {f.key for f in fields if f.value_norm}
    required = set(REQUIRED.get(doc_type, ()))
    found: list[FieldValue] = []

    if "account_no" not in have:
        # 只接受能通过 Luhn 的候选：日期串、流水号等纯数字长串过不了校验位，
        # 宁可继续标 MISSING 交人工，也不要凭空造一个"看起来像卡号"的值。
        best: tuple[float, str, int, str, Line] | None = None
        for page in pages:
            for line in lines_by_page.get(page.page_no, []):
                for m in _RE_CARD_SCAN.finditer(to_halfwidth(line.text)):
                    for digits, repaired in card_variants(m.group(0)):
                        if not (16 <= len(digits) <= 19) or not luhn_ok(digits):
                            continue
                        score = (2 if repaired else 0) + len(digits) / 100
                        if best is None or score > best[0]:
                            best = (score, digits, page.page_no, line.source, line)
        if best:
            _, digits, page_no, source, line = best
            flags = ["INFERRED"] + (["VL_FALLBACK"] if source == "vl" else [])
            found.append(FieldValue(
                key="account_no", label="账号/卡号", kind="account", page_no=page_no,
                value_raw=_group_digits(digits), value_norm=digits,
                confidence=float(line.score or 0.0),
                bbox=line.box if (line.box and line.box[2] - line.box[0] > 1) else None,
                source=source, required="account_no" in required, flags=flags))

    if "tax_id" not in have:
        for page in pages:
            for line in lines_by_page.get(page.page_no, []):
                hit = None
                for m in _RE_USCC_SCAN.finditer(squash(line.text).upper()):
                    if uscc_valid(m.group(0)):
                        hit = m.group(0)
                        break
                if hit:
                    flags = ["INFERRED"] + (["VL_FALLBACK"] if line.source == "vl" else [])
                    found.append(FieldValue(
                        key="tax_id", label="纳税人识别号", kind="id", page_no=page.page_no,
                        value_raw=hit, value_norm=hit, confidence=float(line.score or 0.0),
                        bbox=line.box if (line.box and line.box[2] - line.box[0] > 1) else None,
                        source=line.source, required="tax_id" in required, flags=flags))
                    break
            if found and found[-1].key == "tax_id":
                break
    return found


def _apply_lexicon(fields: list[FieldValue], lexicon: dict[str, set[str]]) -> list[FieldValue]:
    """领域词表纠错：人工确认过的机构名/户名/开户行，能稳定修正近似错字。"""
    targets = {"seller": "seller", "buyer": "buyer", "bank_name": "bank"}
    for fv in fields:
        kind = targets.get(fv.key)
        if not kind or not fv.value_norm:
            continue
        pool = lexicon.get(kind) or set()
        if not pool or fv.value_norm in pool:
            continue
        match = difflib.get_close_matches(fv.value_norm, list(pool), n=1, cutoff=0.86)
        if match and match[0] != fv.value_norm:
            fv.candidate = {"lexicon": match[0], "before": fv.value_norm}
            fv.value_raw = match[0]
            fv.value_norm = match[0]
            fv.flags.append("LEXICON_FIXED")
    return fields


def compare_key(kind: str, a: str | None, b: str | None) -> bool:
    """跨引擎比较：金额/日期先归一化再比，避免格式差异造成假冲突。"""
    if a is None or b is None:
        return a == b
    if kind == "amount":
        va, _ = parse_amount(a)
        vb, _ = parse_amount(b)
        if va is not None and vb is not None:
            return va == vb
        return squash(a) == squash(b)
    if kind == "date":
        da, db = parse_date(a), parse_date(b)
        if da and db:
            return da == db
    if kind == "account":
        na = re.sub(r"[^0-9A-Za-z*]", "", squash(a))
        nb = re.sub(r"[^0-9A-Za-z*]", "", squash(b))
        if na and nb:
            return na == nb
    sa, sb = squash(a), squash(b)
    if sa and sb and (sa in sb or sb in sa) and min(len(sa), len(sb)) >= 4:
        return True
    return difflib.SequenceMatcher(None, sa, sb).ratio() >= 0.90


def to_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    v, _ = parse_amount(text)
    return v
