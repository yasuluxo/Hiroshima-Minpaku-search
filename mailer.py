import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

EMAIL_USER=os.environ["EMAIL_USER"]
EMAIL_PASS=os.environ["EMAIL_PASS"]
EMAIL_TO=os.environ["EMAIL_TO"]

def build():

    with open("output.json","r",encoding="utf8") as f:
        data=json.load(f)

    new=data["new"]
    down=data["down"]

    today=datetime.now().strftime("%Y-%m-%d")

    body=f"広島 民泊候補レポート\\n{today}\\n\\n"

    if len(new)==0 and len(down)==0:
        body+="本日の新着はありません。"
        return body,0

    if new:
        body+="【新着物件】\\n\\n"

        for i,p in enumerate(new,1):

            body+=f"""■{i} {p["score"]}

{p["area"]} {p["title"]}

家賃：{p["rent"]}

間取り：{p["layout"]}

面積：{p["size"]}

住所：{p["address"]}

コメント：{p["comment"]}

URL
{p["url"]}

-------------------------

"""

    if down:

        body+="\\n【値下がり】\\n\\n"

        for p in down:

            body+=f"""{p["area"]} {p["title"]}

{p["old_price"]}万円 → {p["price"]}万円

{p["url"]}

-------------------------

"""

    return body,len(new)+len(down)

def send():

    body,count=build()

    msg=MIMEText(body,"plain","utf8")

    msg["Subject"]=f"広島 民泊候補 {count}件"

    msg["From"]=EMAIL_USER

    msg["To"]=EMAIL_TO

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:

        smtp.login(EMAIL_USER,EMAIL_PASS)

        smtp.send_message(msg)

if __name__=="__main__":
    send()