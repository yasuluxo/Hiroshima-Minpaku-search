import os
import smtplib
from email.mime.text import MIMEText

VERSION = "8.11"


def yen(v):
    return "不明" if v is None else f"{v:,}円"


def add_property(lines, p, n):
    lines.append(f"{n}. {p.get('area','')} {yen(p.get('rent'))}")
    if p.get('title'): lines.append(f"   {p['title']}")
    lines.append(f"   種別：{p.get('property_kind','')} / 間取り：{p.get('layout') or '不明'} / 面積：{p.get('size') or '不明'}㎡")
    if p.get('address'): lines.append(f"   住所：{p['address']}")
    if p.get('classification_reason'): lines.append(f"   種別判定：{p['classification_reason']}")
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
    candidates=data.get('all_candidates',[])
    new=data.get('new',[]); down=data.get('price_down',[])
    hist=data.get('history',{}).get('records',{}) if isinstance(data.get('history',{}),dict) else {}
    match_levels=data.get('official_match_levels',{})
    legacy=data.get('legacy_unvalidated_count',0)
    stage=data.get("stage_summary",{})
    lines=[
        f"広島 民泊候補物件 第{VERSION}弾レポート",
        "="*56,
        "",
        f"今回取得した候補：{len(candidates)}件",
        f"累計有効候補：{data.get('valid_history_count',0)}件",
        f"現在掲載候補：{len(candidates)}件",
        f"未確認候補：{sum(1 for x in hist.values() if isinstance(x,dict) and x.get('validation_status')=='valid' and x.get('review_status','未確認')=='未確認')}件",
        f"今回の新着：{len(new)}件",
        f"家賃値下げ：{len(down)}件",
        f"公式照合：部屋一致{match_levels.get('部屋番号一致',0)} / 住所一致{match_levels.get('住所一致',0)} / 建物名一致{match_levels.get('建物名一致',0)}",
        f"旧版履歴の未再検証：{legacy}件（履歴は削除していません）",
        "",
        "【検索・除外の段階別件数】",
        f"・検索サイトから取得した物件URL：{stage.get('raw_items',0)}件",
        f"・重複除去後の詳細URL：{stage.get('unique_detail_urls',0)}件",
        f"・詳細ページ読み込み成功：{stage.get('detail_page_ok',0)}件",
        f"・物件情報（種別・住所）検証成功：{stage.get('detail_validated',0)}件",
        f"・家賃10万円以下：{stage.get('under_rent_cap',0)}件",
        f"・戸建て系：{stage.get('detached',0)}件",
        f"・集合住宅：{stage.get('apartments',0)}件",
        f"・種別不明：{stage.get('unknown',0)}件",
        f"・詳細ページ取得エラー：{stage.get('detail_errors',0)}件",
        f"・集合住宅の公式DB照合対象：{data.get('official_probe_summary',{}).get('apartment_records_checked',0)}件",
        f"・集合住宅の公式DB一致：{data.get('official_probe_summary',{}).get('matched',0)}件 / 不一致：{data.get('official_probe_summary',{}).get('unmatched',0)}件",
        "",
        "【サイト別取得・判定状況】",
        "・戸建て系：SUUMO / HOME'S / at home を広く検索",
        "・集合住宅：広島市公式の「民泊届出」または「旅館業許可」一覧と、住所または建物名が明確に一致するものだけ採用",
        "・集合住宅は公式照合できなければ候補から除外",
        "・物件詳細ページで種別を再確認し、検索画面の共通UI文言だけでは戸建て系にしない",
        "・物件詳細ページで住所を再取得し、別物件の住所を混ぜない",
        "・町名だけの公式照合は不採用",
        "・建物名の部分一致は不採用",
        "・部屋番号一致だけでは不採用。建物/住所の整合を確認",
        "・家賃上限：10万円以下",
        "",
    ]
    for d in data.get("diagnostics",[]):
        lines.append(f"・{d.get('area','')} / {d.get('source','')}：URL候補{d.get('raw',0)} / 取得{d.get('parsed',0)} / 詳細検証{d.get('detail_validated',0)} / 戸建{d.get('detached',0)} / 集合{d.get('apartment',0)} / 不明{d.get('unknown',0)} / エラー{d.get('detail_errors',0)} / 状態{d.get('status','')}")
    lines += [
        "",
        "【今回の候補】",
        "-"*56,
    ]
    if candidates:
        for i,p in enumerate(candidates,1): add_property(lines,p,i)
    else: lines.append("該当なし")
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
        "※第8.11弾では、サイト別の詳細ページ解析を分離し、検索URL取得・詳細ページ読み込み・物件情報解析・種別判定・公式照合・採用を分けて記録しています。",
        "※集合住宅は、現在取得した広島市公式一覧との明確な一致が必要です。",
        "※部屋番号だけの一致、町名だけの一致、建物名の部分一致は採用しません。",
        "※広島市公式一覧への掲載は、現在の賃貸募集部屋がそのまま民泊可能という意味ではありません。",
        "※賃貸借契約、管理規約、所有者・管理会社の承諾、消防・建築・条例等は別途確認してください。",
        "※検索結果の詳細診断・公式PDF診断・公式照合の不一致理由はdiagnostics.jsonに保存しています。",
    ]
    msg=MIMEText("\n".join(lines),"plain","utf-8")
    msg["Subject"]=f"広島民泊 第{VERSION}弾 候補{len(candidates)}件 / 新着{len(new)}件 / 公式照合"
    msg["From"]=user; msg["To"]=to
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(user,password); smtp.send_message(msg)
