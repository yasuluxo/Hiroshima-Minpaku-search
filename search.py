import json
import re
import time
import html as html_lib
import unicodedata
from urllib.parse import urljoin, quote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import AREAS, MAX_RENT, REQUEST_TIMEOUT_MS, USER_AGENT, SEEN_FILE, PRICE_FILE, DIAG_FILE, CANDIDATE_FILE
from official_db import build_official_db, match_official
from mailer import send_report


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[【】「」『』（）()［］\[\]・,，。:：/／_\\-]", "", s)
    return s


def clean_text(s):
    s = html_lib.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_title(s):
    t = clean_text(s)
    if not t or len(t) > 120:
        return ""
    if re.search(r"javascript:|onclick=|class=|https?://", t, re.I):
        return ""
    bad = ["最近見た物件", "お気に入り", "採用情報", "プライバシー", "ログイン", "検索条件", "相場情報", "人気物件ランキング", "アットホームアプリ", "不動産・住宅情報サイト"]
    if any(x in t for x in bad):
        return ""
    return re.sub(r"\s+", " ", t).strip(" -|｜")


def parse_rent(text):
    t = clean_text(text).replace(",", "").replace("，", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万円", t)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d{4,7})\s*円", t)
    return int(m.group(1)) if m else None


def parse_area(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|㎡)", clean_text(text).replace(",", ""))
    return float(m.group(1)) if m else None


def parse_layout(text):
    t = clean_text(text)
    m = re.search(r"(ワンルーム|[1-5](?:LDK|DK|K|LK)(?:[+＋]S)?|[1-5]S?LDK)", t)
    return m.group(1) if m else ""


