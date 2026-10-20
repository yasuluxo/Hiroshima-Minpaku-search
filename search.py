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

VERSION = "8.8"


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


def has_full_address(s):
    return bool(re.search(r"\d+番\d*号|\d+番|\d+号|\d+-\d+|\d+丁目\d+", s or ""))


def extract_address(text, area):
    t = clean_text(text)
    patterns = [
        r"(広島県?広島市(?:中|東|南|西)区[^\s,、|]+)",
        r"(広島市(?:中|東|南|西)区[^\s,、|]+)",
    ]
    candidates = []
    for pat in patterns:
        for m in re.finditer(pat, t):
            value = m.group(1).strip(" -")
            if area in value and has_full_address(value):
                candidates.append(value)
    if candidates:
        # Prefer the shortest complete address: generic body text often repeats it.
        return min(candidates, key=len)
    if area:
        m = re.search(rf"({re.escape(area)}[^\s,、|]+)", t)
        if m and has_full_address(m.group(1)):
            return m.group(1).strip(" -")
    return ""


def make_canonical_id(p):
    address = norm(p.get("address"))
    title = norm(p.get("title"))
    rent = p.get("rent") or 0
    layout = norm(p.get("layout"))
    size = round(float(p.get("size") or 0), 1)
    area = p.get("area", "")
    url = p.get("url", "")
    # URL is a last-resort identity. Address+rent+layout+size remains the primary key.
    if address:
        return f"addr|{address}|{rent}|{layout}|{size}|{area}"
    return f"url|{norm(url)}|{title}|{rent}|{layout}|{size}|{area}"


def classify_kind_from_signals(label="", text="", structure="", title=""):
    """Classify only from property-specific signals. Never default to detached."""
    label = clean_text(label)
    structure = clean_text(structure)
    title = clean_text(title)
    text = clean_text(text)

    # Strong explicit labels first.
    if re.search(r"賃貸(?:一戸建て|一戸建|貸家)|(?:^|[\s｜|])(?:一戸建て|一戸建|貸家)(?:$|[\s｜|])", label):
        return "戸建て系", "explicit_detached_label"
    if re.search(r"テラスハウス|タウンハウス|長屋", label):
        return "戸建て系", "explicit_townhouse_label"
    if re.search(r"賃貸(?:アパート|マンション)|(?:^|[\s｜|])(?:アパート|マンション)(?:$|[\s｜|])", label):
        return "集合住宅", "explicit_apartment_label"

    # Detail-page structure is stronger than generic page text.
    if re.search(r"\d+\s*階建", structure):
        return "集合住宅", "multi_story_structure"
    if re.search(r"(?:一戸建て|一戸建|貸家)", structure):
        return "戸建て系", "detached_structure"

    # Specific detail labels / table rows.
    if re.search(r"(?:物件種別|建物種別|種別)\s*(?:マンション|アパート)", text):
        return "集合住宅", "detail_type_row"
    if re.search(r"(?:物件種別|建物種別|種別)\s*(?:一戸建て|一戸建|貸家)", text):
        return "戸建て系", "detail_type_row"
    if re.search(r"(?:マンション|アパート)\s*\d+階建", text):
        return "集合住宅", "apartment_structure"
    if re.search(r"\d+階建.*(?:マンション|アパート)", text):
        return "集合住宅", "apartment_structure"

    # Title is useful only as a supporting signal when no contradictory detail signal exists.
    if re.search(r"(?:マンション|レジデンス|ハイツ|コーポ|パレス|ビル)$", title):
        return "集合住宅", "residential_building_title"
    if re.search(r"(?:貸家|一戸建て|一戸建)$", title):
        return "戸建て系", "detached_title"

    return "不明", "no_property_specific_signal"


