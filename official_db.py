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
DATE_RE = re.compile(r"20\d{2}年\d{1,2}月\d{1,2}日")
ADDRESS_RE = re.compile(r"広島市(?:中|東|南|西)区")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("広島市", "").replace("広島県", "")
    s = re.sub(r"\s+", "", s)
    s = s.replace("－", "-").replace("‐", "-").replace("−", "-")
    s = re.sub(r"[-【】「」『』（）()［］\[\]・,，。:：/／_\\\s]", "", s)
    return s


def clean_line(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def extract_rooms(text):
    t = unicodedata.normalize("NFKC", text or "")
    rooms = []
    # 201 / 601号室 / ６０１号 etc.  Avoid short street numbers.
    for m in re.finditer(r"(?<!\d)(\d{3,4})(?:号室|号)?", t):
        n = m.group(1)
        if 100 <= int(n) <= 9999:
            rooms.append(n)
    return list(dict.fromkeys(rooms))


def split_official_address(address):
    """Split official address into street/base and the suffix after the first 号."""
    a = clean_line(address)
    a = a.replace("－", "-").replace("‐", "-").replace("−", "-")
    m = re.search(r"^(.*?\d+号)(.*)$", a)
    if not m:
        return a, ""
    base = m.group(1).strip()
    tail = m.group(2).strip()
    if re.fullmatch(r"[-]?\d{3,4}号?", tail):
        base += tail
        tail = ""
    return base, tail


def normalize_address_for_match(s):
    x = unicodedata.normalize("NFKC", s or "")
    x = x.replace("広島県", "").replace("広島市", "")
    x = x.replace("－", "-").replace("‐", "-").replace("−", "-")
    x = re.sub(r"\s+", "", x)
    return x


def extract_address_parts(address):
    """Return (ward, street/base address, building name, room numbers)."""
    a = normalize_address_for_match(address)
    ward_m = re.search(r"(中区|東区|南区|西区)", a)
    ward = ward_m.group(1) if ward_m else ""
    base, tail = split_official_address(a)
    rooms = extract_rooms(tail)
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


def extract_text_variants(pdf_bytes):
    """Return multiple extraction variants because Hiroshima PDFs vary by layout."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    variants = []
    # Plain extraction is usually the most useful for the minpaku table.
    for mode in (None, "layout"):
        chunks = []
        for page in reader.pages:
            try:
                if mode == "layout":
                    txt = page.extract_text(extraction_mode="layout") or ""
                else:
                    txt = page.extract_text() or ""
            except TypeError:
                txt = page.extract_text() or ""
            chunks.append(txt)
        variants.append("\n".join(chunks))
    # De-duplicate identical extraction outputs.
    out = []
    for x in variants:
        if x not in out:
            out.append(x)
    return out


def _extract_minpaku_records(text):
    """Parse date + Hiroshima address rows without relying on PDF column layout."""
    text = unicodedata.normalize("NFKC", text or "")
    # Remove repeated table headings so they cannot become part of an address.
    text = re.sub(r"住宅宿泊事業法に基づく届出施設一覧[^\n]*", "", text)
    text = re.sub(r"届出年月日\s*届出住宅の所在地[^\n]*", "", text)

    # First try the most direct row pattern. It also handles rows concatenated
    # without a newline (which occurs around page breaks in some PDF extractors).
    row_re = re.compile(
        r"(20\d{2}年\d{1,2}月\d{1,2}日)\s*"
        r"(広島市(?:中|東|南|西)区.*?)"
        r"(?=(?:20\d{2}年\d{1,2}月\d{1,2}日)|$)",
        re.S,
    )
    candidates = list(row_re.finditer(text))

    records = []
    for m in candidates:
        date = m.group(1)
        raw = clean_line(m.group(2))
        # A PDF footer/header can trail a row; cut it before the next heading.
        raw = re.split(r"(?:届出年月日|住宅宿泊事業法に基づく届出施設一覧)", raw)[0].strip()
        # Occasionally extraction puts two rows together with no date boundary
        # but a new Hiroshima address. Keep only the first address in that case.
        addr_matches = list(ADDRESS_RE.finditer(raw))
        if len(addr_matches) > 1:
            raw = raw[:addr_matches[1].start()].strip()
        if not re.search(r"\d+番|\d+号|\d+-\d+", raw):
            continue
        ward_m = re.search(r"(中区|東区|南区|西区)", raw)
        if not ward_m:
            continue
        address = raw
        ward, _, building, rooms = extract_address_parts(address)
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


def parse_minpaku(text):
    return _extract_minpaku_records(text)


def extract_as_of_date(text):
    normalized_text = unicodedata.normalize("NFKC", text)
    m = re.search(r"令和\s*([0-9元一二三四五六七八九十百]+)年\s*(\d{1,2})月\s*(\d{1,2})日現在", normalized_text)
    if not m:
        return ""
    kanji = m.group(1)
    vals = {"元": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if kanji.isdigit():
        year = int(kanji)
    elif len(kanji) == 1:
        year = vals.get(kanji, 0)
    else:
        year = 0
    return f"{2018 + year}年{int(m.group(2))}月{int(m.group(3))}日現在" if year else ""


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
        op_match = re.search(r"\s((?:株式会社|有限会社|合同会社|一般社団法人|学校法人|医療法人).*)$", pre_cat)
        if op_match:
            address = pre_cat[:op_match.start()].strip()
            operator = op_match.group(1).strip()
        else:
            address = pre_cat
            operator = ""
            for nxt in lines[i + 1:min(i + 4, len(lines))]:
                if any(nxt.startswith(x) for x in operator_markers):
                    operator = nxt
                    break
        address = address.strip(" ,")
        address = re.split(r"(?:※|住宅宿泊事業法に基づく|旅館業法に基づく)", address)[0].strip()
        if not re.match(r"^(?:中|東|南|西)区", address):
            continue
        if not re.search(r"\d+番|\d+号|\d+-\d+", address):
            continue
        name = prefix
        if not name and i > 0:
            prev = lines[i - 1]
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
        building_name = building or r.get("name", "")
        key = "|".join([
            norm(r.get("type")),
            norm(base),
            norm(building_name),
            norm(r.get("category")),
            ",".join(sorted(r.get("rooms", []) or rooms)),
        ])
        if key in seen:
            continue
        seen.add(key)
        r["ward"] = r.get("ward") or ward
        r["base_address"] = base
        r["building_name"] = building_name
        r["rooms"] = list(dict.fromkeys(r.get("rooms") or rooms))
        r["address_norm"] = norm(base)
        r["building_norm"] = norm(building_name)
        out.append(r)
    return out


def _parse_best(kind, texts):
    parsed = []
    for text in texts:
        records = parse_minpaku(text) if kind == "minpaku" else parse_ryokan(text)
        parsed.append(records)
    if not parsed:
        return []
    # Choose the extraction mode yielding the most records. This fixes PDFs where
    # layout extraction separates the date and address columns.
    return max(parsed, key=len)


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
            texts = extract_text_variants(pdf)
            records = _parse_best(kind, texts)
            all_records.extend(records)
            diagnostics.append({
                "kind": kind,
                "status": "OK",
                "pdf_url": pdf_url,
                "records": len(records),
                "seconds": round(time.time() - started, 1),
            })
        except Exception as e:
            diagnostics.append({
                "kind": kind,
                "status": "ERROR",
                "error": repr(e)[:500],
                "seconds": round(time.time() - started, 1),
            })
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
    return norm(s)


def match_official(property_item, db):
    """Conservative official-list matching: exact base address/building, then room."""
    paddr_raw = property_item.get("address", "") or ""
    ptitle = property_item.get("title", "") or ""
    pdesc = property_item.get("description", "") or ""
    pward, pbase, pbuilding, prooms = extract_address_parts(paddr_raw)
    if not pward:
        # Some listing addresses omit '広島市'; try with the selected ward.
        area = property_item.get("area", "")
        if area in WARD_NAMES:
            pward, pbase, pbuilding, prooms = extract_address_parts("広島市" + area + paddr_raw)

    # Use title only as a building-name signal after stripping room/floor noise.
    title_clean = unicodedata.normalize("NFKC", ptitle or "")
    title_clean = re.sub(r"\s*(?:\d+階\s*/\s*\d+|\d{3,4}号室?|\d{3,4})\s*$", "", title_clean).strip()
    title_norm = norm(title_clean)
    if re.search(r"<[^>]+>", ptitle) or "class=" in ptitle.lower() or "onclick=" in ptitle.lower():
        title_norm = ""
    matches = []
    for r in db.get("records", []):
        if pward and r.get("ward") and pward != r.get("ward"):
            continue
        rbase = r.get("address_norm") or compact_address(r.get("base_address") or r.get("address", ""))
        rbuilding = r.get("building_norm") or norm(r.get("building_name") or r.get("name", ""))
        rrooms = set(r.get("rooms") or [])

        # 1) Exact base address is required for an address-level match.
        base_exact = bool(pbase and rbase and norm(pbase) == norm(rbase))

        # 2) Exact building name. Never use substring matching.
        building_exact = bool(pbuilding and rbuilding and norm(pbuilding) == rbuilding)
        title_building_exact = bool(title_norm and rbuilding and title_norm == rbuilding)

        # A room number is never sufficient by itself: the building identity must
        # also agree when both sides have a building name. This prevents cases such
        # as "西十日市ビル" being treated as "十日市ビル" just because 201 matches.
        building_consistent = (
            not pbuilding
            or not rbuilding
            or building_exact
            or title_building_exact
        )
        room_exact = bool(prooms and rrooms and set(prooms) & rrooms)

        if base_exact and room_exact and building_consistent:
            level = "部屋番号一致"
        elif base_exact and building_consistent:
            level = "住所一致"
        elif building_exact or (title_building_exact and (not pbase or not rbase)):
            level = "建物名一致"
        else:
            continue

        item = dict(r)
        item["match_level"] = level
        matches.append(item)

    # Prefer the strongest level and remove duplicate official rows.
    rank = {"部屋番号一致": 3, "住所一致": 2, "建物名一致": 1}
    matches.sort(key=lambda x: (-rank.get(x.get("match_level", ""), 0), x.get("type", ""), x.get("address", "")))
    out, seen = [], set()
    for m in matches:
        key = (m.get("type"), m.get("address"), m.get("category"), m.get("match_level"))
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out
