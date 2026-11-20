"""Programmatic (templated) mail: banks, travel, health reports, shopping, utilities, apps, internal systems.

Real-life notifications are templated, so these are generated from code with
varied slots. Needle notifications (statements, flights, hotels, reports) are
produced by the same functions as their siblings, with fixed facts.
"""

from __future__ import annotations

import datetime as dt
import random

from . import bible as B
from .calendar import (AS_OF, END, START, TZ, WORKDAYS, at, cn_date, cn_full, days, is_workday, leisure_time,
                       next_workday, rand_day, rand_workday, weekday_cn, work_time)

OWNER = B.OWNER
ME_W = {"name": OWNER["name"], "email": OWNER["email"], "org": B.COMPANY}
ME_P = {"name": OWNER["name"], "email": OWNER["personal_email"], "org": ""}
PEOPLE = B.all_people()


def svc(name: str) -> dict:
    s = next(x for x in B.SERVICES if x["name"] == name)
    return {"name": s["sender"], "email": s["email"], "org": s["name"]}


def mail(t: dt.datetime, frm: dict, to: list, subject: str, body: str, category: str, org=None, attachments=None, cc=None,
         thread=None, project=None, people=None, nid=None, kind=None):
    return {
        "date": t.isoformat(timespec="seconds"), "from": frm, "to": to, "cc": cc or [], "subject": subject, "body": body,
        "category": category, "attachments": attachments or [], "facets": {"org": org, "project": project, "people": people or []},
        "thread": thread, "nid": nid, "kind": kind,
    }


def att(name: str, kind: str, text: str, rng: random.Random, size: int | None = None) -> dict:
    mime = {"doc": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "sheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pdf": "application/pdf",
            "slides": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "image": "image/jpeg"}[kind]
    if name.lower().endswith(".csv"):
        mime = "text/csv"
    if name.lower().endswith(".png"):
        mime = "image/png"
    if size is None:
        lo, hi = {"doc": (18_000, 260_000), "sheet": (12_000, 420_000), "pdf": (60_000, 2_400_000),
                  "slides": (900_000, 14_000_000), "image": (180_000, 3_800_000)}[kind]
        size = int(lo * (hi / lo) ** rng.random())
    return {"name": name, "mime": mime, "size": int(size), "kind": kind, "text": text}


def money(x: float) -> str:
    return f"¥{x:,.2f}"


FOOT_BANK = ("\n\n温馨提示：本邮件由系统自动发送，请勿直接回复。为保障账户安全，星辰银行不会以任何形式向您索取密码、短信验证码等信息。"
             "如有疑问请致电客服热线 95xxx（境外请拨 +86-21-xxxx-xxxx）。\n星辰银行信用卡中心")

# ================================================================== banking
MERCH = [
    ("饭点外卖", 25, 120, "餐饮"), ("速达出行", 18, 95, "交通"), ("云购商城", 39, 899, "购物"), ("惠民超市（望京店）", 60, 420, "超市"),
    ("加油站（京承高速店）", 260, 480, "加油"), ("青柚视频", 25, 25, "娱乐"), ("畅联通信 话费充值", 100, 200, "通讯"),
    ("咖啡小站", 22, 48, "餐饮"), ("书香书城", 45, 260, "购物"), ("动力健身", 199, 199, "运动"), ("社区药房", 35, 180, "医疗"),
    ("停车缴费", 10, 60, "交通"), ("湘味小馆", 160, 560, "餐饮"), ("面包工坊", 28, 86, "餐饮"), ("儿童乐园", 98, 298, "娱乐"),
    ("鲜果时光", 30, 120, "超市"), ("影城（望京店）", 78, 240, "娱乐"), ("洗衣店", 40, 150, "生活"),
]


def _card_txns(rng: random.Random, lo: dt.date, hi: dt.date, n: int, extra=()) -> list[tuple]:
    out = list(extra)
    for _ in range(n):
        m, a, b, c = rng.choice(MERCH)
        amt = round(rng.uniform(a, b), 2) if a != b else float(a)
        out.append((rand_day(rng, lo, hi), m, amt))
    out.sort(key=lambda x: x[0])
    return out


def statement_text(bank: str, card: str, period: tuple[dt.date, dt.date], stmt_day: dt.date, due: dt.date, txns: list[tuple],
                   prev: float, paid: float, limit: int, points: int) -> tuple[str, float]:
    new = round(sum(a for _, _, a in txns), 2)
    total = round(prev - paid + new, 2)
    lines = [f"{bank}信用卡对账单（电子版）",
             f"持卡人：林舟    卡号：{card}    账单周期：{period[0]:%Y/%m/%d}-{period[1]:%Y/%m/%d}",
             f"账单日：{stmt_day:%Y年%m月%d日}    到期还款日：{due:%Y年%m月%d日}    信用额度：{money(limit)}",
             "",
             "【账户概要】",
             f"上期账单金额 {money(prev)} | 上期还款金额 {money(paid)} | 本期新增款项 {money(new)} | 本期应还金额 {money(total)} | 最低还款额 {money(round(total * 0.1, 2))}",
             "",
             "【本期交易明细】",
             "交易日 | 记账日 | 交易摘要 | 交易金额(人民币)"]
    for d, m, a in txns:
        post = d + dt.timedelta(days=1 if d.weekday() < 4 else 2)
        lines.append(f"{d:%m/%d} | {post:%m/%d} | {m} | {a:,.2f}")
    lines += ["", f"本期新增积分：{points}    积分余额：{points * 9 + 31250}",
              "", "【温馨提示】",
              "1. 请于到期还款日前还清本期应还金额，即可享受免息还款期待遇；仅还最低还款额将按日利率万分之五计收利息。",
              "2. 您已开通自动还款（星辰银行储蓄卡 **** 3390，全额），请确保扣款日账户余额充足。",
              "3. 如对账单有疑问，请在到期还款日前致电客服。"]
    return "\n".join(lines), total


def bank_mail(rng: random.Random, txn_plan: dict) -> list[dict]:
    """星辰银行 (6411) and 海川银行 (2087) statements, alerts, reminders, repayments."""
    out = []
    xc = svc("星辰银行")
    hc = svc("海川银行")
    # ---------------- 星辰银行: statement day 5th, due 25th
    prev = 9_876.40
    m = dt.date(2024, 1, 5)
    while m <= END:
        stmt_day = m
        lo = (m - dt.timedelta(days=1)).replace(day=6) if False else (dt.date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 6))
        period = (lo, stmt_day)
        key = f"{stmt_day:%Y-%m}"
        plan = txn_plan.get(("xc", key))
        if plan:
            txns = sorted(plan["txns"], key=lambda x: x[0])
        else:
            extra = txn_plan.get(("xc_extra", key), [])
            txns = _card_txns(rng, lo, stmt_day, rng.randint(30, 46), extra)
        due = dt.date(stmt_day.year, stmt_day.month, 25)
        text, total = statement_text("星辰银行", "**** 6411", period, stmt_day, due, txns, prev, prev, 80_000, int(sum(a for *_, a in txns)))
        if plan:
            assert abs(total - plan["total"]) < 0.005, (total, plan["total"])
        bill_month = lo.month if lo.day > 1 else stmt_day.month
        label = f"{lo.year}年{lo.month}月"
        t = at(stmt_day, 9, rng.randint(2, 40))
        body = (f"尊敬的林舟先生：\n\n您好！您的星辰银行信用卡（卡号末四位6411）{label}电子账单已生成。\n\n"
                f"账单周期：{period[0]:%Y/%m/%d}-{period[1]:%Y/%m/%d}\n本期应还金额：{money(total)}\n最低还款额：{money(round(total * 0.1, 2))}\n"
                f"到期还款日：{due.year}年{due.month}月{due.day}日\n信用额度：¥80,000.00\n\n"
                f"本期共{len(txns)}笔交易，明细请查看附件《星辰银行信用卡对账单_{lo:%Y%m}.pdf》（打开密码为您身份证号后6位）。"
                f"您已开通全额自动还款，系统将于到期还款日从尾号3390的储蓄卡扣款。" + FOOT_BANK)
        a = att(f"星辰银行信用卡对账单_{lo:%Y%m}.pdf", "pdf", text, rng, size=rng.randint(180_000, 420_000))
        if t <= AS_OF:
            out.append(mail(t, xc, [ME_P], f"星辰银行信用卡{label}电子账单（本期应还{money(total)}）", body, "finance", "星辰银行",
                            [a], nid=plan.get("nid") if plan else None, kind="cc_statement",
                            thread=f"xc-stmt-{key}"))
        # repayment reminder (5 days before due)
        rd = due - dt.timedelta(days=rng.choice([5, 6, 7]))
        t2 = at(rd, 10, rng.randint(0, 59))
        if t2 <= AS_OF:
            out.append(mail(t2, xc, [ME_P], f"【星辰银行】{label}账单还款提醒：本期应还{money(total)}，到期还款日{due.month}月{due.day}日",
                            f"尊敬的林舟先生：\n\n您尾号6411的星辰银行信用卡{label}账单本期应还金额为{money(total)}，最低还款额{money(round(total * 0.1, 2))}，"
                            f"到期还款日为{due.year}年{due.month}月{due.day}日。您已设置储蓄卡（尾号3390）全额自动还款，请确保账户余额充足。\n"
                            f"如已还款请忽略本提醒。" + FOOT_BANK, "finance", "星辰银行",
                            nid=(plan.get("nid_remind") if plan else None), kind="cc_remind"))
        t3 = at(due, 6, rng.randint(5, 30))
        if t3 <= AS_OF:
            out.append(mail(t3, xc, [ME_P], "【星辰银行】信用卡还款成功通知",
                            f"尊敬的林舟先生：\n\n您尾号6411的信用卡已于{due.month}月{due.day}日通过自动还款入账{money(total)}，{label}账单已全部还清。感谢您的使用！" + FOOT_BANK,
                            "finance", "星辰银行", kind="cc_paid"))
        # transaction alerts (every card transaction)
        for d, merch, a_ in txns:
            if True:
                h, mi = leisure_time(rng)
                ta = at(d, h, mi)
                if ta <= AS_OF:
                    out.append(mail(ta, xc, [ME_P], f"【星辰银行】消费提醒：您尾号6411信用卡消费{money(a_)}",
                                    f"尊敬的林舟先生：\n您尾号6411的信用卡于{d.month}月{d.day}日{h:02d}:{mi:02d}在「{merch}」消费人民币{money(a_)}。"
                                    f"\n如非本人操作，请立即致电客服冻结卡片。" + FOOT_BANK, "finance", "星辰银行", kind="cc_alert"))
        prev = total
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 5)
    # ---------------- 海川银行: statement 12th, due 1st of next month
    prev = 2_310.55
    m = dt.date(2024, 1, 12)
    while m <= END:
        lo = dt.date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 13)
        key = f"{m:%Y-%m}"
        plan = txn_plan.get(("hc", key))
        txns = sorted(plan["txns"]) if plan else _card_txns(rng, lo, m, rng.randint(8, 16))
        for d, merch, a_ in txns:
            ta = at(d, *leisure_time(rng))
            if ta <= AS_OF:
                out.append(mail(ta, hc, [ME_P], f"【海川银行】交易提醒：尾号2087信用卡支出{money(a_)}",
                                f"林舟先生，您尾号2087的信用卡{d.month}月{d.day}日在「{merch}」消费{money(a_)}。如非本人交易请致电海川银行客服。\n——海川银行信用卡中心",
                                "finance", "海川银行", kind="cc_alert"))
        due = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        text, total = statement_text("海川银行", "**** 2087", (lo, m), m, due, txns, prev, prev, 30_000, int(sum(a for *_, a in txns)))
        text = text.replace("星辰银行储蓄卡 **** 3390", "海川银行借记卡 **** 7720")
        label = f"{lo.year}年{lo.month}月"
        t = at(m, 8, rng.randint(10, 55))
        if t <= AS_OF:
            out.append(mail(t, hc, [ME_P], f"海川银行信用卡电子对账单（{m.year}年{m.month:02d}月）",
                            f"林舟 先生：\n\n您好！以下是您的海川银行信用卡（尾号2087）{m.year}年{m.month}月对账单摘要：\n"
                            f"本期应还：{money(total)}\n最低应还：{money(round(total * 0.1, 2))}\n到期还款日：{due:%Y-%m-%d}\n\n交易明细见附件。"
                            "\n\n——海川银行信用卡中心（本邮件为系统发送，请勿回复）",
                            "finance", "海川银行", [att(f"海川银行信用卡对账单_{m:%Y%m}.pdf", "pdf", text, rng)], kind="cc_statement",
                            nid=plan.get("nid") if plan else None))
        prev = total
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 12)
    return out