def score_property(p):
    text = " ".join([p.get("title", ""), p.get("description", ""), p.get("property_label", "")])
    score, reasons = 0, []
    for word, pts in [("戸建", 3), ("一戸建", 3), ("貸家", 3), ("テラスハウス", 2), ("タウンハウス", 2), ("木造", 2), ("SOHO", 2), ("事務所可", 2), ("駐車場", 1), ("ペット", 1)]:
        if word in text:
            score += pts; reasons.append(word)
    rent = p.get("rent")
    if rent and rent <= 60000:
        score += 2; reasons.append("家賃6万円以下")
    elif rent and rent <= 80000:
        score += 1; reasons.append("家賃8万円以下")
    score = min(score, 5)
    return "★" * score + "☆" * (5 - score), "・".join(dict.fromkeys(reasons))


def looks_like_property(text):
    """Loose listing-card gate. A valid property detail URL is more important
    than having every field in the search-card text. Detail validation is the
    authoritative gate later. This prevents 8.6/8.7 from dropping most homes
    before we ever visit their detail pages."""
    t = clean_text(text)
    if len(t) < 8:
        return False
    # Keep only cards that contain at least one property-specific signal.
    return bool(
        re.search(r"\d+(?:\.\d+)?\s*(?:万円|円)", t)
        or re.search(r"\d+(?:\.\d+)?\s*(?:m²|m2|㎡)", t)
        or re.search(r"(?:ワンルーム|[1-5](?:LDK|DK|K|LK)|一戸建て|一戸建|貸家|アパート|マンション|テラスハウス|タウンハウス|長屋|メゾネット)", t)
    )


def valid_property_url(source, full):
    if not full or full.startswith("javascript:"):
        return False
    low = full.lower()
    if source == "suumo":
        return "suumo.jp" in low and "/chintai/" in low and not any(x in low for x in ["/search/", "?from=", "/sc_"])
    if source == "homes":
        return "homes.co.jp/chintai/room/" in low
    if source == "athome":
        return ("athome.co.jp/chintai/" in low and bool(re.search(r"/chintai/\d{7,}/", low)) and not any(x in low for x in ["souba", "ranking", "service", "app", "search", "mypage"]))
    return False


def safe_title(s):
    t = clean_text(s)
    if not t or len(t) > 120:
        return ""
    if re.search(r"javascript:|onclick=|class=|https?://", t, re.I):
        return ""
    bad = ["最近見た物件", "お気に入り", "採用情報", "プライバシー", "ログイン", "検索条件", "相場情報", "人気物件ランキング", "アットホームアプリ", "不動産・住宅情報サイト"]
    if any(x in t for x in bad):
        return ""
    return re.sub(r"\s+", " ", t).strip(" -|｜")


def title_from_card(text, anchor_text=""):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    bad_re = re.compile(r"(?:お気に入り|追加|詳細を見る|お問い合わせ|無料|築\d+年|地上\d+階|\d+階|徒歩|歩\d+分|敷金|礼金|管理費|共益費|万円|円$|m²|m2|㎡|間取り|動画|パノラマ|入力|チェック|この物件)")
    for i, line in enumerate(lines):
        if re.fullmatch(r"(?:賃貸)?(?:一戸建て|アパート|マンション|テラスハウス|タウンハウス)", line):
            for nxt in lines[i+1:i+4]:
                if not bad_re.search(nxt) and safe_title(nxt):
                    return safe_title(nxt)
    for line in lines[:25]:
        if safe_title(line) and not bad_re.search(line) and not re.search(r"^(広島県|広島市|中区|東区|南区|西区)", line):
            if 2 <= len(line) <= 70:
                return safe_title(line)
    return safe_title(anchor_text)


def make_item(source, area, title, txt, full, property_label=""):
    label = clean_text(property_label)
    kind, reason = classify_kind_from_signals(label, txt, "", title)
    return {
        "source": source,
        "area": area,
        "title": safe_title(title),
        "rent": parse_rent(txt),
        "layout": parse_layout(txt),
        "size": parse_area(txt),
        "address": extract_address(txt, area),
        "url": full,
        "description": clean_text(txt)[:1200],
        "property_label": label,
        "property_kind": kind,
        "classification_reason": reason,
    }


