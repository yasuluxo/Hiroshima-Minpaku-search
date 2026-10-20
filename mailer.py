import os
import smtplib
from email.mime.text import MIMEText


def yen(v):
    return "不明" if v is None else f"{v:,}円"


def add_property(lines, p, n):
    lines.append(f"{n}. {p.get('score','')} {p.get('area','')} {yen(p.get('rent'))}")
    if p.get('title'): lines.append(f"   {p.get('title')}")
    lines.append(f"   種別：{p.get('property_kind','')} / 間取り：{p.get('layout') or '不明'} / 面積：{p.get('size') or '不明'}㎡")
    if p.get('address'): lines.append(f"   住所：{p['address']}")
    if p.get('comment'): lines.append(f"   注目点：{p['comment']}")
    if p.get('official_matches'):
        lines.append("   【広島市公式一覧との照合】")
        lines.append(f"     判定：{p.get('official_match_level','一致')}")
        for r in p['official_matches'][:3]:
            lines.append(f"     {r.get('type','')} / {r.get('category','')} / {r.get('date','')}")
            if r.get('name'): lines.append(f"     施設名：{r['name']}")
            if r.get('address'): lines.append(f"     公式所在地：{r['address']}")
            if r.get('rooms'): lines.append(f"     公式登録部屋：{', '.join(r.get('rooms',[]))}")
    lines.append(f"   {p.get('url','')}")
    for su in p.get('source_urls',[]):
        if su.get('url') and su.get('url') != p.get('url'):
            lines.append(f"   別掲載：{su.get('source')} {su.get('url')}")
    lines.append("")


def send_report(data):
    user=os.environ["EMAIL_USER"]; password=os.environ["EMAIL_PASS"]; to=os.environ["EMAIL_TO"]
    candidates=data.get('all_candidates',data.get('sample',[]))
    new=data.get('new',[]); down=data.get('price_down',[])
    hist=data.get('history',{}).get('records',{}) if isinstance(data.get('history',{}),dict) else {}
    match_levels=data.get('official_match_levels',{})
    lines=[
        "広島 民泊候補物件 第8.5弾レポート",
        "="*56,
        "",
        f"今回取得した候補：{len(candidates)}件",
        f"累計抽出候補：{len(hist)}件",
        f"現在掲載候補：{len(candidates)}件",
        f"未確認候補：{sum(1 for x in hist.values() if x.get('review_status','未確認')=='未確認')}件",
        f"今回の新着：{len(new)}件",
        f"家賃値下げ：{len(down)}件",
        f"公式照合：部屋一致{match_levels.get('部屋番号一致',0)} / 住所一致{match_levels.get('住所一致',0)} / 建物名一致{match_levels.get('建物名一致',0)}",
        "",
        "【第8弾の検索ルール】",
        "・戸建て系：SUUMO / HOME'S / at home を広く検索",
        "・アパート/マンション等：広島市公式の「民泊届出」または「旅館業許可」一覧と、住所または建物名が明確に一致するものだけ採用",
        "・公式照合は町名だけの一致を採用しない",
        "・公式照合は建物名の部分一致を採用しない",
        "・部屋番号一致だけでは採用せず、建物名/住所の整合を確認",
        "・公式照合は「部屋一致」「住所一致」「建物名一致」を区別",
        "・家賃上限：10万円以下",
        "",
        "【今回の候補】",
        "-"*56,
    ]
    if candidates:
        for i,p in enumerate(candidates,1): add_property(lines,p,i)
    else:
        lines.append("該当なし")
    lines += ["", "【今回の新着】", "-"*56]
    if new:
        for i,p in enumerate(new,1): add_property(lines,p,i)
    else: lines.append("該当なし")
    if down:
        lines += ["", "【家賃値下げ】", "-"*56]
        for i,p in enumerate(down,1): add_property(lines,p,i)
    lines += [
        "",
        "="*56,
        "※集合住宅は、公式一覧を取得でき、住所/建物名等の明確な一致を確認できたものだけ候補に採用します。",
        "※部屋番号だけの一致、町名だけの一致、建物名の部分一致は採用しません。",
        "※公式一覧への掲載は、現在の賃貸募集部屋がそのまま民泊可能という意味ではありません。",
        "※賃貸借契約、管理規約、所有者・管理会社の承諾、消防・建築・条例等は別途確認してください。",
        "※広島市公式PDFの取得状況・サイト別取得状況などのデバッグ情報はメールから省き、diagnostics.jsonに保存しています。",
    ]
    msg=MIMEText("\n".join(lines),"plain","utf-8")
    msg["Subject"]=f"広島民泊 第8.5弾 候補{len(candidates)}件 / 新着{len(new)}件 / 公式照合"
    msg["From"]=user; msg["To"]=to
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(user,password); smtp.send_message(msg)