def savings_mail(rng: random.Random) -> list[dict]:
    """Salary credits, mortgage debits, payslips (internal HR system)."""
    out = []
    xc = svc("星辰银行")
    hc = svc("海川银行")
    hr = {"name": "云帆人力系统", "email": "hr-system@yunfan-tech.cn", "org": B.COMPANY}
    base = 46_800
    for y in (2024, 2025, 2026):
        for mo in range(1, 13):
            d = dt.date(y, mo, 10)
            if d > END:
                break
            gross = base * (1.06 if y >= 2025 else 1.0) * (1.04 if y >= 2026 else 1.0)
            si, hf = 2_633.4, 3_768.0
            tax = round((gross - si - hf - 5000 - 3500) * 0.2 - 1410, 2) if mo > 2 else round((gross - si - hf - 5000 - 3500) * 0.1 - 210, 2)
            net = round(gross - si - hf - tax, 2)
            t = at(d, 15, rng.randint(0, 59))
            out.append(mail(t, xc, [ME_P], f"【星辰银行】入账提醒：您尾号3390账户工资入账{money(net)}",
                            f"尊敬的林舟先生：\n您尾号3390的储蓄卡于{d.month}月{d.day}日{t:%H:%M}收到「云帆智能科技有限公司」代发工资{money(net)}。" + FOOT_BANK.replace("信用卡中心", "零售银行部"),
                            "finance", "星辰银行", kind="salary"))
            prev_m = dt.date(y - (mo == 1), (mo - 2) % 12 + 1, 1)
            table = (f"云帆智能科技有限公司 工资条（{prev_m.year}年{prev_m.month}月）\n姓名：林舟  部门：解决方案部  工号：YF0317\n"
                     f"项目 | 金额\n基本工资 | {gross * 0.7:,.2f}\n岗位津贴 | {gross * 0.2:,.2f}\n绩效工资 | {gross * 0.1:,.2f}\n应发合计 | {gross:,.2f}\n"
                     f"养老保险(个人) | -{si * 0.6:,.2f}\n医疗保险(个人) | -{si * 0.25:,.2f}\n失业保险(个人) | -{si * 0.15:,.2f}\n住房公积金(个人) | -{hf:,.2f}\n"
                     f"个人所得税 | -{tax:,.2f}\n实发工资 | {net:,.2f}\n\n本工资条仅供本人查阅，如有疑问请联系HRBP刘倩。")
            out.append(mail(at(d, 11, rng.randint(0, 59)), hr, [ME_W], f"【工资条】{prev_m.year}年{prev_m.month}月工资明细", table, "system", None,
                            kind="payslip"))
            md = dt.date(y, mo, 18)
            out.append(mail(at(md, 7, rng.randint(0, 30)), hc, [ME_P], "【海川银行】个人住房贷款还款提醒",
                            f"林舟先生，您的个人住房贷款（合同尾号0926）本月应还款{money(9_684.37)}，扣款日{y}年{mo}月20日，扣款账户：海川银行借记卡尾号7720。请确保余额充足。\n——海川银行",
                            "finance", "海川银行", kind="mortgage"))
            base = base
    return [x for x in out if x["date"] <= AS_OF.isoformat()]


# ================================================================== travel
AIR = svc("蓝鲸航空")
HOTEL = svc("悦庭酒店")
TRIP_ORG = {"上海": "瀚海物流", "苏州": "华川制造", "杭州": "青禾医疗", "西安": "北辰能源", "天津": "远洋港务", "郑州": "金穗农业", "嘉兴": "南湖文旅"}
CITY_AIRPORT = {"上海": ("上海虹桥", "SHA"), "深圳": ("深圳宝安", "SZX"), "杭州": ("杭州萧山", "HGH"), "西安": ("西安咸阳", "XIY"),
                "郑州": ("郑州新郑", "CGO"), "苏州": ("上海虹桥", "SHA"), "天津": None, "嘉兴": ("上海虹桥", "SHA"), "扬州": ("扬州泰州", "YTY"),
                "成都": ("成都天府", "TFU"), "三亚": ("三亚凤凰", "SYX")}
FLIGHT_NO = {"苏州": ("JW1503", "JW1510"), "嘉兴": ("JW1505", "JW1512"), "上海": ("JW1501", "JW1508"), "深圳": ("JW1305", "JW1308"), "杭州": ("JW1711", "JW1716"), "西安": ("JW1203", "JW1206"),
             "郑州": ("JW1631", "JW1634"), "扬州": ("JW1861", "JW1864"), "成都": ("JW1405", "JW1410"), "三亚": ("JW1953", "JW1958")}
DUR = {"苏州": (2, 15), "嘉兴": (2, 15), "上海": (2, 15), "深圳": (3, 25), "杭州": (2, 20), "西安": (2, 5), "郑州": (1, 40), "扬州": (1, 55), "成都": (3, 0), "三亚": (3, 50)}


def flight_mail(rng, trip: dict) -> list[dict]:
    """Booking, optional schedule change, check-in reminder for one round trip."""
    out = []
    city = trip["city"]
    if CITY_AIRPORT.get(city) is None:
        return out
    ap, code = CITY_AIRPORT[city]
    fo, fr = FLIGHT_NO[city]
    dh, dm = DUR[city]
    go_d, back_d = trip["go"], trip["back"]
    go_t = trip.get("go_time", (8, 0))
    back_t = trip.get("back_time", (18, 40))
    arr = (dt.datetime(2000, 1, 1, *go_t) + dt.timedelta(hours=dh, minutes=dm))
    barr = (dt.datetime(2000, 1, 1, *back_t) + dt.timedelta(hours=dh, minutes=dm + 5))
    order = trip.get("order") or f"JW{go_d:%y%m%d}{rng.randint(100000, 999999)}"
    pax = trip.get("pax", ["林舟"])
    price = trip.get("price") or rng.choice([1_380, 1_560, 1_740, 1_980, 2_210])
    pay = trip.get("pay", "企业月结（云帆差旅平台）")
    seat = "经济舱"
    booked = trip["booked"]
    lines = [f"旅客：{'、'.join(pax)}    订单号：{order}    支付方式：{pay}",
             f"去程：{fo}  {go_d:%Y-%m-%d}（{weekday_cn(go_d)}） {go_t[0]:02d}:{go_t[1]:02d} 北京大兴(PKX) → {arr:%H:%M} {ap}({code})  {seat}",
             f"返程：{fr}  {back_d:%Y-%m-%d}（{weekday_cn(back_d)}） {back_t[0]:02d}:{back_t[1]:02d} {ap}({code}) → {barr:%H:%M} 北京大兴(PKX)  {seat}",
             f"票价合计：{money(price * len(pax) * 2)}（含机场建设费及燃油附加费）",
             "电子客票号：" + "；".join(f"{p} 999-{rng.randint(1000, 9999)}******" for p in pax)]
    chan = "" if trip.get("family") else f"（预订渠道：云帆差旅平台；出差事由：{trip.get('purpose', '客户拜访')}，{city} {go_d:%m/%d}-{back_d:%m/%d}）"
    body = (f"尊敬的{'林舟先生' if len(pax) == 1 else '林舟先生及同行旅客'}：\n\n您的机票已出票成功，行程信息如下{chan}：\n\n" + "\n".join(lines) +
            "\n\n请于航班起飞前45分钟到达登机口，国内航班停止办理乘机手续时间为起飞前40分钟。"
            "您可通过蓝鲸航空App或官网办理在线值机（起飞前24小时开放）。\n行李额：经济舱免费托运20公斤。\n\n鲸享会会员号：JW88****215（金卡）\n\n蓝鲸航空 祝您旅途愉快")
    tb = at(booked, *trip.get("booked_time", work_time(rng)))
    tid = f"jw-{order}"
    out.append(mail(tb, AIR, [ME_P], f"【蓝鲸航空】出票成功：{go_d.month}月{go_d.day}日 北京大兴-{ap.replace('上海虹桥', '上海虹桥').replace('扬州泰州', '扬州泰州')} {fo}",
                    body, "travel", "蓝鲸航空",
                    [att(f"行程单_{order}.pdf", "pdf", "蓝鲸航空 电子客票行程单\n" + "\n".join(lines), rng, size=rng.randint(90_000, 160_000))],
                    nid=trip.get("nid_flight"), kind="flight_booking", thread=tid, people=pax))
    if trip.get("change"):
        old, new, when = trip["change"]
        narr = (dt.datetime(2000, 1, 1, *new) + dt.timedelta(hours=dh, minutes=dm))
        oarr = (dt.datetime(2000, 1, 1, *old) + dt.timedelta(hours=dh, minutes=dm))
        cb = (f"尊敬的林舟先生：\n\n非常抱歉地通知您，受航班计划调整影响，您预订的航班时刻发生变更：\n\n"
              f"航班号：{fo}\n日期：{go_d:%Y-%m-%d}（{weekday_cn(go_d)}）\n航段：北京大兴(PKX) → {ap}({code})\n"
              f"原计划：{old[0]:02d}:{old[1]:02d} 起飞，{oarr:%H:%M} 到达\n变更后：{new[0]:02d}:{new[1]:02d} 起飞，{narr:%H:%M} 到达\n\n"
              f"变更后您的完整行程（订单号{order}）：\n"
              f"去程：{fo}  {go_d:%Y-%m-%d}（{weekday_cn(go_d)}） {new[0]:02d}:{new[1]:02d} 北京大兴(PKX) → {narr:%H:%M} {ap}({code})  经济舱（已变更）\n"
              f"返程：{fr}  {back_d:%Y-%m-%d}（{weekday_cn(back_d)}） {back_t[0]:02d}:{back_t[1]:02d} {ap}({code}) → {barr:%H:%M} 北京大兴(PKX)  经济舱（不变）\n\n"
              f"您的座位与行李额度保持不变。如变更后的时刻不便出行，可在起飞前免费改签同一航线其他航班或申请全额退票。\n"
              f"对此给您带来的不便，我们深表歉意。\n\n蓝鲸航空客户服务中心")
        out.append(mail(at(when, 14, rng.randint(0, 59)), AIR, [ME_P], f"【蓝鲸航空】航班时刻变更通知：{fo} {go_d.month}月{go_d.day}日 起飞时间调整为{new[0]:02d}:{new[1]:02d}",
                        cb, "travel", "蓝鲸航空", nid=trip.get("nid_change"), kind="flight_change", thread=tid))
    for d, fno, t_, frm_ap, to_ap in [(go_d, fo, trip.get("change", (None, go_t))[1] if trip.get("change") else go_t, "北京大兴", ap),
                                      (back_d, fr, back_t, ap, "北京大兴")]:
        rem = at(d - dt.timedelta(days=1), 9, rng.randint(0, 50))
        if rem <= AS_OF:
            out.append(mail(rem, AIR, [ME_P], f"【蓝鲸航空】值机提醒：{fno} 明日{t_[0]:02d}:{t_[1]:02d} {frm_ap}-{to_ap}",
                            f"林舟先生您好，您乘坐的{fno}航班（{d:%m月%d日} {t_[0]:02d}:{t_[1]:02d} {frm_ap}-{to_ap}）现已开放在线值机，"
                            "请提前选座并办理值机手续。登机口将于起飞前15分钟关闭。\n\n蓝鲸航空", "travel", "蓝鲸航空", kind="checkin", thread=tid))
    return out