def scrape_suumo(page, area, url):
    page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(1600)
    cards = page.locator(".cassetteitem")
    raw = cards.count(); items=[]; seen=set()
    for i in range(raw):
        try:
            c=cards.nth(i); txt=clean_text(c.inner_text())
            if not looks_like_property(txt):
                # Keep the card if it contains a real SUUMO detail URL; field extraction is deferred.
                pass
            links=c.locator("a"); href=""
            for j in range(min(links.count(),15)):
                h=links.nth(j).get_attribute("href") or ""
                full=urljoin(page.url,h).split("#")[0]
                if valid_property_url("suumo",full): href=full; break
            if not href or href in seen: continue
            title = safe_title(c.locator(".cassetteitem_content-title").first.inner_text()) if c.locator(".cassetteitem_content-title").count() else title_from_card(txt)
            renttxt=c.locator(".cassetteitem_price--rent").first.inner_text() if c.locator(".cassetteitem_price--rent").count() else txt
            layout=c.locator(".cassetteitem_madori").first.inner_text() if c.locator(".cassetteitem_madori").count() else parse_layout(txt)
            sizetxt=c.locator(".cassetteitem_menseki").first.inner_text() if c.locator(".cassetteitem_menseki").count() else txt
            addr=extract_address(txt,area)
            label = title_from_card(txt)
            kind, reason = classify_kind_from_signals(label, txt, "", title)
            item={"source":"SUUMO","area":area,"title":title,"rent":parse_rent(renttxt),"layout":clean_text(layout),"size":parse_area(sizetxt),"address":addr,"url":href,"description":txt,"property_label":label,"property_kind":kind,"classification_reason":reason}
            seen.add(href); items.append(item)
        except Exception:
            pass
    return items, raw


def bounded_card(a, max_levels=7, max_chars=5000):
    """Return the smallest useful property-card ancestor.
    More levels than 8.6 are allowed, but we stop once a self-contained card
    contains rent + area + layout. This avoids borrowing data from sibling cards.
    """
    cur=a
    best=""
    for _ in range(max_levels):
        cur=cur.locator("xpath=..")
        try:
            t=clean_text(cur.inner_text(timeout=1500))
        except Exception:
            t=""
        if not t or len(t)>max_chars:
            continue
        if len(t)>len(best): best=t
        if looks_like_property(t):
            return t
    return best


def page_variants(page, base_url, max_pages=5):
    """Discover pagination URLs without inventing site-specific URL formats."""
    urls=[]; seen=set()
    def add(u):
        u=urljoin(base_url,u).split('#')[0]
        if not u or u in seen: return
        low=u.lower()
        if any(x in low for x in ['/login','/mypage','/search/','/ranking','/souba','/app/']): return
        seen.add(u); urls.append(u)
    add(base_url)
    for _ in range(max_pages-1):
        found=[]
        try:
            links=page.locator('a')
            for i in range(links.count()):
                a=links.nth(i)
                txt=clean_text(a.inner_text())
                href=a.get_attribute('href') or ''
                if not href: continue
                if re.search(r'(次へ|次のページ|次ページ|›|»)',txt): found.append(urljoin(page.url,href))
        except Exception:
            pass
        nxt=None
        for u in found:
            if u not in seen: nxt=u; break
        if not nxt: break
        add(nxt)
        try:
            page.goto(nxt,wait_until='domcontentloaded',timeout=REQUEST_TIMEOUT_MS)
            page.wait_for_timeout(1200)
        except Exception:
            break
    return urls


def collect_hrefs_from_listing(page, source):
    selector = {
        'homes':'a[href*="/chintai/room/"]',
        'athome':'a[href*="/chintai/"]',
    }[source]
    try: return page.locator(selector).count()
    except Exception: return 0