def extract_address(text, area):
    t = clean_text(text)
    # Prefer an address with a block/lot/number. Town-only strings are not enough
    # for official matching, but are retained as a weak listing address.
    patterns = [
        r"(広島県?広島市(?:中|東|南|西)区[^\s,、|]+)",
        r"(広島市(?:中|東|南|西)区[^\s,、|]+)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, t):
            value = m.group(1).strip(" -")
            if area in value and re.search(r"\d+番|\d+号|\d+-\d+|\d+丁目", value):
                return value
    # Some sites print the ward/town without the city prefix.
    m = re.search(rf"({re.escape(area)}[^\s,、|]+)", t)
    if m:
        return m.group(1).strip(" -")
    return ""


def make_canonical_id(p):
    address = norm(p.get("address"))
    title = norm(p.get("title"))
    rent = p.get("rent") or 0
    layout = norm(p.get("layout"))
    size = round(float(p.get("size") or 0), 1)
    area = p.get("area", "")
    if address:
        return f"addr|{address}|{rent}|{layout}|{size}|{area}"
    return f"text|{title}|{rent}|{layout}|{size}|{area}"


def classify_kind(text):
    t = clean_text(text)
    if re.search(r"賃貸一戸建て|一戸建て|一戸建|貸家", t):
        return "戸建て系"
    if re.search(r"テラスハウス|タウンハウス|長屋", t):
        return "戸建て系"
    if re.search(r"賃貸アパート|アパート", t):
        return "集合住宅"
    if re.search(r"賃貸マンション|マンション", t):
        return "集合住宅"
    if re.search(r"メゾネット", t):
        return "集合住宅"
    return "不明"


def is_detached_property(p):
    return p.get("property_kind") == "戸建て系"


def score_property(p):
    text = " ".join([p.get("title", ""), p.get("description", ""), p.get("property_label", "")])
    score, reasons = 0, []
    for word, pts in [("戸建", 3), ("一戸建", 3), ("貸家", 3), ("テラスハウス", 2), ("タウンハウス", 2), ("木造", 2), ("SOHO", 2), ("事務所可", 2), ("駐車場", 1), ("ペット", 1)]:
        if word in text:
            score += pts
            reasons.append(word)
    rent = p.get("rent")
    if rent and rent <= 60000:
        score += 2; reasons.append("家賃6万円以下")
    elif rent and rent <= 80000:
        score += 1; reasons.append("家賃8万円以下")
    score = min(score, 5)
    return "★" * score + "☆" * (5 - score), "・".join(dict.fromkeys(reasons))


def looks_like_property(text):
    t = clean_text(text)
    return len(t) >= 20 and bool(parse_rent(t)) and bool(parse_area(t)) and bool(parse_layout(t) or re.search(r"賃貸(?:一戸建て|アパート|マンション|テラスハウス|タウンハウス)", t))


def valid_property_url(source, full):
    if not full or full.startswith("javascript:"):
        return False
    low = full.lower()
    if source == "suumo":
        return "suumo.jp" in low and "/chintai/" in low and not any(x in low for x in ["/search/", "?from=", "/sc_"])
    if source == "homes":
        return "homes.co.jp/chintai/room/" in low
    if source == "athome":
        # AtHome property detail URLs are numeric under /chintai/. Navigation pages
        # contain words such as souba/ranking/mypage and are rejected.
        return ("athome.co.jp/chintai/" in low and bool(re.search(r"/chintai/\d{7,}/", low)) and not any(x in low for x in ["souba", "ranking", "service", "app", "search", "mypage"]))
    return False


def title_from_card(text, anchor_text=""):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    bad_re = re.compile(r"(?:お気に入り|追加|詳細を見る|お問い合わせ|無料|築\d+年|地上\d+階|\d+階|徒歩|歩\d+分|敷金|礼金|管理費|共益費|万円|円$|m²|m2|㎡|間取り|動画|パノラマ|入力|チェック|この物件)")
    # A line immediately following a property-type label is usually the building name.
    for i, line in enumerate(lines):
        if re.fullmatch(r"(?:賃貸)?(?:一戸建て|アパート|マンション|テラスハウス|タウンハウス)", line):
            for nxt in lines[i+1:i+4]:
                if not bad_re.search(nxt) and clean_title(nxt):
                    return clean_title(nxt)
    # Otherwise choose a short non-UI line containing Japanese text.
    for line in lines[:20]:
        if clean_title(line) and not bad_re.search(line) and not re.search(r"^(広島県|広島市|中区|東区|南区|西区)", line):
            if 2 <= len(line) <= 70:
                return clean_title(line)
    return clean_title(anchor_text)


def make_item(source, area, title, txt, full, property_label=""):
    title = clean_title(title)
    return {
        "source": source,
        "area": area,
        "title": title,
        "rent": parse_rent(txt),
        "layout": parse_layout(txt),
        "size": parse_area(txt),
        "address": extract_address(txt, area),
        "url": full,
        "description": clean_text(txt)[:1200],
        "property_label": property_label or clean_text(txt)[:120],
        "property_kind": classify_kind((property_label + " " + txt)),
    }


def scrape_suumo(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(1600)
    cards = page.locator(".cassetteitem")
    raw = cards.count(); items=[]; seen=set()
    for i in range(raw):
        try:
            c=cards.nth(i); txt=clean_text(c.inner_text())
            if not looks_like_property(txt): continue
            links=c.locator("a")
            href=""
            for j in range(min(links.count(),10)):
                h=links.nth(j).get_attribute("href") or ""
                full=urljoin(page.url,h).split("#")[0]
                if valid_property_url("suumo",full): href=full; break
            if not href or href in seen: continue
            title=clean_title(c.locator(".cassetteitem_content-title").first.inner_text()) if c.locator(".cassetteitem_content-title").count() else title_from_card(txt)
            renttxt=c.locator(".cassetteitem_price--rent").first.inner_text() if c.locator(".cassetteitem_price--rent").count() else txt
            layout=c.locator(".cassetteitem_madori").first.inner_text() if c.locator(".cassetteitem_madori").count() else parse_layout(txt)
            sizetxt=c.locator(".cassetteitem_menseki").first.inner_text() if c.locator(".cassetteitem_menseki").count() else txt
            addr=extract_address(txt,area)
            item={"source":"SUUMO","area":area,"title":title,"rent":parse_rent(renttxt),"layout":clean_text(layout),"size":parse_area(sizetxt),"address":addr,"url":href,"description":txt,"property_label":title_from_card(txt),"property_kind":classify_kind(txt)}
            if item["rent"] is None or item["size"] is None: continue
            seen.add(href); items.append(item)
        except Exception: pass
    return items, raw


def scrape_homes(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(2200)
    links=page.locator('a[href*="/chintai/room/"]')
    raw=links.count(); items=[]; seen=set()
    for i in range(raw):
        try:
            a=links.nth(i); h=a.get_attribute("href") or ""; full=urljoin(page.url,h).split("#")[0]
            if not valid_property_url("homes",full) or full in seen: continue
            card=a.locator("xpath=ancestor::li[1]")
            if card.count()==0: card=a.locator("xpath=ancestor::article[1]")
            if card.count()==0: card=a.locator("xpath=ancestor::div[1]")
            txt=clean_text(card.inner_text(timeout=2500)) if card.count() else clean_text(a.inner_text())
            # If the first ancestor is too small, climb a few levels, but never
            # beyond a bounded card-sized block.
            if not looks_like_property(txt):
                cur=a
                for _ in range(5):
                    cur=cur.locator("xpath=..")
                    t=clean_text(cur.inner_text(timeout=1500))
                    if looks_like_property(t) and len(t)<5000:
                        txt=t; break
            if not looks_like_property(txt): continue
            title=title_from_card(txt,a.inner_text())
            item=make_item("HOME'S",area,title,txt,full)
            if item["rent"] is None or item["size"] is None: continue
            seen.add(full); items.append(item)
        except Exception: pass
    return items, raw


def scrape_athome(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(2200)
    links=page.locator('a[href*="/chintai/"]')
    raw=links.count(); items=[]; seen=set()
    for i in range(raw):
        try:
            a=links.nth(i); h=a.get_attribute("href") or ""; full=urljoin(page.url,h).split("#")[0]
            if not valid_property_url("athome",full) or full in seen: continue
            cur=a; txt=""
            for _ in range(5):
                cur=cur.locator("xpath=..")
                t=clean_text(cur.inner_text(timeout=1500))
                if looks_like_property(t) and len(t)<5000:
                    txt=t; break
            if not txt: continue
            title=title_from_card(txt,a.inner_text())
            item=make_item("at home",area,title,txt,full)
            if item["rent"] is None or item["size"] is None: continue
            seen.add(full); items.append(item)
        except Exception: pass
    return items, raw


def enrich_from_detail(context, p):
    """Fetch a bounded detail page to recover full address/type/name for matching."""
    page=context.new_page()
    try:
        page.goto(p["url"],wait_until="domcontentloaded",timeout=20000)
        page.wait_for_timeout(900)
        text=clean_text(page.locator("body").inner_text(timeout=5000))
        if len(text)<100: return p
        addr=extract_address(text,p.get("area",""))
        if addr and re.search(r"\d+番|\d+号|\d+-\d+|\d+丁目",addr): p["address"]=addr
        if not p.get("layout"): p["layout"]=parse_layout(text)
        if not p.get("size"): p["size"]=parse_area(text)
        if not p.get("rent"): p["rent"]=parse_rent(text)
        label=re.search(r"(賃貸一戸建て|賃貸アパート|賃貸マンション|賃貸テラスハウス|賃貸タウンハウス)",text)
        if label: p["property_label"]=label.group(1); p["property_kind"]=classify_kind(text)
        title=title_from_card(text,p.get("title",""))
        if title and len(title)>1: p["title"]=title
        p["description"]=text[:1600]
        return p
    except Exception:
        return p
    finally:
        try: page.close()
        except Exception: pass


def dedupe(items):
    groups={}
    for p in items:
        p["id"]=make_canonical_id(p); groups.setdefault(p["id"],[]).append(p)
    out=[]
    for cid,group in groups.items():
        group.sort(key=lambda x:(bool(re.search(r"\d+番|\d+号|\d+-\d+|\d+丁目",x.get("address",''))),len(x.get("description",'')),x.get("source",'')),reverse=True)
        p=group[0].copy(); p["source_urls"]=[{"source":x.get("source"),"url":x.get("url")} for x in group if x.get("url")]; p["duplicate_count"]=len(group); out.append(p)
    return out


def filter_candidates(items, official_db):
    out=[]; db_ready=bool(official_db.get("records")) and all(x.get("status")=="OK" for x in official_db.get("diagnostics",[]))
    for p in items:
        if p.get("rent") is None or p["rent"]>MAX_RENT: continue
        # Unknown/malformed kind is not a candidate. This prevents navigation links
        # from becoming "戸建て系" merely because nearby card text had a price.
        kind=p.get("property_kind")
        if kind not in ("戸建て系","集合住宅"): continue
        p["address_quality"]="OK" if re.search(r"\d+番|\d+号|\d+-\d+|\d+丁目",p.get("address",'')) else "LOW"
        matches=[]
        if kind=="集合住宅":
            if not db_ready or p["address_quality"]!="OK": continue
            matches=match_official(p,official_db)
            if not matches: continue
        elif db_ready and p["address_quality"]=="OK":
            matches=match_official(p,official_db)
        p["official_matches"]=matches
        p["official_match_count"]=len(matches)
        p["official_types"]=sorted(set(x.get("type","") for x in matches))
        p["score"],p["comment"]=score_property(p)
        if matches:
            rank={"部屋番号一致":3,"住所一致":2,"建物名一致":1}
            p["official_match_level"]=max((x.get("match_level","") for x in matches),key=lambda x:rank.get(x,0))
            p["comment"]=(p.get("comment","")+"・" if p.get("comment") else "")+"広島市公式一覧："+p["official_match_level"]
        p["map"]="https://www.google.com/maps/search/?api=1&query="+quote(p.get("address") or f"{p.get('area','')} {p.get('title','')}")
        out.append(p)
    out.sort(key=lambda x:(-sum(c=="★" for c in x.get("score","")),x.get("rent") or 999999,x.get("area","")))
    return out


def update_history(candidates):
    hist=load_json(CANDIDATE_FILE,{})
    if isinstance(hist, dict):
        raw_records=hist.get("records", {})
        if isinstance(raw_records, dict):
            records=dict(raw_records)
        elif isinstance(raw_records, list):
            records={x.get("id"): x for x in raw_records if isinstance(x,dict) and x.get("id")}
        else:
            records={}
    elif isinstance(hist, list):
        # Backward-compatible migration for an older 8.3-style list.
        records={x.get("id"): x for x in hist if isinstance(x,dict) and x.get("id")}
    else:
        records={}
    now=time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    for p in candidates:
        old=records.get(p["id"],{})
        merged=dict(old); merged.update(p)
        merged["first_seen"]=old.get("first_seen",now); merged["last_seen"]=now
        merged["review_status"]=old.get("review_status","未確認")
        merged["review_notes"]=old.get("review_notes","")
        records[p["id"]]=merged
    hist={"version":"8.5","updated_at":now,"records":records}
    save_json(CANDIDATE_FILE,hist)
    return hist


def detect_changes(candidates):
    seen=load_json(SEEN_FILE,{}); prices=load_json(PRICE_FILE,{})
    new_items=[]; price_down=[]
    for p in candidates:
        cid=p["id"]; old=prices.get(cid)
        if cid not in seen and not (p.get("url") and p["url"] in seen): p["status"]="新着"; new_items.append(p)
        if old is not None and p.get("rent") is not None and p["rent"]<old: p["old_price"]=old; p["status"]="値下げ"; price_down.append(p)
        seen[cid]=True
        if p.get("url"): seen[p["url"]]=True
        if p.get("rent") is not None: prices[cid]=p["rent"]
    save_json(SEEN_FILE,seen); save_json(PRICE_FILE,prices)
    return new_items,price_down


def main():
    diagnostics=[]; raw_items=[]; official_db=build_official_db()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context(user_agent=USER_AGENT,locale="ja-JP")
        for area,sources in AREAS.items():
            for source,url in sources.items():
                started=time.time(); rec={"area":area,"source":source,"url":url,"status":"unknown","raw":0,"parsed":0,"error":"","body_chars":0,"attempts":0}
                for attempt in range(2):
                    page=context.new_page()
                    try:
                        rec["attempts"]=attempt+1
                        if source=="suumo": items,raw=scrape_suumo(page,area,url)
                        elif source=="homes": items,raw=scrape_homes(page,area,url)
                        else: items,raw=scrape_athome(page,area,url)
                        rec["raw"]=raw; rec["parsed"]=len(items); rec["body_chars"]=len(page.locator("body").inner_text(timeout=5000)); rec["status"]="OK" if items else "RETRIEVAL_ZERO"
                        if items: raw_items.extend(items); break
                    except PlaywrightTimeoutError as e: rec["status"]="TIMEOUT"; rec["error"]=str(e)[:400]
                    except Exception as e: rec["status"]="ERROR"; rec["error"]=repr(e)[:500]
                    finally:
                        try: page.close()
                        except Exception: pass
                rec["seconds"]=round(time.time()-started,1); diagnostics.append(rec)
        # Enrich only real property links, not navigation. Cap per source/ward to
        # keep the daily run practical while giving apartment candidates an address.
        unique_urls=[]; seen_urls=set()
        for p in raw_items:
            if p.get("url") and p["url"] not in seen_urls and len(unique_urls)<180:
                seen_urls.add(p["url"]); unique_urls.append(p["url"])
        by_url={p["url"]:p for p in raw_items}
        for u in unique_urls:
            p=by_url[u]
            if p.get("rent") is not None and p["rent"]<=MAX_RENT:
                enrich_from_detail(context,p)
        browser.close()
    unique=dedupe(raw_items)
    candidates=filter_candidates(unique,official_db)
    new_items,price_down=detect_changes(candidates)
    hist=update_history(candidates)
    report={"version":"8.5","total_raw":len(raw_items),"total_retrieved":len(unique),"candidates":len(candidates),"current":len(candidates),"new":new_items,"price_down":price_down,"all_candidates":candidates,"sample":candidates,"diagnostics":diagnostics,"official_db":official_db,"history":hist,"official_match_levels":{k:sum(1 for p in candidates if p.get("official_match_level")==k) for k in ["部屋番号一致","住所一致","建物名一致"]}}
    save_json(DIAG_FILE,report); send_report(report)

if __name__=="__main__": main()
