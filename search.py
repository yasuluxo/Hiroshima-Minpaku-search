import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

import mailer
from config import *

# -------------------------

def load(path):

    try:
        with open(path,"r",encoding="utf8") as f:
            return json.load(f)
    except:
        return {}

def save(path,data):

    with open(path,"w",encoding="utf8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

# -------------------------

def price(text):

    m=re.search(r"([0-9.]+)",text)

    if m:
        return float(m.group(1))

    return 999

# -------------------------

def score(prop):

    point=0
    reason=[]

    txt=prop["title"]+" "+prop["layout"]+" "+prop["address"]

    if "戸建" in txt:
        point+=3
        reason.append("戸建")

    if "木造" in txt:
        point+=2
        reason.append("木造")

    if "SOHO" in txt:
        point+=2
        reason.append("SOHO")

    if "事務所可" in txt:
        point+=2
        reason.append("事務所可")

    if "店舗相談" in txt:
        point+=2
        reason.append("店舗相談")

    if "駐車場" in txt:
        point+=1
        reason.append("駐車場")

    if prop["price"]<=6:
        point+=2
        reason.append("低家賃")

    if prop["area"] in ["中区","南区"]:
        point+=1

    if point>5:
        point=5

    star="★"*point+"☆"*(5-point)

    return star,"・".join(reason)

# -------------------------

def scrape(area):

    print("検索",area["name"])

    r=requests.get(area["url"],headers=HEADERS,timeout=20)

    soup=BeautifulSoup(r.text,"lxml")

    cards=soup.select(".cassetteitem")

    result=[]

    for c in cards:

        try:

            title=c.select_one(".cassetteitem_content-title").text.strip()

            rent=c.select(".cassetteitem_price--rent")[0].text.strip()

            layout=c.select(".cassetteitem_madori")[0].text.strip()

            size=c.select(".cassetteitem_menseki")[0].text.strip()

            address=c.select_one(".cassetteitem_detail-col1").text.strip()

            link=c.select_one(".js-cassette_link_href")["href"]

            if link.startswith("/"):
                link="https://suumo.jp"+link

            uid=link.split("/")[-2]

            p={

                "id":uid,

                "area":area["name"],

                "title":title,

                "rent":rent,

                "price":price(rent),

                "layout":layout,

                "size":size,

                "address":address,

                "url":link,

                "map":"https://maps.google.com/?q="+quote(address)

            }

            star,comment=score(p)

            p["score"]=star
            p["comment"]=comment

            result.append(p)

        except:

            continue

    return result

# -------------------------

def collect():

    props=[]

    for a in AREAS:

        props.extend(scrape(a))

    print("取得",len(props))

    return props

# -------------------------

def detect(props):

    seen=load(SEEN_FILE)

    prices=load(PRICE_FILE)

    new=[]
    down=[]

    for p in props:

        uid=p["id"]

        if uid not in seen:

            new.append(p)

            seen[uid]=True

        if uid in prices:

            if p["price"]<prices[uid]:

                p["old_price"]=prices[uid]

                down.append(p)

        prices[uid]=p["price"]

    save(SEEN_FILE,seen)

    save(PRICE_FILE,prices)

    return new,down

# -------------------------

props=collect()

new,down=detect(props)

output={

    "total":len(props),

    "new":new,

    "down":down

}

with open("output.json","w",encoding="utf8") as f:

    json.dump(output,f,ensure_ascii=False,indent=2)

mailer.send()

print("新着",len(new))
print("値下",len(down))