def scrape_homes(page, area, url):
    items=[]; seen=set(); raw_total=0
    current=url
    for page_no in range(1,6):
        try:
            page.goto(current,wait_until='domcontentloaded',timeout=REQUEST_TIMEOUT_MS)
            page.wait_for_timeout(1800)
            links=page.locator('a[href*="/chintai/room/"]')
            raw=links.count(); raw_total+=raw
            for i in range(raw):
                try:
                    a=links.nth(i); h=a.get_attribute('href') or ''; full=urljoin(page.url,h).split('#')[0]
                    if not valid_property_url('homes',full) or full in seen: continue
                    txt=bounded_card(a)
                    if not looks_like_property(txt):
                        # A detail URL itself is enough to defer validation to the detail page.
                        txt = clean_text(txt or a.inner_text())
                    title=title_from_card(txt,a.inner_text())
                    item=make_item("HOME'S",area,title,txt,full)
                    seen.add(full); items.append(item)
                except Exception: pass
            nxt=None
            for j in range(page.locator('a').count()):
                try:
                    a=page.locator('a').nth(j); txt=clean_text(a.inner_text()); href=a.get_attribute('href') or ''
                    if re.search(r'(次へ|次のページ|次ページ|›|»)',txt) and href:
                        u=urljoin(page.url,href).split('#')[0]
                        if u not in {x.get('url') for x in items}: nxt=u; break
                except Exception: pass
            if not nxt or nxt==current: break
            current=nxt
        except Exception: break
    return items, raw_total


def scrape_athome(page, area, url):
    items=[]; seen=set(); raw_total=0; current=url
    for page_no in range(1,6):
        try:
            page.goto(current,wait_until='domcontentloaded',timeout=REQUEST_TIMEOUT_MS)
            page.wait_for_timeout(1800)
            links=page.locator('a[href*="/chintai/"]')
            raw=links.count(); raw_total+=raw
            for i in range(raw):
                try:
                    a=links.nth(i); h=a.get_attribute('href') or ''; full=urljoin(page.url,h).split('#')[0]
                    if not valid_property_url('athome',full) or full in seen: continue
                    txt=bounded_card(a)
                    if not looks_like_property(txt):
                        # A detail URL itself is enough to defer validation to the detail page.
                        txt = clean_text(txt or a.inner_text())
                    title=title_from_card(txt,a.inner_text())
                    item=make_item('at home',area,title,txt,full)
                    seen.add(full); items.append(item)
                except Exception: pass
            nxt=None
            for j in range(page.locator('a').count()):
                try:
                    a=page.locator('a').nth(j); txt=clean_text(a.inner_text()); href=a.get_attribute('href') or ''
                    if re.search(r'(次へ|次のページ|次ページ|›|»)',txt) and href:
                        u=urljoin(page.url,href).split('#')[0]
                        if u!=current: nxt=u; break
                except Exception: pass
            if not nxt: break
            current=nxt
        except Exception: break
    return items, raw_total


def jsonld_objects(page):
    objects=[]
    try:
        scripts=page.locator('script[type="application/ld+json"]')
        for i in range(scripts.count()):
            raw=scripts.nth(i).inner_text(timeout=1500)
            try:
                data=json.loads(raw)
                if isinstance(data,list): objects.extend(data)
                elif isinstance(data,dict) and isinstance(data.get("@graph"),list): objects.extend(data["@graph"])
                elif isinstance(data,dict): objects.append(data)
            except Exception: pass
    except Exception: pass
    return objects


def jsonld_value(obj, key):
    v=obj.get(key) if isinstance(obj,dict) else None
    if isinstance(v,dict):
        return v.get("name") or v.get("value") or v.get("@value") or ""
    return v or ""


