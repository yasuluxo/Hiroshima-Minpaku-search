import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import mailer

# ===========================
# 広島 民泊物件ハンター Ver3
# ===========================

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

AREAS = [
    {
        "name":"中区",
        "url":"https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34101"
    },
    {
        "name":"南区",
        "url":"https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34103"
    },
    {
        "name":"西区",
        "url":"https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34104"
    },
    {
        "name":"東区",
        "url":"https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34102"
    }
]

SEEN_FILE = "seen.json"
PRICE_FILE = "price_history.json"

# ------------------------------

def load(path):

    if os.path.exists(path):
        with open(path,"r",encoding="utf8") as f:
            return json.load(f)

    return {}

def save(path,obj):

    with open(path,"w",encoding="utf8") as f:
        json.dump(obj,f,ensure_ascii=False,indent=2)

# ------------------------------

def get_price(text):

    m = re.search(r"([0-9.]+)",text)

    if m:
        return float(m.group(1))

    return 999

# ------------------------------

def get_company(title):

    m = re.search(r"(株式会社.+?|.+?不動産)",title)

    if m:
        return m.group(1)

    return ""

# ------------------------------

def judge(prop):

    score = 0

    reasons=[]

    text = (
        prop["title"] +
        prop["layout"] +
        prop["address"]
    )

    if "戸建" in text:
        score += 3
        reasons.append("戸建")

    if "木造" in text:
        score += 2
        reasons.append("木造")

    if "SOHO" in text:
        score += 2
        reasons.append("SOHO")

    if "事務所可" in text:
        score += 2
        reasons.append("事務所可")

    if "店舗相談" in text:
        score += 2
        reasons.append("店舗相談")

    if "駐車場" in text:
        score += 1
        reasons.append("駐車場")

    if prop["price"] <= 6:
        score += 2
        reasons.append("低家賃")

    if prop["area"] in ["中区","南区"]:
        score += 1
        reasons.append("人気エリア")

    stars = "★"*min(score,5) + "☆"*(5-min(score,5))

    return stars,"・".join(reasons)

# ------------------------------

def scrape(area):

    print("検索:",area["name"])

    r = requests.get(area["url"],headers=HEADERS,timeout=20)

    soup = BeautifulSoup(r.text,"lxml")

    cards = soup.select(".cassetteitem")

    results=[]

    for c in cards:

        try:

            title = c.select_one(
                ".cassetteitem_content-title"
            ).get_text(strip=True)

            address = c.select_one(
                ".cassetteitem_detail-col1"
            ).get_text(strip=True)

            rent = c.select(
                ".cassetteitem_price--rent"
            )[0].get_text(strip=True)

            layout = c.select(
                ".cassetteitem_madori"
            )[0].get_text(strip=True)

            size = c.select(
                ".cassetteitem_menseki"
            )[0].get_text(strip=True)

            href = c.select_one(
                ".js-cassette_link_href"
            )["href"]

            if href.startswith("/"):
                href = "https://suumo.jp"+href

            uid = href.split("/")[-2]

            prop = {

                "id":uid,

                "site":"SUUMO",

                "area":area["name"],

                "title":title,

                "address":address,

                "rent":rent,

                "price":get_price(rent),

                "layout":layout,

                "size":size,

                "company":get_company(title),

                "url":href,

                "map":"https://maps.google.com/?q="+quote(address)

            }

            star,comment = judge(prop)

            prop["score"]=star

            prop["comment"]=comment

            results.append(prop)

        except Exception:

            continue

    return results

# ------------------------------

def collect():

    all=[]

    for area in AREAS:

        all.extend(scrape(area))

    return all

# ------------------------------

def detect(properties):

    seen = load(SEEN_FILE)

    prices = load(PRICE_FILE)

    new=[]

    down=[]

    for p in properties:

        uid = p["id"]

        if uid not in seen:

            new.append(p)

            seen[uid]=True

        if uid in prices:

            if p["price"] < prices[uid]:

                p["old_price"]=prices[uid]

                down.append(p)

        prices[uid]=p["price"]

    save(SEEN_FILE,seen)

    save(PRICE_FILE,prices)

    return new,down

# ------------------------------

if __name__=="__main__":

    properties = collect()

    new,down = detect(properties)

    output={

        "new":new,

        "down":down

    }

    with open("output.json","w",encoding="utf8") as f:

        json.dump(output,f,ensure_ascii=False,indent=2)

    mailer.send()

    print("新着",len(new),"件")

    print("値下がり",len(down),"件")