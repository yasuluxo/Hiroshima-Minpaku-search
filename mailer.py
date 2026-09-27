
import os, smtplib
from email.mime.text import MIMEText
EMAIL_USER=os.environ["EMAIL_USER"]; EMAIL_PASS=os.environ["EMAIL_PASS"]; EMAIL_TO=os.environ["EMAIL_TO"]
def send_report(d):
    body=f"広島民泊レポート\n取得:{d['total']}件\n新着:{len(d['new'])}件\n\n"
    for i,p in enumerate(d["new"][:10],1):
        body+=f"{i}. {p['score']} {p['area']} {p['rent']} {p['layout']}\n{p['title']}\n{p['comment']}\n{p['url']}\n\n"
    if not d["new"]: body+="本日の新着はありません。"
    msg=MIMEText(body,"plain","utf8")
    msg["Subject"]=f"広島民泊候補 新着{len(d['new'])}件"; msg["From"]=EMAIL_USER; msg["To"]=EMAIL_TO
    with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
        s.login(EMAIL_USER,EMAIL_PASS); s.send_message(msg)