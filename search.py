from playwright.sync_api import sync_playwright
from urllib.parse import quote
import smtplib
from email.mime.text import MIMEText
import json
import os
import re

EMAIL_USER=os.environ["EMAIL_USER"]
EMAIL_PASS=os.environ["EMAIL_PASS"]
EMAIL_TO=os.environ["EMAIL_TO"]

AREAS=[
("中区","https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34101"),
("南区","https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34103"),
("西区","https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34104"),
("東区","https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34102"),
]

SEEN="seen.json"
PRICE="price_history.json"

def load(path):
    if os.path.exists(path):
        return json.load(open(path,encoding="utf8"))
    return {}

def save(path,data):
    json.dump(data,open(path,"w",encoding="utf8"),ensure_ascii=False,indent=2)

def get_price(text):
    m=re.search(r"([0-9.]+)",text)
    return float(m.group(1)) if m else 999

def score(title,layout,area,price):
    s=0
    r=[]
    t=f"{title} {layout}"

    if "戸建" in t:
        s+=3;r.append("戸建")
    if "木造" in t:
        s+=2;r.append("木造")
    if "SOHO" in t or "事務所可" in t:
        s+=2;r.append("SOHO")
    if "駐車場" in t:
        s+=1;r.append("駐車場")
    if price<=6:
        s+=2;r.append("低家賃")
    if area in ["中区","南区"]:
        s+=1

    s=min(s,5)
    return "★"*s+"☆"*(5-s),"・".join(r)

def scrape():
    props=[]

    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()

        for area,url in AREAS:

            page.goto(url,wait_until="networkidle",timeout=60000)

            page.wait_for_timeout(2000)

            cards=page.locator(".cassetteitem")

            count=cards.count()

            for i in range(count):

                c=cards.nth(i)

                try:

                    title=c.locator(".cassetteitem_content-title").inner_text().strip()

                    rent=c.locator(".cassetteitem_price--rent").first.inner_text().strip()

                    layout=c.locator(".cassetteitem_madori").first.inner_text().strip()

                    size=c.locator(".cassetteitem_menseki").first.inner_text().strip()

                    address=c.locator(".cassetteitem_detail-col1").inner_text().strip()

                    href=c.locator(".js-cassette_link_href").first.get_attribute("href")

                    if href.startswith("/"):
                        href="https://suumo.jp"+href

                    uid=href.split("/")[-2]

                    price=get_price(rent)

                    star,comment=score(title,layout,area,price)

                    props.append({
                        "id":uid,
                        "area":area,
                        "title":title,
                        "rent":rent,
                        "price":price,
                        "layout":layout,
                        "size":size,
                        "address":address,
                        "url":href,
                        "map":"https://maps.google.com/?q="+quote(address),
                        "score":star,
                        "comment":comment
                    })

                except:
                    pass

        browser.close()

    return props

def detect(props):

    seen=load(SEEN)
    prices=load(PRICE)

    new=[]
    down=[]

    for p in props:

        uid=p["id"]

        if uid not in seen:
            new.append(p)
            seen[uid]=True

        if uid in prices and p["price"]<prices[uid]:
            p["old_price"]=prices[uid]
            down.append(p)

        prices[uid]=p["price"]

    save(SEEN,seen)
    save(PRICE,prices)

    return new,down

def send(total,new,down):

    body=f"""広島 民泊レポート

取得件数：{total}
新着：{len(new)}
値下がり：{len(down)}

"""

    if not new and not down:
        body+="新着物件はありません。"

    if new:
        body+="\n【新着】\n\n"

        for i,p in enumerate(new[:10],1):

            body+=f"""■ {i} {p["score"]}

{p["area"]} {p["title"]}

家賃：{p["rent"]}
間取り：{p["layout"]}
面積：{p["size"]}

{p["comment"]}

地図
{p["map"]}

SUUMO
{p["url"]}

----------------------

"""

    if down:

        body+="\n【値下がり】\n\n"

        for p in down:

            body+=f"""{p["area"]} {p["title"]}

{p["old_price"]}万円→{p["price"]}万円

{p["url"]}

----------------------

"""

    msg=MIMEText(body,"plain","utf8")
    msg["Subject"]=f"広島 民泊候補 新着{len(new)}件"
    msg["From"]=EMAIL_USER
    msg["To"]=EMAIL_TO

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_USER,EMAIL_PASS)
        smtp.send_message(msg)

if __name__=="__main__":

    props=scrape()

    new,down=detect(props)

    send(len(props),new,down)

    print("取得",len(props))