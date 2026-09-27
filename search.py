
import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import mailer

# ==========================
# 広島 民泊物件ハンター Ver4
# ==========================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
    )
}

AREAS = [
    {
        "name": "中区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34101",
    },
    {
        "name": "南区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34103",
    },
    {
        "name": "西区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34104",
    },
    {
        "name": "東区",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34102",
    },
]

SEEN_FILE = "seen.json"
PRICE_FILE = "price_history.json"


# -------------------------
# JSON
# -------------------------

def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# -------------------------
# Utility
# -------------------------

def get_price(text):
    m = re.search(r"([0-9.]+)", text)
    if not m:
        return 999
    return float(m.group(1))


def get_company(title):
    m = re.search(r"(株式会社.+?|.+?不動産)", title)
    return m.group(1) if m else ""


# -------------------------
# 民泊スコア
# -------------------------

def judge(prop):
    score = 0
    reasons = []

    text = (
        prop["title"]
        + " "
        + prop["layout"]
        + " "
        + prop["address"]
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

    if prop["area"] in ["中区", "南区"]:
        score += 1
        reasons.append("人気エリア")

    score = min(score, 5)

    stars = "★" * score + "☆" * (5 - score)

    return stars, "・".join(reasons)


# -------------------------
# SUUMO取得
# -------------------------

def scrape(area):
    print("検索:", area["name"])

    r = requests.get(area["url"], headers=HEADERS, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")

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

            link = c.select_one(".js-cassette_link_href")["href"]

            if link.startswith("/"):
                link = "https://suumo.jp" + link

            uid = link.split("/")[-2]

            prop = {
                "id": uid,
                "site": "SUUMO",
                "area": area["name"],
                "title": title,
                "address": address,
                "rent": rent,
                "price": get_price(rent),
                "layout": layout,
                "size": size,
                "company": get_company(title),
                "url": link,
                "map": "https://maps.google.com/?q="
                + quote(address),
            }

            star, comment = judge(prop)

            prop["score"] = star
            prop["comment"] = comment

            results.append(prop)

        except Exception:
            continue

    return results


# -------------------------
# 全エリア取得
# -------------------------

def collect():
    properties = []

    for area in AREAS:
        properties.extend(scrape(area))

    print("取得件数:", len(properties))

    return properties


# -------------------------
# 新着・値下
# -------------------------

def detect(properties):
    seen = load_json(SEEN_FILE)
    prices = load_json(PRICE_FILE)

    new = []
    down = []

    for p in properties:
        uid = p["id"]

        if uid not in seen:
            new.append(p)
            seen[uid] = True

        if uid in prices:
            if p["price"] < prices[uid]:
                p["old_price"] = prices[uid]
                down.append(p)

        prices[uid] = p["price"]

    save_json(SEEN_FILE, seen)
    save_json(PRICE_FILE, prices)

    return new, down


# -------------------------
# Main
# -------------------------

if __name__ == "__main__":

    properties = collect()

    new, down = detect(properties)

    output = {
        "total": len(properties),
        "new": new,
        "down": down,
    }

    with open("output.json", "w", encoding="utf8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    mailer.send()

    print(f"取得:{len(properties)}件")
    print(f"新着:{len(new)}件")
    print(f"値下:{len(down)}件")