def hotel_mail(rng, trip: dict) -> list[dict]:
    out = []
    city = trip["city"]
    hotel = trip.get("hotel") or B.HOTELS.get(city)
    if not hotel:
        return out
    hname = hotel.split("（")[0]
    addr = hotel.split("（")[1].rstrip("）") if "（" in hotel else ""
    ci, co = trip["go"], trip["back"]
    nights = (co - ci).days
    conf = trip.get("conf") or f"YT{ci:%y%m%d}{rng.randint(1000, 9999)}"
    rate = trip.get("rate") or rng.choice([520, 580, 620, 680, 720])
    room = trip.get("room", "高级大床房")
    booked = trip.get("hotel_booked", trip["booked"])
    chan = "" if trip.get("family") else f"（预订渠道：云帆差旅平台；出差事由：{trip.get('purpose', '客户拜访')}，{city} {ci:%m/%d}-{co:%m/%d}）"
    body = (f"尊敬的林舟先生：\n\n感谢您选择悦庭酒店！您的预订已确认{chan}：\n\n确认号：{conf}\n酒店：{hname}\n地址：{addr}\n"
            f"入住日期：{ci:%Y年%m月%d日}（{weekday_cn(ci)}）  离店日期：{co:%Y年%m月%d日}（{weekday_cn(co)}）  共{nights}晚\n"
            f"房型：{room} 1间    价格：{money(rate)}/晚（{trip.get('rate_note', '企业协议价，含单早')}）\n"
            f"入住时间：14:00后  退房时间：12:00前\n担保方式：{trip.get('guarantee', '云帆差旅平台企业担保')}\n\n"
            f"如需取消或修改，请在入住日前一天18:00前通过悦庭会员App或致电酒店前台办理。\n悦庭会员号：YT66****093\n\n悦庭酒店集团 预订中心")
    tid = f"yt-{conf}"
    if not trip.get("cancel_only"):
        out.append(mail(at(booked, *trip.get("hotel_time", work_time(rng))), HOTEL, [ME_P],
                        f"【悦庭酒店】预订确认：{hname} {ci.month}月{ci.day}日入住 确认号{conf}", body, "travel", "悦庭酒店",
                        nid=trip.get("nid_hotel"), kind="hotel_booking", thread=tid, people=["林舟"]))
    if trip.get("hotel_cancel"):
        cd = trip["hotel_cancel"]
        out.append(mail(at(cd, 11, rng.randint(0, 59)), HOTEL, [ME_P], f"【悦庭酒店】预订取消确认：{hname} 确认号{conf}",
                        f"尊敬的林舟先生：\n\n您在{hname}的预订（确认号{conf}，{ci:%Y年%m月%d日}入住，{co:%m月%d日}离店，{trip.get('booked_by', '本人')}于{booked:%m月%d日}预订）已按您的要求取消，未产生取消费用。\n"
                        "期待下次为您服务。\n\n悦庭酒店集团 预订中心", "travel", "悦庭酒店", nid=trip.get("nid_hotel_cancel"), kind="hotel_cancel", thread=tid))
        return out
    folio_t = at(co, 11, rng.randint(0, 59))
    if folio_t <= AS_OF:
        extra = trip.get("folio_extra", rng.choice([0, 0, 68, 136, 245]))
        total = rate * nights + extra
        out.append(mail(folio_t, HOTEL, [ME_P], f"【悦庭酒店】您的住宿账单（{hname}，{ci.month}/{ci.day}-{co.month}/{co.day}）",
                        f"尊敬的林舟先生：\n\n感谢您入住{hname}。本次住宿账单如下，电子发票将发送至您的邮箱。\n", "travel", "悦庭酒店",
                        [att(f"悦庭酒店账单_{conf}.pdf", "pdf",
                             f"{hname} 宾客账单\n宾客：林舟  房号：{rng.randint(6, 22)}{rng.randint(1, 40):02d}  确认号：{conf}\n入住：{ci:%Y-%m-%d}  离店：{co:%Y-%m-%d}\n"
                             + "\n".join(f"{(ci + dt.timedelta(days=i)):%m-%d} 房费 {rate:,.2f}" for i in range(nights))
                             + (f"\n餐饮/杂项 {extra:,.2f}" if extra else "") + f"\n合计 {total:,.2f}\n结算方式：{trip.get('settle', '企业挂账')}", rng)],
                        kind="hotel_folio", thread=tid))
    return out


# ================================================================== health
def health_report_text(name: str, sex: str, age: int, d: dt.date, rows: list[tuple], findings: list[str], advice: list[str], no: str) -> str:
    head = [f"安康体检中心 健康体检报告", f"体检编号：{no}    姓名：{name}    性别：{sex}    年龄：{age}岁    体检日期：{d:%Y年%m月%d日}",
            "体检单位：云帆智能科技有限公司（年度体检）    体检门店：朝阳门店", "", "【主要检查结果】", "项目 | 结果 | 单位 | 参考范围 | 提示"]
    body = [f"{a} | {b} | {c} | {r} | {f}" for a, b, c, r, f in rows]
    tail = ["", "【主要异常及诊断】"] + [f"{i + 1}. {x}" for i, x in enumerate(findings)] + ["", "【总检建议】"] + \
           [f"{i + 1}. {x}" for i, x in enumerate(advice)] + ["", "主检医生：魏建平（副主任医师）    报告日期：" + f"{d + dt.timedelta(days=10):%Y年%m月%d日}",
                                                            "本报告仅对本次检查负责，如有疑问请咨询健康管理中心。"]
    return "\n".join(head + body + tail)


LIN_ROWS = {
    2024: dict(wt="71.0", bmi="23.2", waist="86", bp="122/80", glu="5.2", hba1c="5.4", tc="4.86", tg="1.42", hdl="1.18", ldl="2.96", ua="372", alt="24", cr="78", hb="152"),
    2025: dict(wt="72.5", bmi="23.7", waist="88", bp="126/82", glu="5.4", hba1c="5.5", tc="5.02", tg="1.56", hdl="1.15", ldl="3.12", ua="386", alt="28", cr="80", hb="150"),
    2026: dict(wt="77.8", bmi="25.4", waist="94", bp="132/86", glu="6.3", hba1c="5.9", tc="5.96", tg="1.82", hdl="1.02", ldl="4.26", ua="468", alt="35", cr="82", hb="151"),
}


def lin_rows(y: int) -> list[tuple]:
    v = LIN_ROWS[y]
    up = lambda cond: "↑" if cond else ""
    return [("身高", "175.0", "cm", "-", ""), ("体重", v["wt"], "kg", "-", ""), ("体重指数BMI", v["bmi"], "kg/m²", "18.5-23.9", up(float(v["bmi"]) >= 24)),
            ("腰围", v["waist"], "cm", "<90", up(int(v["waist"]) >= 90)), ("血压", v["bp"], "mmHg", "<140/90", ""),
            ("空腹血糖", v["glu"], "mmol/L", "3.9-6.1", up(float(v["glu"]) > 6.1)), ("糖化血红蛋白", v["hba1c"], "%", "4.0-6.0", ""),
            ("总胆固醇", v["tc"], "mmol/L", "<5.18", up(float(v["tc"]) > 5.18)), ("甘油三酯", v["tg"], "mmol/L", "<1.70", up(float(v["tg"]) > 1.7)),
            ("高密度脂蛋白胆固醇", v["hdl"], "mmol/L", ">1.04", "↓" if float(v["hdl"]) < 1.04 else ""),
            ("低密度脂蛋白胆固醇(LDL-C)", v["ldl"], "mmol/L", "<3.37", up(float(v["ldl"]) > 3.37)), ("尿酸", v["ua"], "μmol/L", "208-428", up(int(v["ua"]) > 428)),
            ("谷丙转氨酶(ALT)", v["alt"], "U/L", "9-50", ""), ("肌酐", v["cr"], "μmol/L", "57-111", ""), ("血红蛋白", v["hb"], "g/L", "130-175", ""),
            ("心电图", "窦性心律，正常心电图", "", "", ""), ("腹部B超", "脂肪肝（轻度）" if y >= 2025 else "肝胆胰脾肾未见明显异常", "", "", "")]


