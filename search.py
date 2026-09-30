import json, os, re, time, unicodedata
from urllib.parse import urljoin, quote
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from config import AREAS, MAX_RENT, REQUEST_TIMEOUT_MS, USER_AGENT, SEEN_FILE, PRICE_FILE, DIAG_FILE
from official_db import build_official_db, load_official_db, match_official
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
    s = re.sub(r"[【】「」『』（）()［］\[\]・,，。:：/／_-]", "", s)
    return s


def parse_rent(text):
    t = (text or "").replace(",", "").replace("，", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*万円", t)
    if m: return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d{4,7})\s*円", t)
    return int(m.group(1)) if m else None


def parse_area(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|㎡)", (text or "").replace(",", ""))
    return float(m.group(1)) if m else None


def parse_layout(text):
    m = re.search(r"(ワンルーム|[1-5][KDK]{1,3}(?:[+＋]S)?|[1-5]LDK)", text or "")
    return m.group(1) if m else ""


def extract_address(text, area):
    t = (text or "").replace("\n", " ")
    patterns = [
        rf"(広島県?広島市{re.escape(area)}[^\s,、|]+)",
        rf"(広島市{re.escape(area)}[^\s,、|]+)",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m: return m.group(1)
    return ""


def make_canonical_id(p):
    address = norm(p.get("address"))
    title = norm(p.get("title"))
    rent = p.get("rent") or 0
    layout = norm(p.get("layout"))
    size = round(float(p.get("size") or 0), 1)
    area = p.get("area", "")
    # 住所が取れればサイトをまたいで同一物件をまとめる
    if address:
        return f"addr|{address}|{rent}|{layout}|{size}|{area}"
    # 住所が取れないサイトはタイトル・家賃・間取り・面積でまとめる
    return f"text|{title}|{rent}|{layout}|{size}|{area}"


def is_detached_property(p):
    text = " ".join([p.get("title", ""), p.get("description", ""), p.get("layout", "")])
    return any(w in text for w in ["戸建", "一戸建", "貸家", "テラスハウス", "タウンハウス", "長屋"] )


def score_property(p):
    text = " ".join([p.get("title", ""), p.get("description", ""), p.get("layout", "")])
    score, reasons = 0, []
    for word, pts in [("戸建",3),("一戸建",3),("貸家",3),("テラスハウス",2),("タウンハウス",2),("木造",2),("SOHO",2),("事務所可",2),("駐車場",1),("ペット",1)]:
        if word in text:
            score += pts; reasons.append(word)
    rent = p.get("rent")
    if rent and rent <= 60000: score += 2; reasons.append("家賃6万円以下")
    elif rent and rent <= 80000: score += 1; reasons.append("家賃8万円以下")
    score = min(score, 5)
    return "★"*score + "☆"*(5-score), "・".join(dict.fromkeys(reasons))


def looks_like_property(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    return len(t) >= 25 and bool(re.search(r"万円|円", t)) and bool(re.search(r"m²|m2|㎡|DK|LDK|ワンルーム|[1-5]K", t))


def generic_extract(page, source, area):
    out, seen = [], set()
    for a in page.locator("a").all():
        try:
            href = a.get_attribute("href") or ""
            title = re.sub(r"\s+", " ", a.inner_text()).strip()
            if not href: continue
            full = urljoin(page.url, href).split("#")[0]
            if source == "suumo" and "suumo.jp" not in full: continue
            if source == "homes" and "homes.co.jp" not in full: continue
            if source == "athome" and "athome.co.jp" not in full: continue
            if "/chintai/" not in full: continue
            parent = a.locator("xpath=ancestor::*[self::article or self::li or self::div][1]")
            txt = re.sub(r"\s+", " ", parent.inner_text(timeout=2500)).strip()
            if not looks_like_property(txt): continue
            if full in seen: continue
            seen.add(full)
            out.append({"source":source.upper(),"area":area,"title":title[:120],"rent":parse_rent(txt),"layout":parse_layout(txt),"size":parse_area(txt),"address":extract_address(txt,area),"url":full,"description":txt[:900]})
        except Exception:
            pass
    return out


def scrape_suumo(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(1800)
    cards = page.locator(".cassetteitem")
    raw = cards.count()
    items=[]
    for i in range(raw):
        try:
            c=cards.nth(i); txt=re.sub(r"\s+"," ",c.inner_text()).strip()
            title=c.locator(".cassetteitem_content-title").first.inner_text().strip()
            renttxt=c.locator(".cassetteitem_price--rent").first.inner_text().strip()
            layout=c.locator(".cassetteitem_madori").first.inner_text().strip()
            sizetxt=c.locator(".cassetteitem_menseki").first.inner_text().strip()
            addr=c.locator(".cassetteitem_detail-col1").first.inner_text().strip()
            href=c.locator("a").first.get_attribute("href") or ""
            items.append({"source":"SUUMO","area":area,"title":title,"rent":parse_rent(renttxt),"layout":layout,"size":parse_area(sizetxt),"address":addr,"url":urljoin(page.url,href),"description":txt})
        except Exception: pass
    if not items: items=generic_extract(page,"suumo",area)
    return items, raw


def scrape_homes(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(1800)
    items=[]
    # HOME'Sは現在ページ上に15件表示されることがあるため、article/liだけに依存しない
    for a in page.locator("a").all():
        try:
            href=a.get_attribute("href") or ""
            full=urljoin(page.url,href).split("#")[0]
            if "homes.co.jp" not in full or "/chintai/" not in full: continue
            parent=a.locator("xpath=ancestor::*[self::article or self::li or self::div][1]")
            txt=re.sub(r"\s+"," ",parent.inner_text(timeout=2000)).strip()
            if not looks_like_property(txt): continue
            title=re.sub(r"\s+"," ",a.inner_text()).strip() or txt[:100]
            items.append({"source":"HOME'S","area":area,"title":title[:120],"rent":parse_rent(txt),"layout":parse_layout(txt),"size":parse_area(txt),"address":extract_address(txt,area),"url":full,"description":txt[:900]})
        except Exception: pass
    return items, len(items)


def scrape_athome(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(1800)
    items=generic_extract(page,"athome",area)
    return items, len(items)


def dedupe(items):
    # 同じ物件が複数サイト・複数URLで出ても、住所/条件が一致すれば1件にする。
    groups={}
    for p in items:
        p["id"] = make_canonical_id(p)
        groups.setdefault(p["id"], []).append(p)
    out=[]
    for cid, group in groups.items():
        # 情報量の多いものを代表にする。元URLはsource_urlsに残す。
        group.sort(key=lambda x: (bool(x.get("address")), len(x.get("description", "")), x.get("source", "")), reverse=True)
        p=group[0].copy()
        p["source_urls"]=[{"source":x.get("source"),"url":x.get("url")} for x in group if x.get("url")]
        p["duplicate_count"]=len(group)
        out.append(p)
    return out


def filter_candidates(items, official_db):
    out=[]
    for p in items:
        if p.get("rent") is None or p["rent"] > MAX_RENT: continue
        official_matches = match_official(p, official_db)
        detached = is_detached_property(p)
        # 第8弾の方針：戸建て系は従来通り広く検索。
        # アパート・マンション等は、広島市の公式一覧に登録された
        # 建物・住所と一致した候補だけを残す。
        if not detached and not official_matches:
            continue
        p["property_kind"] = "戸建て系" if detached else "公式一覧一致（集合住宅等）"
        p["official_matches"] = official_matches
        p["official_match_count"] = len(official_matches)
        p["official_types"] = sorted(set(x.get("type", "") for x in official_matches))
        p["score"],p["comment"]=score_property(p)
        if official_matches:
            p["score"] = "★"*min(5, max(3, sum(1 for c in p.get("score","") if c=="★") + 2)) + "☆"*max(0, 5-min(5, max(3, sum(1 for c in p.get("score","") if c=="★") + 2)))
            levels = [x.get("match_level", "") for x in official_matches]
            best_level = "部屋番号一致" if "部屋番号一致" in levels else ("住所一致" if "住所一致" in levels else "建物名一致")
            p["official_match_level"] = best_level
            comment = {
                "部屋番号一致": "広島市公式一覧：部屋番号まで一致",
                "住所一致": "広島市公式一覧：住所一致（同一建物候補）",
                "建物名一致": "広島市公式一覧：建物名一致（同一建物候補）",
            }.get(best_level, "広島市公式一覧に一致")
            p["comment"] = (p.get("comment","") + "・" if p.get("comment") else "") + comment
        p["map"]="https://www.google.com/maps/search/?api=1&query="+quote(p.get("address") or f"{p.get('area','')} {p.get('title','')}")
        out.append(p)
    # 民泊向き加点→家賃安い順
    out.sort(key=lambda x: (-sum(1 for c in x.get("score","") if c=="★"), x.get("rent") or 999999, x.get("area","")))
    return out


def detect_changes(candidates):
    seen=load_json(SEEN_FILE,{})
    prices=load_json(PRICE_FILE,{})
    new_items=[]; price_down=[]
    for p in candidates:
        cid=p["id"]
        old=prices.get(cid)
        # 第6/7弾の旧URLキーも確認して、移行時の全件新着化を防ぐ
        old_url_seen = bool(p.get("url") and p["url"] in seen)
        if cid not in seen and not old_url_seen:
            p["status"]="新着"; new_items.append(p)
        if old is not None and p.get("rent") is not None and p["rent"] < old:
            p["old_price"]=old; p["status"]="値下げ"; price_down.append(p)
        seen[cid]=True
        if p.get("url"): seen[p["url"]]=True
        if p.get("rent") is not None: prices[cid]=p["rent"]
    save_json(SEEN_FILE,seen); save_json(PRICE_FILE,prices)
    return new_items,price_down


def main():
    diagnostics=[]; raw_items=[]
    official_db = build_official_db()
    for od in official_db.get("diagnostics", []):
        print(json.dumps({"official_source": od}, ensure_ascii=False))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context(user_agent=USER_AGENT,locale="ja-JP")
        for area,sources in AREAS.items():
            for source,url in sources.items():
                started=time.time(); rec={"area":area,"source":source,"url":url,"status":"unknown","raw":0,"parsed":0,"error":"","page_title":"","body_chars":0,"attempts":0}
                for attempt in range(2):
                    page=context.new_page()
                    try:
                        if attempt:
                            time.sleep(1.2)
                        rec["attempts"] = attempt + 1
                        if source=="suumo": items,raw=scrape_suumo(page,area,url)
                        elif source=="homes": items,raw=scrape_homes(page,area,url)
                        else: items,raw=scrape_athome(page,area,url)
                        rec["raw"]=raw; rec["parsed"]=len(items); rec["page_title"]=page.title()[:200]; rec["body_chars"]=len(page.locator("body").inner_text(timeout=5000))
                        rec["status"]="OK" if items else "RETRIEVAL_ZERO"
                        if items:
                            raw_items.extend(items)
                            break
                    except PlaywrightTimeoutError as e:
                        rec["status"]="TIMEOUT"; rec["error"]=str(e)[:400]
                    except Exception as e:
                        rec["status"]="ERROR"; rec["error"]=repr(e)[:500]
                    finally:
                        try: page.close()
                        except Exception: pass
                rec["seconds"]=round(time.time()-started,1)
                if rec["status"]=="RETRIEVAL_ZERO" and rec["body_chars"] < 1000:
                    rec["hint"]="ページ本文が短く、ブロック/リダイレクト等の可能性"
                diagnostics.append(rec); print(json.dumps(rec,ensure_ascii=False))
        browser.close()
    unique=dedupe(raw_items)
    candidates=filter_candidates(unique, official_db)
    new_items,price_down=detect_changes(candidates)
    report={"total_raw":len(raw_items),"total_retrieved":len(unique),"candidates":len(candidates),"new":new_items,"price_down":price_down,"sample":candidates[:20],"diagnostics":diagnostics,"official_db":official_db,"official_match_levels":{k:sum(1 for p in candidates if p.get("official_match_level")==k) for k in ["部屋番号一致","住所一致","建物名一致"]}}
    save_json(DIAG_FILE,report); send_report(report)

if __name__=="__main__": main()