def extract_detail_structured(page, area):
    """Extract only property-detail signals; generic search-page UI is ignored."""
    result={"title":"","address":"","rent":None,"size":None,"layout":"","property_label":"","structure":"","detail_text":""}
    objs=jsonld_objects(page)
    for obj in objs:
        typ=obj.get("@type") if isinstance(obj,dict) else ""
        if isinstance(typ,list): typ=" ".join(map(str,typ))
        if not any(x in str(typ).lower() for x in ["product","residence","apartment","house","offer"]):
            continue
        name=jsonld_value(obj,"name")
        if name and not result["title"]: result["title"]=safe_title(str(name))
        addr=obj.get("address") if isinstance(obj,dict) else None
        if isinstance(addr,dict):
            parts=[addr.get("addressRegion"),addr.get("addressLocality"),addr.get("streetAddress")]
            a="".join(str(x) for x in parts if x)
            if area in a and has_full_address(a): result["address"]=a
        offers=obj.get("offers") if isinstance(obj,dict) else None
        if isinstance(offers,dict):
            price=offers.get("price")
            if price:
                try: result["rent"]=int(float(str(price).replace(",","")))
                except Exception: pass
        floor=obj.get("floorSize") if isinstance(obj,dict) else None
        if isinstance(floor,dict):
            v=floor.get("value")
            if v:
                try: result["size"]=float(v)
                except Exception: pass
    # Meta tags are less authoritative than JSON-LD, but much safer than whole-body title guessing.
    for selector, key in [('meta[property="og:title"]',"title"),('meta[name="description"]',"detail_text")]:
        try:
            el=page.locator(selector).first
            if el.count():
                val=clean_text(el.get_attribute("content") or "")
                if key=="title" and val and not result["title"]: result["title"]=safe_title(val)
                if key=="detail_text" and val: result[key]=val
        except Exception: pass

    try:
        body=clean_text(page.locator("body").inner_text(timeout=5000))
    except Exception:
        body=""
    result["detail_text"]=(result.get("detail_text")+" "+body[:12000]).strip()

    if not result["address"]:
        result["address"]=extract_address(body,area)
    if result["rent"] is None: result["rent"]=parse_rent(body)
    if result["size"] is None: result["size"]=parse_area(body)
    if not result["layout"]: result["layout"]=parse_layout(body)

    # Prefer labels close to the property-type field. Avoid generic navigation phrases.
    label_patterns=[
        r"(?:物件種別|建物種別|種別)\s*[:：]?\s*(賃貸一戸建て|一戸建て|一戸建|貸家|賃貸アパート|アパート|賃貸マンション|マンション|テラスハウス|タウンハウス|長屋)",
        r"(賃貸一戸建て|賃貸アパート|賃貸マンション|賃貸テラスハウス|賃貸タウンハウス)\s+(?:[\d,]+円|\d+(?:\.\d+)?万円)",
    ]
    for pat in label_patterns:
        m=re.search(pat,body)
        if m:
            result["property_label"]=m.group(1); break
    if not result["property_label"]:
        # Common site-specific phrases that identify the actual listing type.
        m=re.search(r"(?:賃貸|建物)\s*(一戸建て|一戸建|貸家|テラスハウス|タウンハウス|長屋|マンション|アパート)",body)
        if m: result["property_label"]=m.group(1)
    if not result["property_label"]:
        m=re.search(r"(一戸建て|一戸建|貸家|テラスハウス|タウンハウス|長屋|マンション|アパート)\s*(?:の)?\s*賃貸",body)
        if m: result["property_label"]=m.group(1)
    if not result["property_label"]:
        # Search a compact window around floor-count / structure terms.
        m=re.search(r"([^。\n]{0,80}(?:\d+階建|一戸建て|一戸建|貸家|マンション|アパート)[^。\n]{0,80})",body)
        if m: result["structure"]=m.group(1)
    return result