def health_mail(rng) -> list[dict]:
    out = []
    ak = svc("安康体检中心")
    plan = {2024: (dt.date(2024, 3, 19), dt.date(2024, 4, 12), dt.date(2024, 4, 23)),
            2025: (dt.date(2025, 3, 18), dt.date(2025, 4, 11), dt.date(2025, 4, 21)),
            2026: (dt.date(2026, 3, 17), dt.date(2026, 4, 10), dt.date(2026, 4, 20))}
    findings = {
        2024: ["腹部B超未见明显异常，各项指标基本在正常范围。", "低密度脂蛋白胆固醇2.96 mmol/L，处于正常范围上限附近。"],
        2025: ["低密度脂蛋白胆固醇3.12 mmol/L，接近临界值。", "尿酸386 μmol/L，正常高值。", "脂肪肝（轻度）。"],
        2026: ["血脂异常：低密度脂蛋白胆固醇4.26 mmol/L（↑），总胆固醇5.96 mmol/L（↑），甘油三酯1.82 mmol/L（↑）。",
               "高尿酸血症：尿酸468 μmol/L（↑）。", "空腹血糖受损：空腹血糖6.3 mmol/L（↑）。", "超重：体重77.8 kg，BMI 25.4，腰围94 cm。", "脂肪肝（轻度）。"],
    }
    advice = {
        2024: ["保持均衡饮食与规律作息。", "建议每年体检一次。"],
        2025: ["注意控制饮食中的油脂和高嘌呤食物，适量运动。", "一年后复查血脂、尿酸及腹部B超。"],
        2026: ["低脂低嘌呤饮食：减少红肉、动物内脏、海鲜及含糖饮料，戒酒或严格限酒。", "每周进行≥150分钟中等强度有氧运动（快走、慢跑、游泳等），控制体重。",
               "3个月后复查血脂、尿酸、空腹血糖，必要时加做糖耐量试验。", "如复查仍异常，建议内分泌科或心内科门诊就诊。"],
    }
    for y, (bk, exam, rep) in plan.items():
        fam = y == 2026
        out.append(mail(at(bk, 10, rng.randint(0, 59)), ak, [ME_P], f"【安康体检中心】体检预约成功：{exam.month}月{exam.day}日 朝阳门店",
                        f"尊敬的林舟先生：\n\n您已成功预约{exam:%Y年%m月%d日}（{weekday_cn(exam)}）08:00-10:00 安康体检中心朝阳门店的年度体检"
                        f"（云帆智能科技有限公司团检套餐A{'，并为家属苏晴预约同日家属套餐' if fam else ''}）。\n\n体检前注意事项：\n1. 体检前一天清淡饮食，晚上10点后禁食禁水；\n"
                        "2. 体检当天请携带身份证原件；\n3. 女士请避开生理期。\n\n安康体检中心", "health", "安康体检中心", kind="checkup_booking"))
        age = 40 + (y - 2024)
        rows = lin_rows(y)
        text = health_report_text("林舟", "男", age, exam, rows, findings[y], advice[y], f"AK{exam:%Y%m%d}0{rng.randint(100, 999)}")
        body = (f"尊敬的林舟先生：\n\n您于{exam:%Y年%m月%d日}在安康体检中心（朝阳门店）完成的{y}年度健康体检报告已出具，请查阅附件PDF（打开密码为手机号后四位）。\n\n"
                "本次体检主要异常提示：\n" + "\n".join("· " + f for f in findings[y]) + "\n\n总检建议：\n" + "\n".join("· " + a for a in advice[y]) +
                "\n\n如需报告解读，可预约健康管理师一对一咨询（电话或到店）。\n\n安康体检中心 健康管理部")
        out.append(mail(at(rep, 16, rng.randint(0, 59)), ak, [ME_P], f"【安康体检中心】您的{y}年度体检报告已出具（林舟）", body, "health", "安康体检中心",
                        [att(f"林舟_{y}年度体检报告.pdf", "pdf", text, rng, size=rng.randint(1_100_000, 2_600_000))],
                        nid={2024: "q2.r2024", 2025: "q2.r2025", 2026: "q2.r2026"}[y], kind="checkup_report", people=["林舟"]))
    # 苏晴 2026 (distractor) and 2025 (older)
    su_rows = [("身高", "163.0", "cm", "-", ""), ("体重", "54.6", "kg", "-", ""), ("体重指数BMI", "20.5", "kg/m²", "18.5-23.9", ""), ("血压", "108/70", "mmHg", "<140/90", ""),
               ("空腹血糖", "4.9", "mmol/L", "3.9-6.1", ""), ("总胆固醇", "4.52", "mmol/L", "<5.18", ""), ("低密度脂蛋白胆固醇(LDL-C)", "2.61", "mmol/L", "<3.37", ""),
               ("尿酸", "276", "μmol/L", "155-357", ""), ("血红蛋白", "112", "g/L", "115-150", "↓"), ("血清铁蛋白", "11.2", "ng/mL", "15-150", "↓"),
               ("甲状腺B超", "甲状腺右叶结节（TI-RADS 3类，约6×4mm）", "", "", ""), ("乳腺B超", "双侧乳腺增生", "", "", "")]
    sf = ["轻度贫血（血红蛋白112 g/L↓，铁蛋白偏低），考虑缺铁性。", "甲状腺右叶结节，TI-RADS 3类。", "双侧乳腺增生。"]
    sa = ["适当增加红肉、动物肝脏等富铁食物，必要时在医生指导下补铁，2个月后复查血常规。", "甲状腺结节建议6个月后复查甲状腺B超。", "乳腺增生建议每年复查乳腺B超。"]
    text = health_report_text("苏晴", "女", 40, dt.date(2026, 4, 10), su_rows, sf, sa, "AK202604100512")
    out.append(mail(at(dt.date(2026, 4, 21), 9, 12), ak, [ME_P], "【安康体检中心】您为家属预约的体检报告已出具（苏晴）",
                    "尊敬的林舟先生：\n\n您为家属苏晴女士预约的2026年度体检（2026年4月10日，家属套餐）报告已出具，请查阅附件或转交本人。\n\n主要异常提示：\n"
                    + "\n".join("· " + f for f in sf) + "\n\n总检建议：\n" + "\n".join("· " + a for a in sa) + "\n\n安康体检中心 健康管理部",
                    "health", "安康体检中心", [att("苏晴_2026年度体检报告.pdf", "pdf", text, rng, size=1_480_000)], nid="q2.su2026", kind="checkup_report",
                    people=["苏晴"]))
    out.append(mail(at(dt.date(2026, 7, 20), 10, 5), ak, [ME_P], "【安康体检中心】复查提醒：建议复查血脂、尿酸、血糖",
                    "尊敬的林舟先生：\n\n距您2026年4月10日的年度体检已满3个月。根据总检建议，您需要复查血脂、尿酸及空腹血糖。"
                    "复查套餐（空腹抽血，约30分钟）价格¥260，可在安康App预约朝阳门店任意工作日早上7:30-9:30。\n\n安康体检中心 健康管理部", "health",
                    "安康体检中心", kind="recheck", nid="q2.recheck"))
    return out


# ================================================================== shopping / express / food / ride
GOODS = [
    ("家用", ["抽纸 24包整箱", "洗衣凝珠 3盒装", "厨房湿巾", "保鲜袋 大中小组合", "不锈钢锅铲套装", "空气净化器滤芯", "电动牙刷刷头 8支", "收纳箱 3只装", "LED护眼台灯", "加湿器"]),
    ("食品", ["进口牛奶 24盒", "坚果礼盒", "东北大米 10kg", "有机鸡蛋 30枚", "挂耳咖啡 50袋", "燕麦片 1kg", "冷冻虾仁 1kg", "橄榄油 1L", "茶叶礼盒", "儿童奶酪棒"]),
    ("儿童", ["儿童科普绘本套装", "乐高式拼插积木", "四年级数学同步练习", "儿童保温杯", "跳绳+计数器", "儿童羽毛球拍", "彩铅 72色", "儿童护眼台灯", "编程机器人套件", "儿童雨衣"]),
    ("数码", ["蓝牙耳机", "手机快充充电器 65W", "移动硬盘 2TB", "无线鼠标", "机械键盘", "Type-C扩展坞", "智能手环", "车载手机支架", "降噪耳机", "平板保护套"]),
    ("服饰", ["男士羽绒服", "运动鞋", "商务衬衫 2件装", "羊毛围巾", "儿童冬季外套", "跑步袜 6双", "女士针织开衫", "保暖内衣套装"]),
    ("图书", ["《运筹学导论》", "《供应链管理》", "《数据密集型应用系统设计》", "《人类简史》", "《三体》全集", "《非暴力沟通》", "《高效能人士的七个习惯》"]),
]


