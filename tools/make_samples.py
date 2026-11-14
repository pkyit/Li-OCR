"""生成可行性验证用的样例单据（合成数据，不含任何真实客户信息）。

覆盖场景：
- 印刷体发票 / 回单 / 合同 / 收据 / 采购订单 / 结单 / 报销单
- 有文本层的 PDF（走确定性抽取路径）
- 拍摄质量差（模糊+倾斜+高压缩）
- 三类典型缺陷：税号校验位错、金额勾稽不平、必填字段缺失、金额格式不规范

用法：python -m tools.make_samples [输出目录]
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
OUT = Path(__file__).resolve().parents[1] / "samples"
W, H = 1240, 1754

_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_W = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc(body17: str) -> str:
    total = sum(_USCC_CHARS.index(c) * _USCC_W[i] for i, c in enumerate(body17))
    check = 31 - total % 31
    return body17 + _USCC_CHARS[0 if check == 31 else check]


def font(size: int):
    return ImageFont.truetype(FONT_PATH, size, index=0)


def new_page():
    img = Image.new("RGB", (W, H), "white")
    return img, ImageDraw.Draw(img)


def line(d, y, text, size=26, x=90, color="black", bold=False):
    d.text((x, y), text, font=font(size), fill=color)
    return y + int(size * 1.5)


def kv(d, y, k, v, size=26, kx=90, vx=430):
    d.text((kx, y), k, font=font(size), fill="#333333")
    d.text((vx, y), v, font=font(size), fill="black")
    return y + int(size * 1.6)


def rule(d, y, w=1060, x=90):
    d.line([(x, y), (x + w, y)], fill="#bbbbbb", width=2)
    return y + 14


def save(img: Image.Image, name: str, quality: int = 95):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    if name.lower().endswith((".jpg", ".jpeg")):
        img.save(path, quality=quality, subsampling=1)
    else:
        img.save(path, format="PNG", optimize=True)
    print(f"  {path.name}  {path.stat().st_size // 1024} KB")


# ---------------------------------------------------------------- 发票
def invoice(name="发票_增值税专用_01234567.png", *, tax_id=None, total="9,605.00",
            net="8,500.00", tax="1,105.00", with_date=True, quality=None):
    img, d = new_page()
    y = line(d, 70, "增值税专用发票", 46, x=380)
    y = line(d, 150, "发票代码：011002100111", 24)
    y = line(d, 190, "发票号码：01234567", 24)
    if with_date:
        line(d, 190, "开票日期：2026年11月02日", 24, x=700)
    y = 260
    y = kv(d, y, "购买方名称：", "上海鼎和贸易有限公司")
    y = kv(d, y, "统一社会信用代码：", uscc("91310000MA1FL7X23"))
    y = rule(d, y + 10)
    y = kv(d, y, "销售方名称：", "杭州云枢科技有限公司")
    y = kv(d, y, "纳税人识别号：", tax_id or uscc("91330106MA2H8Q4W1"))
    y = rule(d, y + 10)

    rows = [
        ("*信息技术服务*技术服务费", "1", "8,500.00", "13%", "1,105.00"),
        ("*信息技术服务*运维服务", "1", "2,300.00", "6%", "138.00"),
    ]
    d.text((90, y + 10), "项目名称", font=font(22), fill="#555")
    d.text((600, y + 10), "数量", font=font(22), fill="#555")
    d.text((700, y + 10), "金额", font=font(22), fill="#555")
    d.text((860, y + 10), "税率", font=font(22), fill="#555")
    d.text((960, y + 10), "税额", font=font(22), fill="#555")
    y += 50
    for r in rows:
        d.text((90, y), r[0], font=font(24))
        d.text((600, y), r[1], font=font(24))
        d.text((700, y), r[2], font=font(24))
        d.text((860, y), r[3], font=font(24))
        d.text((960, y), r[4], font=font(24))
        y += 46
    y = rule(d, y + 6)
    y = kv(d, y, "合计金额：", f"¥{net}")
    y = kv(d, y, "税额合计：", f"¥{tax}")
    y = kv(d, y, "价税合计（小写）：", f"¥{total}", size=28)
    y = kv(d, y, "备注：", "合同编号 HT-2026-0417")
    y = rule(d, y + 8)
    line(d, y + 10, "收款人：王敏          复核：李强          开票人：张伟", 22)
    line(d, y + 90, "销售方：（盖章）", 22)
    if quality:
        img = img.filter(ImageFilter.GaussianBlur(quality["blur"]))
        img = img.rotate(quality["rotate"], expand=True, fillcolor="white")
        save(img, name, quality.get("jpeg", 60))
    else:
        save(img, name)


# ---------------------------------------------------------------- 回单
def bank_receipt(name="银行回单_电子转账.png", *, amount="128,600.00", with_date=True):
    img, d = new_page()
    y = line(d, 70, "中国工商银行  电子回单", 40, x=300)
    y = line(d, 140, "回单编号：20261102-8845213", 24)
    if with_date:
        line(d, 140, "交易日期：2026-11-02", 24, x=700)
    y = rule(d, 200)
    y = kv(d, y + 20, "付款方名称：", "上海鼎和贸易有限公司")
    y = kv(d, y, "付款账号：", "6222 0212 3456 7890")
    y = kv(d, y, "收款方名称：", "杭州云枢科技有限公司")
    y = kv(d, y, "收款账号：", "1202 0248 0990 0123 456")
    y = kv(d, y, "开户行：", "工行杭州文三路支行")
    y = kv(d, y, "交易金额：", f"¥{amount}", size=30)
    y = kv(d, y, "币种：", "人民币")
    y = kv(d, y, "用途：", "技术服务费")
    y = rule(d, y + 10)
    line(d, y + 20, "本回单为电子凭证，可作为记账依据。", 22)
    save(img, name)


# ---------------------------------------------------------------- 收据
def receipt(name="收据_收款.png"):
    img, d = new_page()
    y = line(d, 80, "收 据", 46, x=520)
    y = line(d, 170, "收据编号：SJ-2026-1102-07", 24)
    y = kv(d, y + 60, "今收到：", "上海鼎和贸易有限公司")
    y = kv(d, y, "交来：", "技术服务费预付款")
    y = kv(d, y, "金额（小写）：", "¥35,000.00", size=30)
    y = kv(d, y, "币种：", "人民币")
    y = kv(d, y, "日期：", "2026年11月02日")
    y = kv(d, y, "收款单位：", "杭州云枢科技有限公司")
    y = kv(d, y, "联系电话：", "0571-8899 2031")
    line(d, y + 80, "（财务专用章）", 22, x=700)
    save(img, name)


# ---------------------------------------------------------------- 结单
def statement(name="银行结单_账户对账单.png"):
    img, d = new_page()
    y = line(d, 70, "招商银行  个人账户月结单", 38, x=260)
    y = line(d, 140, "账期：2026-10-01 至 2026-10-31", 24)
    y = kv(d, y + 30, "户名：", "上海鼎和贸易有限公司")
    y = kv(d, y, "账号：", "6225 8801 2345 6789")
    y = kv(d, y, "币种：", "人民币")
    y = rule(d, y + 10)
    d.text((90, y + 10), "交易日期", font=font(22), fill="#555")
    d.text((330, y + 10), "交易摘要", font=font(22), fill="#555")
    d.text((700, y + 10), "发生额", font=font(22), fill="#555")
    d.text((900, y + 10), "余额", font=font(22), fill="#555")
    y += 50
    rows = [("2026-10-03", "转账收入", "+85,000.00", "185,320.55"),
            ("2026-10-08", "服务费支出", "-12,400.00", "172,920.55"),
            ("2026-10-15", "税费扣缴", "-9,605.00", "163,315.55"),
            ("2026-10-22", "利息收入", "+126.30", "163,441.85")]
    for r in rows:
        d.text((90, y), r[0], font=font(24))
        d.text((330, y), r[1], font=font(24))
        d.text((700, y), r[2], font=font(24))
        d.text((900, y), r[3], font=font(24))
        y += 46
    y = rule(d, y + 8)
    y = kv(d, y, "本期结余：", "¥163,441.85", size=28)
    save(img, name)


# ---------------------------------------------------------------- 采购订单
def purchase_order(name="采购订单_2026-118.png", *, total="15,000.00", currency="人民币"):
    img, d = new_page()
    y = line(d, 70, "采 购 订 单", 42, x=430)
    y = line(d, 150, "订单编号：PO-2026-118", 24)
    line(d, 150, "日期：2026年11月05日", 24, x=760)
    y = kv(d, y + 50, "供应商：", "苏州恒信电子有限公司")
    y = kv(d, y, "采购方：", "上海鼎和贸易有限公司")
    y = kv(d, y, "币种：", currency)
    y = rule(d, y + 8)
    y += 20
    for item in [("服务器机箱", "台", "4", "3,000.00"), ("万兆交换机", "台", "1", "12,000.00")]:
        d.text((90, y), item[0], font=font(24))
        d.text((520, y), item[1], font=font(24))
        d.text((640, y), item[2], font=font(24))
        d.text((820, y), item[3], font=font(24))
        y += 46
    y = rule(d, y + 6)
    y = kv(d, y, "合计：", f"¥{total}", size=30)
    y = kv(d, y, "备注：", "含税，运费由供应商承担")
    save(img, name)


# ---------------------------------------------------------------- 报销单
def expense(name="费用报销单_差旅.png"):
    img, d = new_page()
    y = line(d, 70, "员工差旅费用报销单", 40, x=320)
    y = kv(d, y + 40, "报销单号：", "BX-2026-1102-014")
    y = kv(d, y, "报销部门：", "技术研发部")
    y = kv(d, y, "报销人：", "陈立")
    y = kv(d, y, "报销日期：", "2026年11月03日")
    y = rule(d, y + 8)
    y += 20
    for item in [("高铁票（上海-杭州）", "2026-10-28", "146.00"),
                 ("酒店住宿（2晚）", "2026-10-28", "760.00"),
                 ("市内交通", "2026-10-29", "88.50")]:
        d.text((90, y), item[0], font=font(24))
        d.text((620, y), item[1], font=font(24))
        d.text((820, y), item[2], font=font(24))
        y += 46
    y = rule(d, y + 6)
    y = kv(d, y, "报销金额：", "¥994.50", size=30)
    y = kv(d, y, "币种：", "人民币")
    y = kv(d, y, "审批人：", "赵宁")
    save(img, name)


# ---------------------------------------------------------------- 合同（文本层 PDF）
def contract_pdf(name="合同_技术服务协议.pdf"):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas

    pdfmetrics.registerFont(TTFont("MSYH", FONT_PATH, subfontIndex=0))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    c = rl_canvas.Canvas(str(path), pagesize=(595, 842))  # A4 点
    c.setFont("MSYH", 18)
    c.drawString(190, 790, "技术服务合同")
    c.setFont("MSYH", 11)
    lines = [
        "合同编号：HT-2026-0417",
        "签署日期：2026年10月20日",
        "甲方：上海鼎和贸易有限公司",
        "乙方：杭州云枢科技有限公司",
        "一、服务内容：乙方为甲方提供信息系统运维与技术支持服务。",
        "二、合同金额：人民币 128,000.00 元（含税）。",
        "三、付款方式：合同签署后 10 个工作日内支付 50%，验收后支付余款。",
        "四、服务期限：2026年11月01日 至 2027年10月31日。",
        "五、争议解决：双方协商不成，提交上海仲裁委员会仲裁。",
    ]
    y = 740
    for ln in lines:
        c.drawString(70, y, ln)
        y -= 26
    c.showPage()
    c.save()
    print(f"  {path.name}  {path.stat().st_size // 1024} KB")


def invoice_pdf(name="发票_文本层_增值税普通.pdf"):
    """带真实文本层的 PDF：用于验证"确定性抽取优先"的路径。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas

    pdfmetrics.registerFont(TTFont("MSYH", FONT_PATH, subfontIndex=0))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    c = rl_canvas.Canvas(str(path), pagesize=(595, 842))
    c.setFont("MSYH", 20)
    c.drawString(180, 790, "增值税普通发票")
    c.setFont("MSYH", 11)
    rows = [
        ("发票代码：031002100211", 760), ("发票号码：09876543", 742),
        ("开票日期：2026年11月06日", 724),
        ("购买方名称：上海鼎和贸易有限公司", 690),
        ("销售方名称：宁波海川物流有限公司", 664),
        ("纳税人识别号：" + uscc("91330203MA2AF9K7Q"), 646),
        ("金额：46,000.00", 610), ("税率：9%", 592), ("税额：4,140.00", 574),
        ("价税合计（小写）：¥50,140.00", 550),
        ("备注：运输服务费", 520),
    ]
    for text, y in rows:
        c.drawString(70, y, text)
    c.showPage()
    c.save()
    print(f"  {path.name}  {path.stat().st_size // 1024} KB")


def main() -> int:
    random.seed(20261114)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    globals()["OUT"] = out
    print(f"生成样例到 {out}")
    invoice()
    invoice("发票_税号校验位错_01234568.png", tax_id=uscc("91330106MA2H8Q4W1")[:-1] + "Z")
    invoice("发票_勾稽不平_01234569.png", total="10,000.00", net="8,500.00", tax="1,105.00")
    invoice("发票_缺日期_01234570.png", with_date=False)
    invoice("发票_拍摄模糊.jpg", quality={"blur": 1.9, "rotate": 2.4, "jpeg": 42})
    bank_receipt()
    bank_receipt("银行回单_缺日期.png", with_date=False)
    receipt()
    statement()
    purchase_order()
    purchase_order("采购订单_金额无小数.png", total="12,000")
    expense()
    contract_pdf()
    invoice_pdf()
    return 0


if __name__ == "__main__":
    sys.exit(main())
