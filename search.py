import os
import json
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ==========================
# 広島 民泊物件ハンター v1
# ==========================

HEADERS = {
    "User-Agent":
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
}

AREAS = [
    {
        "name": "中区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34101"
    },
    {
        "name": "南区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34103"
    },
    {
        "name": "西区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34104"
    },
    {
        "name": "東区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34102"
    }
]

SEEN_FILE = "seen.json"
PRICE_FILE = "price_history.json"

# ----------------------------

def load_json(path):
    if os.path.exists(path):
        with open(path,"r",encoding="utf8") as f:
            return json.load(f)
    return {}

def save_json(path,obj):
    with open(path,"w",encoding="utf8") as f:
        json.dump(obj,f,ensure_ascii=False,indent=2)

# ----------------------------

def extract_price(text):
    m = re.search(r"([0-9.]+)",text)
    if not m:
        return 999999
    return float(m.group(1))

# ----------------------------

def judge(prop):

    score = 0
    comment = []

    title = prop["title"]
    layout = prop["layout"]

    if prop["area"] in ["中区","南区"]:
        score += 2
        comment.append("人気エリア")

    if "RC" in title or "鉄筋" in title:
        score += 2
        comment.append("RC造")

    if "木造" in title:
        score += 1
        comment.append("木造")

    if "戸建" in title:
        score += 3
        comment.append("戸建")

    if layout in ["1LDK","2DK","2LDK"]:
        score += 2

    if prop["price"] <= 6:
        score += 2

    stars = "★"*min(score,5) + "☆"*(5-min(score,5))

    return stars,"・".join(comment)

# ----------------------------

def scrape_area(area):

    r = requests.get(area["url"],headers=HEADERS,timeout=20)

    soup = BeautifulSoup(r.text,"lxml")

    cards = soup.select(".cassetteitem")

    results = []

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

            href = c.select_one(".js-cassette_link_href")["href"]

            if href.startswith("/"):
                href = "https://suumo.jp"+href

            uid = href.split("/")[-2]

            price = extract_price(rent)

            prop = {
                "id": uid,
                "site": "SUUMO",
                "area": area["name"],
                "title": title,
                "address": address,
                "rent": rent,
                "price": price,
                "layout": layout,
                "size": size,
                "url": href,
                "date": str(datetime.now().date())
            }

            star,comment = judge(prop)

            prop["score"] = star
            prop["comment"] = comment

            results.append(prop)

        except Exception:
            continue

    return results

# ----------------------------

def collect():

    all_props=[]

    for area in AREAS:
        all_props.extend(scrape_area(area))

    return all_props

# ----------------------------

def detect_updates(properties):

    seen = load_json(SEEN_FILE)
    prices = load_json(PRICE_FILE)

    new=[]
    down=[]

    for p in properties:

        uid=p["id"]

        if uid not in seen:
            new.append(p)
            seen[uid]=True

        if uid in prices:
            if p["price"] < prices[uid]:
                old = prices[uid]
                p["old_price"]=old
                down.append(p)

        prices[uid]=p["price"]

    save_json(SEEN_FILE,seen)
    save_json(PRICE_FILE,prices)

    return new,down

# ----------------------------

if __name__=="__main__":

    props=collect()

    new,down=detect_updates(props)

    output={
        "new":new,
        "down":down
    }

    with open("output.json","w",encoding="utf8") as f:
        json.dump(output,f,ensure_ascii=False,indent=2)

    import mailer

    mailer.send()

    print(f"メール送信完了 新着:{len(new)}件")