def shopping_mail(rng, per_month=(72, 92), forced=()) -> list[dict]:
    out = []
    shop = svc("云购商城")
    exp = svc("极速快递")
    month = dt.date(2024, 1, 1)
    orders = []
    while month <= END:
        nxt = dt.date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        for _ in range(rng.randint(*per_month)):
            d = rand_day(rng, month, min(nxt - dt.timedelta(days=1), END))
            cat, items = rng.choice(GOODS)
            k = rng.choice([1, 1, 1, 2, 3])
            picked = rng.sample(items, min(k, len(items)))
            price = [round(rng.uniform(19, 399) if cat != "数码" else rng.uniform(59, 899), 0) - 0.1 for _ in picked]
            orders.append((d, picked, price, None, None))
        month = nxt
    for f in forced:
        orders.append(f)
    for d, picked, price, oid, nid in orders:
        oid = oid or f"YG{d:%y%m%d}{rng.randint(10_000_000, 99_999_999)}"
        total = round(sum(price), 2)
        h, mi = leisure_time(rng)
        t0 = at(d, h, mi)
        if t0 > AS_OF:
            continue
        tid = f"yg-{oid}"
        item_lines = "\n".join(f"· {n}  ×1  {money(p)}" for n, p in zip(picked, price))
        addr = "北京市朝阳区望京街道 嘉禾园小区7号楼2单元1102  林舟 138****5521"
        out.append(mail(t0, shop, [ME_P], f"【云购商城】订单提交成功：{picked[0]}{'等' + str(len(picked)) + '件商品' if len(picked) > 1 else ''}",
                        f"亲爱的林舟：\n\n您的订单已支付成功，我们会尽快为您发货。\n\n订单号：{oid}\n下单时间：{t0:%Y-%m-%d %H:%M}\n{item_lines}\n"
                        f"实付金额：{money(total)}（星辰银行信用卡 尾号6411）\n收货地址：{addr}\n\n可在「我的订单」中查看物流进度。\n云购商城",
                        "notification", "云购商城", kind="order", thread=tid, nid=nid))
        exp_no = f"JS{rng.randint(10**11, 10**12 - 1)}"
        t1 = t0 + dt.timedelta(hours=rng.randint(3, 30))
        if t1 <= AS_OF:
            out.append(mail(t1, shop, [ME_P], f"【云购商城】您的订单已发货（{oid[-6:]}）",
                            f"亲爱的林舟：\n\n您的订单{oid}已由极速快递发出，运单号{exp_no}。预计1-3天送达。\n\n云购商城", "notification", "云购商城",
                            kind="ship", thread=tid))
        t2 = t1 + dt.timedelta(hours=rng.randint(16, 60))
        if t2 <= AS_OF:
            code = f"{rng.randint(1, 9)}-{rng.randint(1, 30)}-{rng.randint(1000, 9999)}"
            if rng.random() < 0.55:
                out.append(mail(t2, exp, [ME_P], f"【极速快递】您的快递已放入嘉禾园驿站，取件码{code}",
                                f"您好，您的快递（运单号{exp_no}，云购商城）已到达嘉禾园快递驿站（7号楼底商），取件码{code}，请于3天内凭取件码取件。\n营业时间 8:00-21:00。\n极速快递",
                                "notification", "极速快递", kind="express", thread=f"js-{exp_no}"))
            else:
                out.append(mail(t2, exp, [ME_P], f"【极速快递】您的快递已签收（{exp_no}）",
                                f"您好，您的快递（运单号{exp_no}）已于{t2:%m月%d日%H:%M}由本人/家人签收，签收地址：嘉禾园小区7号楼。感谢使用极速快递。",
                                "notification", "极速快递", kind="express", thread=f"js-{exp_no}"))
        if rng.random() < 0.35:
            t3 = t2 + dt.timedelta(days=rng.randint(1, 5))
            if t3 <= AS_OF:
                out.append(mail(t3, shop, [ME_P], f"【云购商城】电子发票已开具（订单{oid[-6:]}）",
                                f"亲爱的林舟：\n订单{oid}的电子普通发票已开具，发票金额{money(total)}，抬头：个人。可在附件下载。", "notification", "云购商城",
                                [att(f"电子发票_{oid}.pdf", "pdf", f"电子普通发票\n购买方：个人（林舟）\n销售方：云购商城（北京）电子商务有限公司\n"
                                     + "\n".join(f"{n} 1 {p:.2f}" for n, p in zip(picked, price)) + f"\n价税合计：{money(total)}", rng, size=rng.randint(60_000, 120_000))],
                                kind="invoice", thread=tid))
    return out


