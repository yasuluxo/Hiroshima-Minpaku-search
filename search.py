
from playwright.sync_api import sync_playwright
import json, os, re
from urllib.parse import quote
from mailer import send_report
from config import AREAS, HEADERS, SEEN_FILE, PRICE_FILE

def load(p):
    if os.path.exists(p):
        return json.load(open(p,encoding="utf8"))
    return {}
def save(p,d):
    json.dump(d,open(p,"w",encoding="utf8"),ensure_ascii=False,indent=2)
def price(t):
    m=re.search(r"([0-9.]+)",t); return float(m.group(1)) if m else 999
def score(title,layout,area,p):
    s=0; r=[]; txt=f"{title} {layout}"
    for k,v in [("戸建",3),("木造",2),("SOHO",2),("事務所可",2),("駐車場",1)]:
        if k in txt: s+=v; r.append(k)
    if p<=6: s+=2; r.append("低家賃")
    if area in ["中区","南区"]: s+=1
    s=min(s,5)
    return "★"*s+"☆"*(5-s),"・".join(r)
def scrape():
    props=[]
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True)
        page=b.new_page(user_agent=HEADERS["User-Agent"])
        for area,url in AREAS:
            page.goto(url, wait_until="networkidle")
            for i in range(page.locator(".cassetteitem").count()):
                c=page.locator(".cassetteitem").nth(i)
                try:
                    title=c.locator(".cassetteitem_content-title").inner_text().strip()
                    rent=c.locator(".cassetteitem_price--rent").first.inner_text().strip()
                    layout=c.locator(".cassetteitem_madori").first.inner_text().strip()
                    size=c.locator(".cassetteitem_menseki").first.inner_text().strip()
                    addr=c.locator(".cassetteitem_detail-col1").inner_text().strip()
                    href=c.locator(".js-cassette_link_href").first.get_attribute("href")
                    if href.startswith("/"): href="https://suumo.jp"+href
                    uid=href.split("/")[-2]
                    pval=price(rent); st,cm=score(title,layout,area,pval)
                    props.append({"id":uid,"area":area,"title":title,"rent":rent,"price":pval,
                        "layout":layout,"size":size,"address":addr,"url":href,
                        "map":"https://maps.google.com/?q="+quote(addr),"score":st,"comment":cm})
                except: pass
        b.close()
    return props
def detect(props):
    seen=load(SEEN_FILE); prices=load(PRICE_FILE); new=[]; down=[]
    for p in props:
        if p["id"] not in seen: new.append(p); seen[p["id"]]=True
        if p["id"] in prices and p["price"]<prices[p["id"]]:
            p["old_price"]=prices[p["id"]]; down.append(p)
        prices[p["id"]]=p["price"]
    save(SEEN_FILE,seen); save(PRICE_FILE,prices)
    return new,down
if __name__=="__main__":
    props=scrape(); new,down=detect(props)
    send_report({"total":len(props),"new":new,"down":down})
