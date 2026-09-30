import os
import smtplib
from email.mime.text import MIMEText


def yen(v): return "不明" if v is None else f"{v:,}円"


def add_property(lines, p, n):
    lines.append(f"{n}. {p.get('score','')} {p.get('area','')} {yen(p.get('rent'))}")
    lines.append(f"   {p.get('title','')}")
    lines.append(f"   種別：{p.get('property_kind','')} / 間取り：{p.get('layout','')} / 面積：{p.get('size','')}㎡")
    if p.get('address'): lines.append(f"   住所：{p['address']}")
    if p.get('comment'): lines.append(f"   注目点：{p['comment']}")
    if p.get('official_matches'):
        lines.append("   【広島市公式一覧との一致】")
        for r in p['official_matches'][:6]:
            label = r.get('category','')
            date = r.get('date','')
            name = r.get('name','')
            addr = r.get('address','')
            lines.append(f"     {r.get('type','')} / {label} / {date}")
            if name: lines.append(f"     施設名：{name}")
            if addr: lines.append(f"     公式所在地：{addr}")
    if p.get('duplicate_count',1)>1: lines.append(f"   同一候補として統合：{p['duplicate_count']}件")
    lines.append(f"   {p.get('url','')}")
    for su in p.get('source_urls',[]):
        if su.get('url') and su.get('url') != p.get('url'):
            lines.append(f"   別掲載：{su.get('source')} {su.get('url')}")
    lines.append("")


def send_report(data):
    user=os.environ["EMAIL_USER"]; password=os.environ["EMAIL_PASS"]; to=os.environ["EMAIL_TO"]
    official=data.get('official_db',{})
    official_records=len(official.get('records',[]))
    official_diag=official.get('diagnostics',[])
    official_ok=sum(1 for x in official_diag if x.get('status')=='OK')
    official_errors=[x for x in official_diag if x.get('status')!='OK']
    candidates=data.get('candidates',0)
    new=data.get('new',[])
    down=data.get('price_down',[])
    official_candidate_count=sum(1 for p in data.get('sample',[]) if p.get('official_matches'))

    lines=[
        "広島 民泊候補物件 第8弾レポート",
        "="*52,
        "",
        f"取得した生データ：{data.get('total_raw',0)}件",
        f"重複統合後：{data.get('total_retrieved',0)}件",
        f"第8弾の候補：{candidates}件",
        f"今回の新着：{len(new)}件",
        f"家賃値下げ：{len(down)}件",
        f"広島市公式一覧DB：{official_records}レコード / 取得成功{official_ok}系統",
        "",
        "【第8弾の検索ルール】",
        "・戸建て系：従来通り、SUUMO / HOME'S / at home を広く検索",
        "・アパート/マンション等：広島市公式の『民泊届出』または『旅館業許可』一覧と一致するものだけ採用",
        "・家賃上限：10万円以下",
        "",
        "【広島市公式PDF取得状況】",
        "-"*52,
    ]
    for x in official_diag:
        lines.append(f"{x.get('kind')}：status={x.get('status')} records={x.get('records',0)}")
        if x.get('pdf_url'): lines.append(f"  PDF：{x['pdf_url']}")
        if x.get('error'): lines.append(f"  エラー：{x['error']}")
    if official_errors:
        lines.append("⚠ 公式一覧の取得に失敗した系統があります。集合住宅候補が少なく出る可能性があります。")

    lines += ["", "【サイト別取得状況】", "-"*52]
    for x in data.get('diagnostics',[]):
        lines.append(f"{x.get('source')} / {x.get('area')}：raw={x.get('raw',0)} parsed={x.get('parsed',0)} status={x.get('status')} body={x.get('body_chars',0)}")
        if x.get('error'): lines.append(f"  エラー：{x['error']}")
    failures=[x for x in data.get('diagnostics',[]) if x.get('status')!='OK']
    if failures:
        lines += ["", "⚠ 取得0件・タイムアウト・エラーがあります。", "これは物件0件とは限らず、取得側の問題の可能性があります。"]

    lines += ["", "【新着（全件）】", "-"*52]
    if new:
        for i,p in enumerate(new,1): add_property(lines,p,i)
    else:
        lines.append("該当なし")
    lines += ["", "【値下げ（全件）】", "-"*52]
    if down:
        for i,p in enumerate(down,1): add_property(lines,p,i)
    else:
        lines.append("該当なし")
    if not new and not down:
        lines += ["", "【現在取得できている候補（最大20件）】", "-"*52]
        for i,p in enumerate(data.get('sample',[])[:20],1): add_property(lines,p,i)
    lines += [
        "",
        "="*52,
        "※『公式一覧に一致』は、広島市が公開している届出/許可一覧との住所・建物名等の一致を示すもので、現在の賃貸募集部屋がそのまま民泊可能という意味ではありません。",
        "※賃貸借契約、管理規約、所有者・管理会社の承諾、消防・建築・条例等は別途確認してください。",
        "※同一物件と思われる掲載は住所・家賃・間取り・面積等で統合しています。",
    ]
    msg=MIMEText("\n".join(lines),"plain","utf-8")
    msg["Subject"]=f"広島民泊 第8弾 候補{candidates}件 / 新着{len(new)}件 / 公式一致あり"
    msg["From"]=user; msg["To"]=to
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(user,password); smtp.send_message(msg)