def daily_life_mail(rng) -> list[dict]:
    """Food delivery, ride receipts, video membership, telecom/utility bills, property notices, app security."""
    out = []
    food, ride, video = svc("饭点外卖"), svc("速达出行"), svc("青柚视频")
    tel, gas, power, prop = svc("畅联通信"), svc("京华燃气"), svc("城市电力服务"), svc("嘉禾物业")
    pan = svc("云盘")
    shops = ["湘味小馆", "老北京炸酱面", "轻食沙拉坊", "麻辣香锅", "粥铺", "黄焖鸡米饭", "日式便当", "牛肉面馆", "披萨小厨", "饺子馆"]
    for d in days():
        r = rng.random()
        if r < 0.65:
            h, mi = rng.choice([(12, rng.randint(0, 40)), (18, rng.randint(20, 59)), (21, rng.randint(0, 50))])
            s = rng.choice(shops)
            amt = round(rng.uniform(28, 135), 1)
            out.append(mail(at(d, h, mi), food, [ME_P], f"【饭点外卖】订单已送达：{s}",
                            f"您在「{s}」的订单已送达，实付{money(amt)}。\n送达地址：{'云帆大厦6层前台' if is_workday(d) and h < 15 else '嘉禾园小区7号楼'}\n感谢您的支持，期待您的评价！\n饭点外卖",
                            "notification", "饭点外卖", kind="food"))
        for _ride in range(rng.choice([0, 1, 1, 1, 2])):
            h, mi = work_time(rng) if is_workday(d) else leisure_time(rng)
            km = round(rng.uniform(3, 32), 1)
            fare = round(12 + km * 2.3 + rng.uniform(0, 8), 2)
            a, b = rng.choice([("望京嘉禾园", "云帆大厦"), ("云帆大厦", "望京嘉禾园"), ("北京大兴国际机场", "望京嘉禾园"), ("望京嘉禾园", "北京大兴国际机场"),
                               ("云帆大厦", "北京南站"), ("望京嘉禾园", "育才实验小学"), ("云帆大厦", "国贸"), ("望京嘉禾园", "朝阳公园")])
            out.append(mail(at(d, h, mi), ride, [ME_P], f"【速达出行】行程电子发票及收据（{d.month}月{d.day}日 {fare:.2f}元）",
                            f"尊敬的乘客：\n您{d:%Y-%m-%d} {h:02d}:{mi:02d}的行程已完成。\n起点：{a}\n终点：{b}\n里程：{km}公里  时长：{int(km * 2.6 + 6)}分钟\n"
                            f"实付：{money(fare)}（星辰银行信用卡 尾号6411）\n电子发票可在「我的-发票」申请开具。\n速达出行", "notification", "速达出行", kind="ride",
                            attachments=[att(f"速达出行电子行程单_{d:%Y%m%d}.pdf", "pdf",
                                             f"速达出行 电子行程单\n申请日期：{d:%Y-%m-%d}  行程时间：{d:%Y-%m-%d}至{d:%Y-%m-%d}  共1笔行程，合计{fare:.2f}元\n"
                                             f"序号 | 服务商 | 车型 | 上车时间 | 城市 | 起点 | 终点 | 里程(公里) | 金额(元)\n1 | 速达出行 | 快车 | {d:%m-%d} {h:02d}:{mi:02d} | 北京 | {a} | {b} | {km} | {fare:.2f}\n"
                                             "本行程单仅作为报销凭证使用，发票请另行申请。", rng, size=rng.randint(40_000, 90_000))]
                            if (is_workday(d) and rng.random() < 0.35) else None))
        for _deb in range(rng.choice([0, 1, 1, 2, 2])):
            xc = svc("星辰银行")
            kind_, payee, lo_, hi_ = rng.choice([("快捷支付", "云购商城", 20, 600), ("快捷支付", "饭点外卖", 18, 120), ("转账支出", "苏晴", 500, 5000),
                                                 ("快捷支付", "社区菜店", 15, 180), ("自动扣款", "畅联通信", 128, 128), ("转账收入", "苏晴", 200, 2000),
                                                 ("快捷支付", "速达出行", 12, 80), ("消费", "惠民超市（望京店）", 30, 420), ("理财赎回", "星辰稳盈30天", 5000, 50000)])
            amt = round(rng.uniform(lo_, hi_), 2)
            if payee == "苏晴" and kind_ == "转账支出" and d.day == 15:
                payee, amt = "林建华", 2000.00
            h, mi = leisure_time(rng)
            out.append(mail(at(d, h, mi), xc, [ME_P], f"【星辰银行】账户变动提醒：尾号3390{'收入' if '收入' in kind_ or '赎回' in kind_ else '支出'}{money(amt)}",
                            f"尊敬的林舟先生：\n您尾号3390的储蓄卡于{d.month}月{d.day}日{h:02d}:{mi:02d}发生{kind_}（{payee}）{money(amt)}，余额请登录手机银行查询。"
                            "\n如非本人操作，请立即致电客服。\n星辰银行 零售银行部", "finance", "星辰银行", kind="debit"))
        for _inv in range(rng.choice([0, 0, 1, 1, 2])):
            seller, item, lo_, hi_ = rng.choice([("饭点外卖平台商户", "餐饮服务", 30, 400), ("速达出行", "客运服务", 15, 160), ("加油站（京承高速店）", "汽油", 250, 480),
                                                 ("云购商城", "日用品", 40, 900), ("湘味小馆", "餐饮服务", 120, 900), ("畅联通信", "通信服务", 128, 216),
                                                 ("书香书城", "图书", 40, 300), ("停车场管理公司", "停车服务", 10, 80)])
            amt = round(rng.uniform(lo_, hi_), 2)
            no = f"{d:%y}11{rng.randint(10**15, 10**16 - 1)}"
            h, mi = leisure_time(rng)
            out.append(mail(at(d, h, mi), {"name": "电子发票服务平台", "email": "fapiao@e-invoice-service.cn", "org": "电子发票服务平台"}, [ME_P],
                            f"您收到一张电子发票【{seller}】{money(amt)}",
                            f"尊敬的用户：\n{seller}为您开具了一张电子发票，发票号码{no}，开票金额{money(amt)}，开票日期{d:%Y-%m-%d}。发票文件见附件，也可在发票夹中查看。"
                            "\n本邮件由电子发票服务平台自动发送，请勿回复。", "notification", "电子发票服务平台",
                            attachments=[att(f"电子发票_{no}.pdf", "pdf",
                                             f"电子发票（普通发票）\n发票号码：{no}\n开票日期：{d:%Y年%m月%d日}\n购买方：{'个人（林舟）' if rng.random() < 0.6 else '云帆智能科技有限公司 统一社会信用代码 91110108MA********'}\n"
                                             f"销售方：{seller}\n项目名称 | 规格 | 数量 | 金额 | 税率 | 税额\n*{item}* | - | 1 | {amt / 1.06:.2f} | 6% | {amt - amt / 1.06:.2f}\n"
                                             f"价税合计（小写）：{money(amt)}\n开票人：系统", rng, size=rng.randint(40_000, 110_000))], kind="einvoice"))
        if rng.random() < 0.8:
            shop_ = rng.choice(["书香书城", "乐思英语教材商城", "鲜果时光", "社区团购", "动力健身商城", "母婴精选店", "茶叶专营店", "户外装备店"])
            exp = svc("极速快递")
            no = f"JS{rng.randint(10**11, 10**12 - 1)}"
            h, mi = leisure_time(rng)
            if rng.random() < 0.5:
                code = f"{rng.randint(1, 9)}-{rng.randint(1, 30)}-{rng.randint(1000, 9999)}"
                out.append(mail(at(d, h, mi), exp, [ME_P], f"【极速快递】您的快递已放入嘉禾园驿站，取件码{code}",
                                f"您好，您的快递（运单号{no}，寄件方：{shop_}）已到达嘉禾园快递驿站（7号楼底商），取件码{code}，请于3天内取件。\n营业时间 8:00-21:00。\n极速快递",
                                "notification", "极速快递", kind="express"))
            else:
                out.append(mail(at(d, h, mi), exp, [ME_P], f"【极速快递】您有一个包裹正在派送（{no[-6:]}）",
                                f"您好，您来自「{shop_}」的包裹（运单号{no}）正在派送中，快递员王师傅（电话 156****{rng.randint(1000, 9999)}）预计今日送达。如不在家可放驿站。\n极速快递",
                                "notification", "极速快递", kind="express"))
        if rng.random() < 0.35:
            item = rng.choice(GOODS)[1]
            it = rng.choice(item)
            out.append(mail(at(d, rng.randint(9, 22), rng.randint(0, 59)), svc("云购商城"), [ME_P], rng.choice([f"【云购商城】您关注的「{it}」降价了", f"【云购商城】「{it}」到货提醒"]),
                            f"亲爱的林舟：您关注/加入购物车的「{it}」{rng.choice(['已降价' + str(rng.randint(5, 30)) + '%', '已到货，库存有限'])}，快去看看吧。\n云购商城",
                            "notification", "云购商城", kind="watch"))
        if is_workday(d) and rng.random() < 0.3:
            fee = rng.choice([6, 8, 10, 15, 20, 24, 30])
            out.append(mail(at(d, rng.randint(17, 21), rng.randint(0, 59)), {"name": "智慧停车", "email": "pay@zhihui-tingche.cn", "org": "智慧停车"}, [ME_P],
                            f"停车缴费成功 {money(fee)}", f"京N·8K2**于{rng.choice(['云帆大厦地下车库', '望京商务中心P2', '国贸停车场', '朝阳公园停车场'])}停车，缴费{money(fee)}（无感支付，星辰银行信用卡）。",
                            "notification", "智慧停车", kind="parking"))
        if d.weekday() in (5, 6) and rng.random() < 0.35:
            fee = round(rng.uniform(15, 165), 2)
            out.append(mail(at(d, rng.randint(17, 22), rng.randint(0, 59)), {"name": "ETC出行服务", "email": "notice@etc-chuxing.cn", "org": "ETC出行服务"}, [ME_P],
                            f"【ETC】通行扣费通知 {money(fee)}",
                            f"尊敬的车主：您的车辆京N·8K2**于{d.month}月{d.day}日通行{rng.choice(['京承高速', '京藏高速', '机场高速', '京哈高速', '大兴机场高速'])}，扣费{money(fee)}，扣款账户：星辰银行信用卡（尾号6411）。",
                            "notification", "ETC出行服务", kind="etc"))
        if rng.random() < 0.3:
            show = rng.choice(["《山海之间》", "《长河落日》", "《小小科学家》", "《深海档案》", "《城市之光》", "《星辰大海》"])
            out.append(mail(at(d, rng.randint(19, 22), rng.randint(0, 59)), video, [ME_P], f"【青柚视频】您追的{show}更新了",
                            f"您追的{show}已更新至第{rng.randint(5, 40)}集，会员可抢先看{rng.choice([2, 4, 6])}集。打开青柚视频App继续观看。", "notification", "青柚视频", kind="app"))
        if d.day == 3:
            amt = round(rng.uniform(128, 216), 2)
            out.append(mail(at(d, 10, rng.randint(0, 59)), tel, [ME_P], f"【畅联通信】{(d - dt.timedelta(days=5)).month}月账单：{money(amt)}",
                            f"尊敬的客户：您手机号138****5521上月话费账单{money(amt)}（套餐费128.00，流量包{amt - 128:.2f}），将从绑定的星辰银行卡自动扣款。\n畅联通信",
                            "notification", "畅联通信", kind="telecom"))
        if d.day == 8:
            kwh = rng.randint(260, 620)
            out.append(mail(at(d, 9, rng.randint(0, 59)), power, [ME_P], f"电费账单：{d.year}年{(d - dt.timedelta(days=10)).month}月用电{kwh}度",
                            f"户号：01****8927（嘉禾园7-2-1102）\n上月用电量：{kwh}度  电费：{money(kwh * 0.4883)}\n缴费截止：{d.year}年{d.month}月25日\n城市电力服务",
                            "notification", "城市电力服务", kind="utility"))
        if d.day == 15 and d.month % 2 == 0:
            m3 = rng.randint(18, 95)
            out.append(mail(at(d, 9, rng.randint(0, 59)), gas, [ME_P], f"【京华燃气】燃气费账单 {m3}立方米",
                            f"尊敬的用户：您的燃气户号（嘉禾园7-2-1102）近两月用气{m3}立方米，金额{money(m3 * 2.63)}，请于本月底前缴纳。\n京华燃气", "notification", "京华燃气",
                            kind="utility"))
        if rng.random() < 0.045:
            topic = rng.choice([("停水通知", "因二次供水设备检修，7号楼将于{d2}9:00-15:00停水，请提前储水。"),
                                ("电梯维保", "7号楼2单元电梯将于{d2}进行年度维保，期间暂停使用约3小时。"),
                                ("消防演练", "小区将于{d2}上午10:00进行消防演练，届时会有警报声，请勿惊慌。"),
                                ("物业费缴纳", "2026年度物业费（4.2元/平方米·月）已开始收缴，可在物业App或前台缴纳。"),
                                ("垃圾分类", "本月起小区实行定时定点投放，厨余垃圾投放时间为7:00-9:00、18:00-20:00。"),
                                ("供暖试水", "冬季供暖将于{d2}开始试水试压，请家中留人并检查暖气阀门。"),
                                ("车位管理", "地下车库将进行地面翻新，B区车位{d2}起暂停使用一周，请临时停放至C区。")])
            d2 = cn_date(d + dt.timedelta(days=rng.randint(2, 6)))
            out.append(mail(at(d, rng.randint(9, 17), rng.randint(0, 59)), prop, [ME_P], f"【嘉禾物业】{topic[0]}",
                            f"尊敬的业主：\n\n{topic[1].format(d2=d2)}\n给您带来的不便敬请谅解。\n\n嘉禾物业服务中心\n{cn_date(d)}", "notification", "嘉禾物业",
                            kind="property"))
        if d.day == 20 and d.month in (1, 4, 7, 10):
            out.append(mail(at(d, 8, rng.randint(0, 59)), video, [ME_P], "【青柚视频】会员自动续费成功",
                            "您的青柚视频连续包季会员已自动续费成功，扣款¥68.00，有效期顺延3个月。如需取消请在App「我的-会员」中操作。", "notification", "青柚视频",
                            kind="membership"))
        for _otp in range(rng.choice([0, 1, 1, 1, 2, 2])):
            src = rng.choice([("云购商城", "service@yungou-mall.com", "登录验证码"), ("星辰银行", "creditcard@xingchen-bank.com", "动态验证码"),
                              ("蓝鲸航空", "noreply@bluewhale-air.com", "会员登录验证码"), ("鲸云盘", "no-reply@jingyunpan.com", "登录验证码"),
                              ("速达出行", "receipt@suda-go.com", "验证码"), ("畅联通信", "bill@changlian-tel.com", "账户安全验证码")])
            code = rng.randint(100000, 999999)
            out.append(mail(at(d, *leisure_time(rng)), {"name": src[0], "email": src[1], "org": src[0]}, [ME_P], f"【{src[0]}】{src[2]}：{code}",
                            f"您的{src[2]}为 {code}，5分钟内有效。如非本人操作，请忽略本邮件并注意账户安全。\n{src[0]}", "notification", src[0], kind="otp"))
        if rng.random() < 0.15:
            out.append(mail(at(d, *leisure_time(rng)), pan, [ME_P], rng.choice(["【鲸云盘】您的空间已使用90%", "【鲸云盘】相册自动备份完成", "【鲸云盘】新设备登录提醒"]),
                            rng.choice(["您的鲸云盘空间已使用1.8TB/2TB，建议清理或升级空间。", f"本次共备份照片和视频{rng.randint(30, 400)}个。",
                                        "您的账号在新设备（Mac）上登录，如非本人操作请及时修改密码。"]), "notification", "鲸云盘", kind="app"))
    return [x for x in out if x["date"] <= AS_OF.isoformat()]


# ================================================================== internal systems
SYS = {s["name"]: {"name": s["name"], "email": s["email"], "org": B.COMPANY} for s in B.SYSTEMS}
TEAM = ["gucheng", "fangyu", "yezihan", "qinchuan", "tangyue", "songjia", "qiuyang", "baiyang", "yangyu"]


