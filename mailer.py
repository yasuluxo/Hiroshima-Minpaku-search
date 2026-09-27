import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
EMAIL_TO = os.environ["EMAIL_TO"]


def send():

    with open("output.json","r",encoding="utf8") as f:
        data=json.load(f)

    new=data["new"]
    down=data["down"]
    total=data["total"]

    today=datetime.now().strftime("%Y-%m-%d")

    body=f"""広島 民泊物件レポート

日付：{today}
取得件数：{total}

"""

    if len(new)==0 and len(down)==0:

        body+="新着・値下がり物件はありません。"

    if new:

        body+="【新着】\n\n"

        for i,p in enumerate(new,1):

            body+=f"""
■ {i} {p['score']}

エリア：{p['area']}

家賃：{p['rent']}

間取り：{p['layout']}

面積：{p['size']}

住所：{p['address']}

おすすめ：
{p['comment']}

GoogleMap
{p['map']}

SUUMO
{p['url']}

--------------------------

"""

    if down:

        body+="\n【値下がり】\n\n"

        for p in down:

            body+=f"""
{p['area']} {p['title']}

{p['old_price']}万円 → {p['price']}万円

{p['url']}

--------------------------

"""

    msg=MIMEText(body,"plain","utf8")

    msg["Subject"]=f"広島 民泊候補 {len(new)+len(down)}件"

    msg["From"]=EMAIL_USER
    msg["To"]=EMAIL_TO

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:

        smtp.login(EMAIL_USER,EMAIL_PASS)

        smtp.send_message(msg)