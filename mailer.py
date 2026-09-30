import os, smtplib
from email.mime.text import MIMEText


def yen(v): return "不明" if v is None else f"{v:,}円"


def add_property(lines,p,n):
    lines.append(f"{n}. {p.get('score','')} {p.get('area','')} {yen(p.get('rent'))}")
    lines.append(f"   {p.get('title','')}")
    lines.append(f"   間取り：{p.get('layout','')} / 面積：{p.get('size','')}㎡")
    if p.get('address'): lines.append(f"   住所：{p['address']}")
    if p.get('comment'): lines.append(f"   注目点：{p['comment']}")
    if p.get('duplicate_count',1)>1: lines.append(f"   同一候補として統合：{p['duplicate_count']}件")
    lines.append(f"   {p.get('url','')}")
    # 代表URL以外も残す
    for su in p.get('source_urls',[]):
        if su.get('url') and su.get('url') != p.get('url'):
            lines.append(f"   別掲載：{su.get('source')} {su.get('url')}")
    lines.append("")


def send_report(data):
    user=os.environ["EMAIL_USER"]; password=os.environ["EMAIL_PASS"]; to=os.environ["EMAIL_TO"]
    lines=["広島 民泊候補物件 第7.1弾レポート","="*44,"",f"取得した生データ：{data.get('total_raw',0)}件",f"重複統合後：{data.get('total_retrieved',0)}件",f"家賃10万円以下の候補：{data.get('candidates',0)}件",f"今回の新着：{len(data.get('new',[]))}件",f"家賃値下げ：{len(data.get('price_down',[]))}件",""]
    lines += ["【サイト別取得状況】","-"*44]
    diagnostics=data.get('diagnostics',[])
    for x in diagnostics:
        lines.append(f"{x.get('source')} / {x.get('area')}：raw={x.get('raw',0)} parsed={x.get('parsed',0)} status={x.get('status')} body={x.get('body_chars',0)}")
        if x.get('error'): lines.append(f"  エラー：{x['error']}")
    failures=[x for x in diagnostics if x.get('status')!="OK"]
    if failures:
        lines += ["","⚠ 取得0件・タイムアウト・エラーがあります。","これは物件0件とは限らず、取得側の問題の可能性があります。"]
    # 新着は15件制限を撤廃し、全件送信
    lines += ["","【新着（全件）】","-"*44]
    new=data.get('new',[])
    if new:
        for i,p in enumerate(new,1): add_property(lines,p,i)
    else: lines.append("該当なし")
    lines += ["","【値下げ（全件）】","-"*44]
    down=data.get('price_down',[])
    if down:
        for i,p in enumerate(down,1): add_property(lines,p,i)
    else: lines.append("該当なし")
    if not new and not down:
        lines += ["","【現在取得できている候補（全件）】","-"*44]
        for i,p in enumerate(data.get('sample',[]),1): add_property(lines,p,i)
    lines += ["","="*44,"※同一物件と思われる掲載は住所・家賃・間取り・面積等で統合しています。","※民泊利用可否は貸主・管理会社・自治体等へ確認してください。"]
    msg=MIMEText("\n".join(lines),"plain","utf-8")
    msg["Subject"]=f"広島民泊 第7.1弾 候補{data.get('candidates',0)}件 / 新着{len(new)}件"
    msg["From"]=user; msg["To"]=to
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(user,password); smtp.send_message(msg)