def system_mail(rng) -> list[dict]:
    out = []
    oa, cal, it, pm, trv, exp, hr = (SYS[k] for k in ("云帆OA", "云帆日历", "IT服务台", "项目管理平台", "云帆差旅平台", "费用报销系统", "云帆人力系统"))
    rooms = ["6层 星河厅", "6层 远航厅", "5层 启明厅", "7层 大会议室", "6层 小会议室602", "线上（云帆会议）"]
    meetings = [("解决方案部周会", "部门周会", ["gucheng", "fangyu", "yezihan", "qinchuan", "tangyue", "songjia", "qiuyang", "baiyang", "yangyu"]),
                ("售前商机过会", "商机评审", ["gucheng", "wanglei", "zhongling", "shenhang", "guowen"]),
                ("技术方案评审", "评审", ["chenmo", "liangfeng", "gucheng", "fangyu"]),
                ("1:1沟通", "1on1", None), ("交付-售前交接会", "交接", ["dumingyuan", "zhouheng", "fengtao", "shaomin"]),
                ("月度经营分析会", "经营会", ["hanzhendong", "zhaoyiming", "guowen", "fanzhiqiang", "dumingyuan"]),
                ("产品需求对齐", "需求", ["ouyangchen", "xiabing", "yuelin", "chenmo"]), ("招聘面试", "面试", ["zhangrui", "gucheng"])]
    for d in WORKDAYS:
        # calendar invites
        for _ in range(rng.choice([0, 1, 2, 3, 3, 4, 5, 6])):
            title, kind, att_keys = rng.choice(meetings)
            if title == "解决方案部周会" and d.weekday() != 0:
                continue
            if att_keys is None:
                k = rng.choice(TEAM)
                att_keys = [k]
                title = f"1:1沟通（林舟/{PEOPLE[k]['name']}）"
            if title == "招聘面试":
                title = f"面试：{rng.choice(['解决方案经理', '售前工程师', '行业方案专家'])}候选人 {rng.choice('王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗')}{rng.choice(['先生', '女士'])}"
            md = next_workday(d, rng.choice([1, 1, 2, 3]))
            h = rng.choice([9, 10, 10, 11, 14, 14, 15, 16, 17])
            room = rng.choice(rooms)
            org_key = rng.choice(att_keys)
            names = "、".join(PEOPLE[k]["name"] for k in att_keys)
            out.append(mail(at(d, *work_time(rng)), cal, [ME_W], f"会议邀请：{title} @ {md.month}月{md.day}日（{weekday_cn(md)}） {h:02d}:00-{h + 1:02d}:00",
                            f"{PEOPLE[org_key]['name']} 邀请您参加会议\n\n主题：{title}\n时间：{md:%Y-%m-%d}（{weekday_cn(md)}） {h:02d}:00-{h + 1:02d}:00\n地点：{room}\n"
                            f"组织者：{PEOPLE[org_key]['name']}\n参会人：林舟、{names}\n\n[接受]  [暂定]  [拒绝]\n\n此邮件由云帆日历自动发送。",
                            "system", None, kind="calendar", people=[PEOPLE[k]["name"] for k in att_keys]))
        # OA approvals
        for _ in range(rng.choice([0, 1, 2, 2, 3, 3, 4, 5, 6])):
            k = rng.choice(TEAM)
            typ = rng.choices(["费用报销", "出差申请", "请假申请", "采购申请", "用印申请", "合同审批", "加班申请"], weights=[8, 5, 4, 2, 2, 2, 2])[0]
            no = f"{ {'费用报销': 'BX', '出差申请': 'CC', '请假申请': 'QJ', '采购申请': 'CG', '用印申请': 'YY', '合同审批': 'HT', '加班申请': 'JB'}[typ]}{d:%Y%m%d}{rng.randint(100, 999)}"
            detail = {
                "费用报销": f"报销金额：{money(round(rng.uniform(260, 8600), 2))}\n费用类型：{rng.choice(['差旅费', '业务招待费', '交通费', '办公用品', '培训费'])}\n关联项目：{rng.choice([p['name'] for p in B.PROJECTS if p.get('org')])}",
                "出差申请": f"出差地点：{rng.choice(['上海', '苏州', '杭州', '西安', '天津', '郑州', '深圳', '武汉'])}\n出差时间：{next_workday(d, 3):%m月%d日}-{next_workday(d, 5):%m月%d日}\n事由：{rng.choice(['客户需求调研', '方案汇报', '项目验收', '投标述标', '现场演示'])}",
                "请假申请": f"请假类型：{rng.choice(['年假', '事假', '病假', '调休'])}\n请假时间：{next_workday(d, 2):%m月%d日} 共{rng.choice([0.5, 1, 1, 2, 3])}天\n工作交接人：{PEOPLE[rng.choice(TEAM)]['name']}",
                "采购申请": f"采购内容：{rng.choice(['演示用工控机2台', '投影仪1台', '行业数据报告', '外包标注服务', '软件授权（地图SDK）'])}\n预算金额：{money(round(rng.uniform(3000, 86000), -2))}",
                "用印申请": f"用印文件：{rng.choice(['投标文件', '保密协议', '授权委托书', '技术服务合同', '验收报告'])}\n印章类型：{rng.choice(['公章', '合同专用章'])}  份数：{rng.randint(1, 5)}",
                "合同审批": f"合同名称：{rng.choice(['技术服务合同', '软件许可合同', '外包服务合同', '保密协议'])}\n合同金额：{money(round(rng.uniform(50000, 900000), -3))}",
                "加班申请": f"加班日期：{next_workday(d, 1):%m月%d日}\n加班时长：{rng.choice([2, 3, 4, 8])}小时\n事由：{rng.choice(['投标文件冲刺', '项目上线值守', '客户演示准备'])}",
            }[typ]
            oa_att = None
            if typ == "费用报销" and rng.random() < 0.45:
                rows = []
                tot = 0.0
                for _r in range(rng.randint(3, 9)):
                    item = rng.choice(["机票", "高铁票", "酒店住宿", "市内交通", "餐费补贴", "客户招待", "资料打印", "快递费"])
                    amt = round(rng.uniform(30, 1800), 2)
                    tot += amt
                    rows.append(f"{(d - dt.timedelta(days=rng.randint(3, 25))):%Y-%m-%d} | {item} | {amt:,.2f} | {rng.choice(['电子发票', '增值税专票', '行程单', '收据'])}")
                oa_att = [att(f"报销单_{no}.pdf", "pdf", f"云帆智能科技有限公司 费用报销单\n单号：{no}  报销人：{PEOPLE[k]['name']}  部门：解决方案部\n"
                              "日期 | 费用项目 | 金额(元) | 票据类型\n" + "\n".join(rows) + f"\n合计：{tot:,.2f}元\n审批流程：部门负责人（林舟）→ 财务审核（孟瑶）→ 财务经理（孙浩）", rng,
                              size=rng.randint(120_000, 900_000))]
            out.append(mail(at(d, *work_time(rng)), oa, [ME_W], f"【待审批】{PEOPLE[k]['name']}的{typ}（{no}）",
                            f"林舟，您好：\n\n{PEOPLE[k]['name']}提交了{typ}，需要您审批。\n\n单号：{no}\n申请人：{PEOPLE[k]['name']}（解决方案部）\n{detail}\n\n"
                            "请登录云帆OA或移动端处理。\n\n——云帆OA（系统邮件，请勿回复）", "system", None, kind="oa", people=[PEOPLE[k]["name"]],
                            attachments=oa_att))
        # PM system
        for _pm in range(rng.choice([0, 1, 1, 2, 2, 3])):
            proj = rng.choice([p for p in B.PROJECTS if p.get("org")])
            s, e = dt.date.fromisoformat(proj["start"]), dt.date.fromisoformat(proj["end"])
            if s <= d <= e and proj["name"] not in ("瀚海物流·华东智能调度平台（一期）",):
                ev = rng.choice(["里程碑即将到期", "风险登记", "周报已提交", "任务指派", "变更申请待评审", "工时填报提醒"])
                body = {
                    "里程碑即将到期": f"项目「{proj['name']}」的里程碑「{rng.choice(['需求确认', '方案评审', '系统联调', 'UAT测试', '上线', '初验'])}」将于{next_workday(d, 5):%m月%d日}到期，当前完成度{rng.randint(55, 95)}%。",
                    "风险登记": f"项目「{proj['name']}」新增风险：{rng.choice(['客户数据提供延迟', '关键人员请假', '接口文档不完整', '硬件到货延迟', '需求范围蔓延'])}（等级：{rng.choice(['中', '高', '低'])}）。",
                    "周报已提交": f"项目「{proj['name']}」第{rng.randint(3, 30)}周周报已由项目经理提交，请查阅。",
                    "任务指派": f"您被指派为项目「{proj['name']}」任务「{rng.choice(['方案评审', '报价复核', '客户汇报材料审核', '验收材料确认'])}」的审核人，截止{next_workday(d, 3):%m月%d日}。",
                    "变更申请待评审": f"项目「{proj['name']}」提交了需求变更申请（预计增加{rng.randint(5, 40)}人天），请参与评审。",
                    "工时填报提醒": "您本周的工时尚未填报，请于周五18:00前完成。",
                }[ev]
                out.append(mail(at(d, *work_time(rng)), pm, [ME_W], f"[项目管理平台] {ev}：{proj['name']}" if ev != "工时填报提醒" else "[项目管理平台] 工时填报提醒",
                                body + "\n\n登录项目管理平台查看详情。", "system", proj["org"], project=proj["name"], kind="pm"))
        # IT notices
        if rng.random() < 0.12:
            topic = rng.choice([("系统维护通知", "本周六22:00至周日06:00对邮件系统及OA进行例行维护，期间服务可能中断。"),
                                ("密码即将过期", "您的域账号密码将在7天后过期，请登录自助门户修改，新密码需包含大小写字母、数字和符号。"),
                                ("钓鱼邮件预警", "近期发现冒充财务部发送“工资调整通知”的钓鱼邮件，请勿点击链接或输入账号密码。"),
                                ("VPN客户端升级", "VPN客户端将升级至新版本，旧版本将于月底停止使用，请按指引升级。"),
                                ("终端安全检查", "您的笔记本未安装最新安全补丁，请于3个工作日内完成更新。"),
                                ("会议室系统上线", "新的会议室预订系统已上线，可在OA中预订并扫码签到。")])
            out.append(mail(at(d, *work_time(rng)), it, [ME_W], f"【IT服务台】{topic[0]}", f"各位同事：\n\n{topic[1]}\n如有问题请联系IT服务台（分机8000）。\n\n信息部", "system",
                            None, kind="it"))
        # HR system
        if rng.random() < 0.06:
            topic = rng.choice(["考勤异常提醒", "年假余额提醒", "培训课程报名", "员工满意度调研", "季度绩效考核启动"])
            out.append(mail(at(d, *work_time(rng)), hr, [ME_W], f"【人力系统】{topic}",
                            {"考勤异常提醒": f"您{d.month}月{max(1, d.day - 2)}日缺少下班打卡记录，请在3个工作日内提交补卡申请。",
                             "年假余额提醒": f"您当前剩余年假{rng.randint(3, 12)}天，请合理安排休假。",
                             "培训课程报名": f"《{rng.choice(['大模型应用实践', '结构化思维与表达', '项目管理PMP精讲', '商务谈判技巧'])}》开放报名，名额有限。",
                             "员工满意度调研": "年度员工满意度调研已开始，请在本周内完成匿名问卷。",
                             "季度绩效考核启动": "本季度绩效考核已启动，请于两周内完成自评并与直属上级沟通。"}[topic] + "\n\n云帆人力系统", "system", None, kind="hr"))
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            k = rng.choice(TEAM + ["zhouheng", "fengtao", "shaomin", "chenmo", "wanglei", "zhongling", "xuekai"])
            doc = rng.choice(["方案", "报价测算", "周报汇总", "投标文件", "会议纪要", "需求清单", "实施计划", "验收材料", "客户案例", "培训材料"])
            proj = rng.choice([p["name"] for p in B.PROJECTS if p.get("org") and p["name"] not in ("瀚海物流·华东智能调度平台（一期）",)] + ["解决方案部"])
            out.append(mail(at(d, *work_time(rng)), {"name": "云帆文档", "email": "docs@yunfan-tech.cn", "org": B.COMPANY}, [ME_W],
                            f"{PEOPLE[k]['name']} 与您共享了文档「{proj}_{doc}」",
                            f"{PEOPLE[k]['name']}（{PEOPLE[k].get('title', '')}）与您共享了文档：\n\n《{proj}_{doc}_v{rng.randint(1, 5)}.{rng.choice(['docx', 'xlsx', 'pptx'])}》\n权限：可编辑\n"
                            f"留言：{rng.choice(['林总帮忙看一下', '请审阅', '已更新，见修订记录', '供参考', '周五前请给意见'])}\n\n在云帆文档中打开。", "system", None, kind="docs",
                            people=[PEOPLE[k]["name"]]))
    return [x for x in out if x["date"] <= AS_OF.isoformat()]


