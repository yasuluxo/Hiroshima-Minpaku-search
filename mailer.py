import os,smtplib
from email.mime.text import MIMEText
def yen(v): return '不明' if v is None else f'{v:,}円'
def send_report(d):
 u=os.environ['EMAIL_USER']; pw=os.environ['EMAIL_PASS']; to=os.environ['EMAIL_TO']; body=[]
 body += ['広島 民泊候補物件 第7弾レポート','='*42,'',f"全サイト取得後の重複除去：{d['total_retrieved']}件",f"家賃10万円以下の候補：{d['candidates']}件",f"今回の新着：{len(d['new'])}件",f"家賃値下げ：{len(d['price_down'])}件",'','【サイト別取得状況】']
 for x in d['diagnostics']:
  body.append(f"{x['source']} / {x['area']}：raw={x['raw']} parsed={x['parsed']} status={x['status']}")
  if x.get('error'): body.append('  エラー：'+x['error'])
 if any(x['status']!='OK' for x in d['diagnostics']): body += ['', '⚠ 取得0件・タイムアウト・エラーがあります。これは「物件0件」とは別です。']
 def add(title,props,limit=15):
  body.extend(['',title,'-'*42])
  if not props: body.append('該当なし'); return
  for i,p in enumerate(props[:limit],1):
   body += [f"{i}. {p.get('score','')} {p.get('area','')} {yen(p.get('rent'))}",f"   {p.get('title','')}",f"   間取り：{p.get('layout','')} / 面積：{p.get('size','')}㎡"]
   if p.get('comment'): body.append('   注目点：'+p['comment'])
   if p.get('old_price'): body.append('   旧家賃：'+yen(p['old_price']))
   body += [f"   {p.get('url','')}",'']
 add('【新着】',d['new']); add('【値下げ】',d['price_down'])
 if not d['new'] and not d['price_down']: add('【現在取得できている候補（サンプル）】',d.get('sample',[]),10)
 body += ['', '※「新着0件」と「取得0件」は別扱いです。','※民泊利用の可否は物件ごとに貸主・管理会社・自治体等へ確認してください。']
 msg=MIMEText('\n'.join(body),'plain','utf-8'); msg['Subject']=f"広島民泊 第7弾 候補{d['candidates']}件 / 新着{len(d['new'])}件"; msg['From']=u; msg['To']=to
 with smtplib.SMTP_SSL('smtp.gmail.com',465) as s: s.login(u,pw); s.send_message(msg)