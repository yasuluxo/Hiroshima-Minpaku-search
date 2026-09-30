import io
import json
import re
import time
import unicodedata
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

from config import OFFICIAL_PAGES, USER_AGENT, OFFICIAL_DB_FILE

WARD_NAMES = ["中区", "東区", "南区", "西区"]


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("広島市", "")
    s = s.replace("広島県", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[【】「」『』（）()［］\[\]・,，。:：/／_-]", "", s)
    return s


def clean_line(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def extract_rooms(text):
    t = unicodedata.normalize("NFKC", text or "")
    rooms = []
    # Room lists such as 201, 301, 401 or ３０１、３０２号
    for m in re.finditer(r"(?<!\d)(\d{3,4})(?:号室|号)?", t):
        n = m.group(1)
        # Avoid treating ordinary street numbers as room numbers.
        if 100 <= int(n) <= 9999:
            rooms.append(n)
    return list(dict.fromkeys(rooms))


def split_official_address(address):
    """Split a Hiroshima official address into street/base and building-name parts."""
    a = clean_line(address)
    a = a.replace("－", "-").replace("‐", "-").replace("−", "-")
    # Everything through the first 号 is the postal/street address. Anything after
    # it is normally the building name and room number.
    m = re.search(r"^(.*?\d+号)(.*)$", a)
    if not m:
        return a, ""
    base = m.group(1).strip()
    tail = m.group(2).strip()
    # A suffix like -202号 is still part of the address, not a building name.
    if re.fullmatch(r"[-]?\d{3,4}号?", tail):
        base = base + tail
        tail = ""
    return base, tail


def normalize_address_for_match(s):
    x = unicodedata.normalize("NFKC", s or "")
    x = x.replace("広島県", "").replace("広島市", "")
    x = x.replace("－", "-").replace("‐", "-").replace("−", "-")
    x = re.sub(r"\s+", "", x)
    x = x.replace("丁目", "丁目")
    return x


def extract_address_parts(address):
    """Return (ward, street_address_with_number, building_name, rooms)."""
    a = normalize_address_for_match(address)
    ward_m = re.search(r"(中区|東区|南区|西区)", a)
    ward = ward_m.group(1) if ward_m else ""
    base, tail = split_official_address(a)
    rooms = extract_rooms(tail)
    # Floor-only suffixes such as 2階、6階 are not building names.
    if re.fullmatch(r"(?:\d+階(?:[、,]\d+階)*)", tail):
        building = ""
    else:
        building = re.sub(r"(?:\d{3,4})(?:号室?|号)?$", "", tail).strip(" -")
    return ward, base, building, rooms


def find_pdf_url(html, base_url):
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    pdfs = [urljoin(base_url, h) for h in hrefs if ".pdf" in h.lower()]
    if not pdfs:
        raise RuntimeError("公式ページからPDFリンクを取得できませんでした")
    return pdfs[0]


def fetch_pdf(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.content


def extract_text(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    chunks = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text(extraction_mode="layout") or "")
        except TypeError:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def parse_minpaku(text):
    records = []
    # The current Hiroshima PDF is a two-column table. Across both normal and
    # layout extraction, each row begins with a YYYY年M月D日 date followed by
    # the complete address. Capture one row at a time until the next date.
    row_re = re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日)\s*(広島市(?:中|東|南|西)区.*?)(?=20\d{2}年\d{1,2}月\d{1,2}日|$)", re.S)
    for m in row_re.finditer(text):
        date = m.group(1)
        raw = clean_line(m.group(2))
        # Remove PDF page headings/column labels accidentally included.
        raw = re.sub(r"住宅宿泊事業法に基づく届出施設一覧.*?現在", "", raw)
        raw = re.sub(r"届出年月日\s*届出住宅の所在地.*$", "", raw)
        addr_m = re.search(r"(広島市(?:中|東|南|西)区.+)$", raw)
        if not addr_m:
            continue
        address = addr_m.group(1).strip()
        if not re.search(r"\d+番|\d+号|\d+-\d+", address):
            continue
        ward = next((w for w in WARD_NAMES if w in address), "")
        if not ward:
            continue
        _, _, building, rooms = extract_address_parts(address)
        records.append({
            "type": "住宅宿泊事業",
            "category": "民泊届出",
            "date": date,
            "ward": ward,
            "address": address,
            "name": building,
            "operator": "",
            "rooms": rooms,
            "source_pdf": "住宅宿泊事業法に基づく届出施設一覧",
        })
    return dedupe_records(records)


def extract_as_of_date(text):
    normalized_text = unicodedata.normalize("NFKC", text)
    m = re.search(r"令和\s*([0-9元一二三四五六七八九十百]+)年\s*(\d{1,2})月\s*(\d{1,2})日現在", normalized_text)
    if not m:
        return ""
    kanji = m.group(1)
    vals = {"元":1,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
    if kanji.isdigit():
        year = int(kanji)
    elif len(kanji) == 1:
        year = vals.get(kanji, 0)
    else:
        year = 0
    return f"{2018+year}年{int(m.group(2))}月{int(m.group(3))}日現在" if year else ""


def parse_ryokan(text):
    records = []
    as_of = extract_as_of_date(text)
    lines = [clean_line(x) for x in text.splitlines() if clean_line(x)]
    category_re = re.compile(r"(旅館・ホテル営業|簡易宿所営業|下宿営業)")
    operator_markers = ("株式会社", "有限会社", "合同会社", "一般社団法人", "学校法人", "医療法人")
    for i, line in enumerate(lines):
        m = re.search(r"(?P<ward>(?:中|東|南|西)区)", line)
        if not m or not re.search(r"\d+番|\d+号|\d+-\d+", line[m.start():]):
            continue
        prefix = line[:m.start()].strip()
        rest = line[m.start():].strip()
        # Address/operator/category can wrap to the next few PDF lines.
        chunk = rest
        for j in range(i + 1, min(i + 5, len(lines))):
            if re.search(r"(?:中|東|南|西)区", lines[j]) and re.search(r"\d+番|\d+号", lines[j]):
                break
            chunk += " " + lines[j]
            if category_re.search(lines[j]):
                break
        cm = category_re.search(chunk)
        category = cm.group(1) if cm else "旅館業"
        pre_cat = chunk[:cm.start()].strip() if cm else chunk
        # Split address from operator. Most operators have a recognizable legal
        # entity prefix; otherwise keep the address up to the first obvious name
        # boundary only when a separate line supplies the operator.
        op_match = re.search(r"\s((?:株式会社|有限会社|合同会社|一般社団法人|学校法人|医療法人).*)$", pre_cat)
        if op_match:
            address = pre_cat[:op_match.start()].strip()
            operator = op_match.group(1).strip()
        else:
            # Personal-name operators are hard to distinguish from an address in
            # plain PDF text. The address is normally complete on the first line;
            # take that line's numeric/address segment and leave operator blank.
            address = pre_cat
            operator = ""
            # If a later line contains a non-address operator, retain it as context.
            for nxt in lines[i+1:min(i+4, len(lines))]:
                if any(nxt.startswith(x) for x in operator_markers):
                    operator = nxt
                    break
        address = address.strip(" ,")
        # If the address accidentally includes a page footer, trim it.
        address = re.split(r"(?:※|住宅宿泊事業法に基づく|旅館業法に基づく)", address)[0].strip()
        if not re.match(r"^(?:中|東|南|西)区", address):
            continue
        if not re.search(r"\d+番|\d+号|\d+-\d+", address):
            continue
        name = prefix
        if not name and i > 0:
            # Some extraction modes put the facility name on the previous line.
            prev = lines[i-1]
            if not re.search(r"(?:中|東|南|西)区", prev) and not category_re.search(prev):
                name = prev
        name = re.sub(r"\s+", " ", name).strip()
        ward = m.group("ward")
        _, _, building_from_addr, rooms = extract_address_parts("広島市" + address)
        if building_from_addr and not name:
            name = building_from_addr
        records.append({
            "type": "旅館業",
            "category": category,
            "date": as_of,
            "ward": ward,
            "address": "広島市" + address,
            "name": name,
            "operator": operator,
            "rooms": rooms,
            "source_pdf": "旅館業法に基づく許可施設一覧",
        })
    return dedupe_records(records)

def dedupe_records(records):
    out, seen = [], set()
    for r in records:
        ward, base, building, rooms = extract_address_parts(r.get("address", ""))
        key = "|".join([norm(r.get("type")), norm(base), norm(building), norm(r.get("name")), norm(r.get("category"))])
        if key in seen:
            continue
        seen.add(key)
        r["ward"] = r.get("ward") or ward
        r["base_address"] = base
        # For ryokan rows, the facility name is the useful building identifier.
        r["building_name"] = building or r.get("name", "")
        r["rooms"] = r.get("rooms") or rooms
        r["address_norm"] = norm(base)
        r["building_norm"] = norm(r.get("building_name", ""))
        out.append(r)
    return out


def build_official_db():
    all_records = []
    diagnostics = []
    for kind, page_url in OFFICIAL_PAGES.items():
        started = time.time()
        try:
            r = requests.get(page_url, headers={"User-Agent": USER_AGENT}, timeout=60)
            r.raise_for_status()
            pdf_url = find_pdf_url(r.text, page_url)
            pdf = fetch_pdf(pdf_url)
            text = extract_text(pdf)
            if kind == "minpaku":
                records = parse_minpaku(text)
            else:
                records = parse_ryokan(text)
            all_records.extend(records)
            diagnostics.append({"kind": kind, "status": "OK", "pdf_url": pdf_url, "records": len(records), "seconds": round(time.time()-started,1)})
        except Exception as e:
            diagnostics.append({"kind": kind, "status": "ERROR", "error": repr(e)[:500], "seconds": round(time.time()-started,1)})
    all_records = dedupe_records(all_records)
    db = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "records": all_records,
        "diagnostics": diagnostics,
    }
    with open(OFFICIAL_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    return db


def load_official_db():
    try:
        with open(OFFICIAL_DB_FILE, encoding="utf-8") as f:
            db = json.load(f)
        if db.get("records"):
            return db
    except Exception:
        pass
    return build_official_db()


def compact_address(s):
    x = norm(s)
    x = x.replace("広島県", "")
    return x


def match_official(property_item, db):
    """Return matching official records. Matching is deliberately conservative."""
    text = norm(" ".join([
        property_item.get("title", ""),
        property_item.get("address", ""),
        property_item.get("description", ""),
    ]))
    paddr = compact_address(property_item.get("address", ""))
    matches = []
    for r in db.get("records", []):
        raddr = compact_address(r.get("address", ""))
        # Exact/near address match is strongest.
        if raddr and (raddr in text or (paddr and paddr in raddr)):
            matches.append(r)
            continue
        # Building-name match: extract the non-address suffix from official address.
        bname = r.get("name", "")
        if bname and norm(bname) in text:
            matches.append(r)
            continue
        # For minpaku rows the building name is embedded in the address. Try a
        # distinctive tail after the ward/number.
        addr = r.get("address", "")
        parts = re.split(r"\d+番地?\d*|\d+番|\d+号", addr)
        tail = norm(parts[-1]) if parts else ""
        if len(tail) >= 3 and tail in text:
            matches.append(r)
    return matches