def enrich_from_detail(context, p):
    page=context.new_page()
    try:
        page.goto(p["url"],wait_until="domcontentloaded",timeout=20000)
        page.wait_for_timeout(900)
        d=extract_detail_structured(page,p.get("area",""))
        if d.get("address") and has_full_address(d["address"]): p["address"]=d["address"]
        if d.get("layout"): p["layout"]=d["layout"]
        if d.get("size") is not None: p["size"]=d["size"]
        if d.get("rent") is not None: p["rent"]=d["rent"]
        if d.get("title") and len(d["title"])<100: p["title"]=d["title"]
        p["detail_text"]=d.get("detail_text","")[:12000]
        p["property_label"]=d.get("property_label") or p.get("property_label","")
        # Structure comes from the detail page only. This is intentionally strict.
        kind, reason=classify_kind_from_signals(
            p.get("property_label","") or d.get("property_label",""), d.get("detail_text",""), d.get("structure",""), p.get("title","")
        )
        p["property_kind"]=kind
        p["classification_reason"]=reason
        p["detail_validated"]=kind in ("戸建て系","集合住宅") and bool(d.get("address"))
        p["detail_source"]=p.get("url")
        return p
    except Exception as e:
        p["detail_validated"]=False
        p["detail_error"]=repr(e)[:300]
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
        # Prefer the fully detail-validated record, then the one with a complete address.
        group.sort(key=lambda x:(x.get("detail_validated",False),has_full_address(x.get("address","")),bool(x.get("property_kind") in ("戸建て系","集合住宅")),len(x.get("description",''))),reverse=True)
        p=group[0].copy()
        p["source_urls"]=[]
        seen_urls=set()
        for x in group:
            if x.get("url") and x["url"] not in seen_urls:
                p["source_urls"].append({"source":x.get("source"),"url":x.get("url")}); seen_urls.add(x["url"])
        p["duplicate_count"]=len(group)
        out.append(p)
    return out


def filter_candidates(items, official_db):
    out=[]
    db_ready=bool(official_db.get("records")) and all(x.get("status")=="OK" for x in official_db.get("diagnostics",[]))
    for p in items:
        if p.get("rent") is None or p["rent"]>MAX_RENT: continue
        # 8.6 hard rule: every candidate must be classified on its own detail page.
        if not p.get("detail_validated"): continue
        kind=p.get("property_kind")
        if kind not in ("戸建て系","集合住宅"): continue
        p["address_quality"]="OK" if has_full_address(p.get("address",'')) else "LOW"
        matches=[]
        if kind=="集合住宅":
            # Apartments/condos are never allowed through without a current official match.
            if not db_ready or p["address_quality"]!="OK": continue
            matches=match_official(p,official_db)
            if not matches: continue
        else:
            if db_ready and p["address_quality"]=="OK": matches=match_official(p,official_db)
        p["official_matches"]=matches
        p["official_match_count"]=len(matches)
        p["official_types"]=sorted(set(x.get("type","") for x in matches))
        p["score"],p["comment"]=score_property(p)
        if matches:
            rank={"部屋番号一致":3,"住所一致":2,"建物名一致":1}
            p["official_match_level"]=max((x.get("match_level","") for x in matches),key=lambda x:rank.get(x,0))
            p["comment"]=(p.get("comment","")+"・" if p.get("comment") else "")+"広島市公式一覧："+p["official_match_level"]
        else:
            p["official_match_level"]="対象外（戸建て系）" if kind=="戸建て系" else "不一致"
        p["validation_status"]="valid"
        p["source_version"]=VERSION
        p["map"]="https://www.google.com/maps/search/?api=1&query="+quote(p.get("address") or f"{p.get('area','')} {p.get('title','')}")
        out.append(p)
    out.sort(key=lambda x:(-sum(c=="★" for c in x.get("score","")),x.get("rent") or 999999,x.get("area",""),x.get("title","")))
    return out


def update_history(candidates):
    hist=load_json(CANDIDATE_FILE,{})
    if isinstance(hist,dict):
        raw_records=hist.get("records",{})
        if isinstance(raw_records,dict): records=dict(raw_records)
        elif isinstance(raw_records,list): records={x.get("id"):x for x in raw_records if isinstance(x,dict) and x.get("id")}
        else: records={}
    elif isinstance(hist,list): records={x.get("id"):x for x in hist if isinstance(x,dict) and x.get("id")}
    else: records={}
    now=time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    for p in candidates:
        old=records.get(p["id"],{})
        merged=dict(old); merged.update(p)
        merged["first_seen"]=old.get("first_seen",now); merged["last_seen"]=now
        merged["review_status"]=old.get("review_status","未確認")
        merged["review_notes"]=old.get("review_notes","")
        records[p["id"]]=merged
    hist={"version":VERSION,"updated_at":now,"records":records}
    save_json(CANDIDATE_FILE,hist)
    return hist


