import os
import json
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
import smtplib

# ===== メール設定 =====
EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
EMAIL_TO = os.environ["EMAIL_TO"]

# ===== SUUMO検索URL =====
URLS = [
    ("中区", "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34101"),
    ("南区", "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34103"),
    ("西区", "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34104"),
    ("東区", "https://suumo.jp/jj/chintai/ichiran/FR301FC005/?ar=080&bs=040&ta=34&sc=34102"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

STATE_FILE = "seen.json"

# ------------------------

def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf8") as f:
            return set(json.load(f))
    return set()

def save_seen(data):
    with open(STATE_FILE, "w", encoding="utf8") as f:
        json.dump(list(data), f, ensure_ascii=False)

def scrape():
    results = []

    for area, url in URLS:
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "lxml")

        cards = soup.select(".cassetteitem")[:20]

        for c in cards:
            try:
                title = c.select_one(".cassetteitem_content-title").text.strip()

                rent = c.select(".cassetteitem_price--rent")[0].text.strip()

                layout = c.select(".cassetteitem_madori")[0].text.strip()

                size = c.select(".cassetteitem_menseki")[0].text.strip()

                address = c.select_one(".cassetteitem_detail-col1").text.strip()

                href = c.select_one(".js-cassette_link_href")["href"]

                if href.startswith("/"):
                    href = "https://suumo.jp" + href

                uid = href.split("/")[-2]

                results.append({
                    "id": uid,
                    "area": area,
                    "title": title,
                    "rent": rent,
                    "layout": layout,
                    "size": size,
                    "address": address,
                    "url": href
                })
            except:
                pass

    return results

def score(p):
    s = 0

    try:
        price = int(p["rent"].replace("万円","").replace(".",""))
    except:
        price = 99

    if "1LDK" in p["layout"] or "2DK" in p["layout"]:
        s += 2

    if "RC" in p["title"]:
        s += 1

    if price <= 7:
        s += 2

    if "中区" in p["area"] or "南区" in p["area"]:
        s += 1

    return "★"*s + "☆"*(5-s)

def send_mail(items):

    body = "【広島 民泊候補 新着】\n\n"

    for i,p in enumerate(items,1):

        body += f"""■ {i}. {p['title']}
エリア：{p['area']}
家賃：{p['rent']}
間取り：{p['layout']}
面積：{p['size']}
住所：{p['address']}
民泊適性：{score(p)}

{p['url']}

------------------------

"""

    msg = MIMEText(body,"plain","utf8")
    msg["Subject"] = f"広島 民泊候補 {len(items)}件"
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_USER,EMAIL_PASS)
        smtp.send_message(msg)

def main():

    seen = load_seen()

    props = scrape()

    new = []

    for p in props:
        if p["id"] not in seen:
            new.append(p)
            seen.add(p["id"])

    save_seen(seen)

    if new:
        send_mail(new[:10])

if __name__ == "__main__":
    main()