def expense_mail(rng, trips: list[dict]) -> list[dict]:
    out = []
    trv, exp = SYS["云帆差旅平台"], SYS["费用报销系统"]
    for tr in trips:
        if tr.get("family"):
            continue
        ap = next_workday(tr["booked"], -1) if tr["booked"] > START else tr["booked"]
        t = at(ap, *work_time(rng))
        if t <= AS_OF:
            out.append(mail(t, trv, [ME_W], f"【差旅平台】出差申请已审批：{tr['city']} {tr['go']:%m/%d}-{tr['back']:%m/%d}",
                            f"林舟，您好：\n您的出差申请（{tr['city']}，{tr['go']:%Y-%m-%d}至{tr['back']:%Y-%m-%d}，事由：{tr.get('purpose', '客户拜访')}）已由赵一鸣审批通过。"
                            f"可在差旅平台预订机票和酒店（企业月结）。\n\n云帆差旅平台", "system", tr.get("org"), kind="travel_approval", nid=tr.get("nid_approval")))
        rd = next_workday(tr["back"], rng.randint(2, 8))
        t2 = at(rd, *work_time(rng))
        if t2 <= AS_OF:
            amt = round(rng.uniform(380, 2600), 2)
            out.append(mail(t2, exp, [ME_W], f"【报销系统】您的差旅报销单已提交（{tr['city']}，{money(amt)}）",
                            f"报销单号：BX{rd:%Y%m%d}{rng.randint(100, 999)}\n出差：{tr['city']} {tr['go']:%m/%d}-{tr['back']:%m/%d}\n报销金额：{money(amt)}"
                            f"（交通、餐补、市内打车）\n当前节点：财务审核（孟瑶）\n\n费用报销系统", "system", tr.get("org"), kind="expense"))
    return out


# ================================================================== insurance & clinics
POLICY_TERMS = ("【重要条款摘要】\n一、保险责任：被保险人在等待期（90日）后初次确诊合同约定的重大疾病，按基本保险金额给付重大疾病保险金，本项责任终止；"
                "确诊合同约定的轻症疾病，按基本保险金额的30%给付轻症疾病保险金，最多给付3次。\n二、责任免除：投保人对被保险人的故意杀害、故意伤害；被保险人故意自伤、"
                "故意犯罪或抗拒依法采取的刑事强制措施；被保险人服用、吸食或注射毒品；酒后驾驶、无合法有效驾驶证驾驶或驾驶无有效行驶证的机动车；战争、军事冲突、暴乱或武装叛乱；"
                "核爆炸、核辐射或核污染；遗传性疾病、先天性畸形、变形或染色体异常（合同另有约定除外）。\n三、宽限期：分期支付保险费的，自保险费约定交纳日的次日零时起60日为宽限期，"
                "宽限期内发生的保险事故，本公司仍承担保险责任，但在给付保险金时会扣减欠交的保险费。\n四、合同效力中止与恢复：宽限期结束仍未交纳保险费的，合同效力中止；"
                "效力中止之日起二年内可申请恢复合同效力。\n五、犹豫期：自签收保险合同次日起15日为犹豫期，犹豫期内解除合同的，本公司无息退还已交保险费。\n"
                "六、如实告知：订立合同时，本公司就被保险人的有关情况提出询问的，投保人应当如实告知。\n（以上为条款摘要，具体以保险合同条款为准。）")


def insurance_mail(rng) -> list[dict]:
    out = []
    kn = svc("康宁人寿")
    pols = [("重大疾病保险", "KN-ZJ-2019****8812", "林舟", 8640.00, 500000, (3, 15)), ("家庭医疗保险", "KN-YL-2023****0457", "林舟、苏晴、林小禾", 2316.00, 3000000, (9, 1)),
            ("少儿重大疾病保险", "KN-SE-2021****3306", "林小禾", 2980.00, 300000, (6, 8)), ("机动车商业保险", "KN-CX-2024****7719", "林舟（京N·8K2**）", 4870.00, 1000000, (6, 20))]
    for y in (2024, 2025, 2026):
        for name, no, ins, prem, amt, (mo, dd) in pols:
            due = dt.date(y, mo, dd)
            rem = due - dt.timedelta(days=30)
            if at(rem, 10, 0) <= AS_OF:
                out.append(mail(at(rem, 10, rng.randint(0, 59)), kn, [ME_P], f"【康宁人寿】续期缴费提醒：{name}（保单号{no}）",
                                f"尊敬的林舟先生：\n\n您投保的《{name}》（保单号{no}，被保险人：{ins}）将于{due:%Y年%m月%d日}进入第{y - 2018 if '重大' in name and '少儿' not in name else y - 2020}个保单年度，"
                                f"应缴保费{money(prem)}，届时将从您授权的星辰银行储蓄卡（尾号3390）自动划扣，请确保余额充足。\n\n如需变更缴费账户，请登录康宁人寿App办理。\n\n康宁人寿保险股份有限公司 客户服务中心",
                                "finance", "康宁人寿", kind="insurance"))
            if at(due, 9, 0) <= AS_OF:
                rep = (f"康宁人寿 保单年度报告（{y}）\n保单号：{no}\n险种：{name}\n投保人：林舟    被保险人：{ins}\n基本保险金额：{money(amt)}\n本期保费：{money(prem)}（已于{due:%Y-%m-%d}缴纳）\n"
                       f"缴费方式：年交    缴费账户：星辰银行储蓄卡（尾号3390）\n保单状态：有效\n\n" + POLICY_TERMS)
                out.append(mail(at(due, 9, rng.randint(0, 59)), kn, [ME_P], f"【康宁人寿】保费缴纳成功及保单年度报告（{name}）",
                                f"尊敬的林舟先生：\n\n您的《{name}》{y}年度保费{money(prem)}已缴纳成功，保单继续有效。附件为本年度保单报告，请妥善保存。\n\n康宁人寿保险股份有限公司",
                                "finance", "康宁人寿", [att(f"保单年度报告_{no.replace('*', 'x')}_{y}.pdf", "pdf", rep, rng)], kind="insurance"))
    claims = [(dt.date(2025, 6, 18), "苏晴", "门诊（急性肠胃炎）", 1326.50), (dt.date(2024, 11, 26), "林小禾", "门诊（支气管炎）", 865.20), (dt.date(2026, 3, 9), "林小禾", "门诊（流感）", 642.00)]
    for d, who, what, amt in claims:
        out.append(mail(at(d, 15, rng.randint(0, 59)), kn, [ME_P], f"【康宁人寿】理赔申请已受理（被保险人：{who}）",
                        f"尊敬的林舟先生：\n您为被保险人{who}提交的{what}医疗费用理赔申请已受理，案件号CL{d:%Y%m%d}{rng.randint(1000, 9999)}，预计3-5个工作日完成审核。\n康宁人寿",
                        "finance", "康宁人寿", kind="insurance", people=[who]))
        out.append(mail(at(next_workday(d, 4), 11, rng.randint(0, 59)), kn, [ME_P], f"【康宁人寿】理赔款已到账：{money(amt)}",
                        f"尊敬的林舟先生：\n被保险人{who}的{what}理赔已审核通过，赔付金额{money(amt)}已转入您尾号3390的储蓄卡，请注意查收。\n康宁人寿", "finance", "康宁人寿",
                        kind="insurance", people=[who]))
    # children's hospital / dental appointments
    hosp = {"name": "京禾儿童医院预约平台", "email": "booking@jinghe-kids-hospital.cn", "org": "京禾儿童医院"}
    dent = {"name": "博雅口腔", "email": "service@boya-dental.cn", "org": "博雅口腔"}
    for d, dept, who in [(dt.date(2024, 3, 12), "呼吸内科", "林小禾"), (dt.date(2024, 11, 25), "呼吸内科", "林小禾"), (dt.date(2025, 5, 20), "眼科（视力筛查复查）", "林小禾"),
                         (dt.date(2025, 9, 16), "口腔科（窝沟封闭）", "林小禾"), (dt.date(2026, 3, 7), "发热门诊", "林小禾"), (dt.date(2026, 8, 3), "眼科（散瞳验光）", "林小禾")]:
        bd = d - dt.timedelta(days=rng.randint(1, 4))
        out.append(mail(at(bd, 21, rng.randint(0, 59)), hosp, [ME_P], f"预约成功：{d.month}月{d.day}日 {dept}",
                        f"林舟您好，您已为就诊人{who}成功预约{d:%Y-%m-%d}（{weekday_cn(d)}）上午 京禾儿童医院 {dept} 普通号，就诊序号{rng.randint(5, 40)}。"
                        "请提前30分钟到院，携带就诊卡及医保卡。如需取消请提前一天在平台操作。", "health", "京禾儿童医院", kind="clinic", people=[who]))
    for d in [dt.date(2024, 5, 18), dt.date(2025, 5, 24), dt.date(2026, 5, 16)]:
        out.append(mail(at(d - dt.timedelta(days=6), 12, rng.randint(0, 59)), dent, [ME_P], f"【博雅口腔】预约确认：{d.month}月{d.day}日 洁牙",
                        f"林舟先生您好，已为您预约{d:%Y-%m-%d}（{weekday_cn(d)}）10:30 博雅口腔望京诊所 洁牙及口腔检查，医生：吕医生。洁牙费用¥288（会员价）。如需改约请致电诊所。",
                        "health", "博雅口腔", kind="clinic", people=["林舟"]))
    return [x for x in out if x["date"] <= AS_OF.isoformat()]