def valid_history_records(hist):
    records=hist.get("records",{}) if isinstance(hist,dict) else {}
    if not isinstance(records,dict): return []
    return [x for x in records.values() if isinstance(x,dict) and x.get("validation_status")=="valid"]


def detect_changes(candidates):
    seen=load_json(SEEN_FILE,{}); prices=load_json(PRICE_FILE,{})
    if not isinstance(seen,dict): seen={}
    if not isinstance(prices,dict): prices={}
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
                started=time.time(); rec={"area":area,"source":source,"url":url,"status":"unknown","raw":0,"parsed":0,"detail_validated":0,"error":"","body_chars":0,"attempts":0}
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
        # 8.8: visit real property detail URLs first, then decide rent/type.
        # This separates retrieval from eligibility and exposes where candidates disappear.
        unique_urls=[]; seen_urls=set()
        for p in raw_items:
            if p.get("url") and p["url"] not in seen_urls:
                seen_urls.add(p["url"]); unique_urls.append(p["url"])
        by_url={p["url"]:p for p in raw_items}
        for u in unique_urls:
            enrich_from_detail(context,by_url[u])
        for rec in diagnostics:
            source_key=re.sub(r"\s+", "", rec["source"].lower().replace("'", ""))
            src_items=[p for p in raw_items if re.sub(r"\s+", "", p.get("source","").lower().replace("'", "")) in source_key]
            rec["detail_attempted"]=len(src_items)
            rec["detail_validated"]=sum(1 for p in src_items if p.get("detail_validated"))
            rec["under_rent_cap"]=sum(1 for p in src_items if p.get("rent") is not None and p.get("rent")<=MAX_RENT)
            rec["detached"]=sum(1 for p in src_items if p.get("property_kind")=="戸建て系")
            rec["apartment"]=sum(1 for p in src_items if p.get("property_kind")=="集合住宅")
            rec["unknown"]=sum(1 for p in src_items if p.get("property_kind")=="不明")
            rec["detail_errors"]=sum(1 for p in src_items if p.get("detail_error"))
        browser.close()
    unique=dedupe(raw_items)
    candidates=filter_candidates(unique,official_db)
    new_items,price_down=detect_changes(candidates)
    hist=update_history(candidates)
    valid_hist=valid_history_records(hist)
    stage_summary={
        "raw_items": len(raw_items),
        "unique_detail_urls": len({p.get("url") for p in raw_items if p.get("url")}),
        "detail_validated": sum(1 for p in raw_items if p.get("detail_validated")),
        "under_rent_cap": sum(1 for p in raw_items if p.get("rent") is not None and p.get("rent")<=MAX_RENT),
        "detached": sum(1 for p in raw_items if p.get("property_kind")=="戸建て系"),
        "apartments": sum(1 for p in raw_items if p.get("property_kind")=="集合住宅"),
        "unknown": sum(1 for p in raw_items if p.get("property_kind")=="不明"),
        "detail_errors": sum(1 for p in raw_items if p.get("detail_error")),
    }
    report={"version":VERSION,"stage_summary":stage_summary,"total_raw":len(raw_items),"total_retrieved":len(unique),"candidates":len(candidates),"current":len(candidates),"new":new_items,"price_down":price_down,"all_candidates":candidates,"sample":candidates,"diagnostics":diagnostics,"official_db":official_db,"history":hist,"valid_history_count":len(valid_hist),"legacy_unvalidated_count":max(0,len(hist.get("records",{}))-len(valid_hist)) if isinstance(hist,dict) else 0,"official_match_levels":{k:sum(1 for p in candidates if p.get("official_match_level")==k) for k in ["部屋番号一致","住所一致","建物名一致"]}}
    save_json(DIAG_FILE,report); send_report(report)

if __name__=="__main__": main()
