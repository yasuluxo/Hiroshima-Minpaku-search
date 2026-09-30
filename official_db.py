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
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[【】「」『』（）()［］\[\]・,，。:：/／_-]", "", s)
    return s


def find_pdf_url(html, base_url):
    # Current Hiroshima City pages expose the current PDF as a normal anchor.
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    pdfs = [urljoin(base_url, h) for h in hrefs if ".pdf" in h.lower()]
    if not pdfs:
        raise RuntimeError("公式ページからPDFリンクを取得できませんでした")
    # The first PDF is the Japanese current list on these pages.
    return pdfs[0]


def fetch_pdf(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.content


def extract_text(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def clean_line(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def parse_minpaku(text):
    records = []
    date_re = re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日)")
    address_re = re.compile(r"(広島市?(?:中|東|南|西)区.+)")
    pending_date = ""
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        dm = date_re.fullmatch(line)
        if dm:
            pending_date = dm.group(1)
            continue
        m = address_re.search(line)
        if not m:
            continue
        address = m.group(1).strip()
        # If date and address share one line, prefer that date. Otherwise use
        # the most recently seen date from the PDF's two-column extraction.
        inline_date = date_re.search(line)
        date = inline_date.group(1) if inline_date else pending_date
        if not date:
            continue
        ward = next((w for w in WARD_NAMES if w in address), "")
        if not ward:
            continue
        records.append({
            "type": "住宅宿泊事業",
            "category": "民泊届出",
            "date": date,
            "ward": ward,
            "address": address,
            "name": "",
            "operator": "",
            "rooms": extract_rooms(address),
            "source_pdf": "住宅宿泊事業法に基づく届出施設一覧",
        })
    return records


def extract_rooms(address):
    # Keep room identifiers such as 401, －４０１, 201・202, etc.
    rooms = re.findall(r"(?:[-－]|\s)(\d{3,4})号室?", address)
    if not rooms:
        rooms = re.findall(r"[-－](\d{3,4})(?:号|$|,|、)", address)
    return rooms



def extract_as_of_date(text):
    normalized_text = unicodedata.normalize("NFKC", text)
    m = re.search(r"令和\s*([0-9元一二三四五六七八九十百]+)年\s*(\d{1,2})月\s*(\d{1,2})日現在", normalized_text)
    if not m:
        return ""
    kanji = m.group(1)
    vals = {"元":1,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
    if kanji.isdigit():
        year=int(kanji)
    elif len(kanji)==1:
        year=vals.get(kanji, 0)
    else:
        # This list is currently only expected to need the simple single-digit era year.
        year=0
    if not year:
        return ""
    return f"{2018+year}年{int(m.group(2))}月{int(m.group(3))}日現在"

def parse_ryokan(text):
    records = []
    as_of = extract_as_of_date(text)
    # PDF text extraction may wrap names/addresses/operators over several lines.
    # We identify every occurrence of a ward address and use nearby text as the
    # facility/operator context. This intentionally favors recall over perfect
    # formatting; the matching engine later uses normalized address/building text.
    lines = [clean_line(x) for x in text.splitlines() if clean_line(x)]
    for i, line in enumerate(lines):
        m = re.search(r"((?:中|東|南|西)区.+?)(?=(?:株式会社|有限会社|合同会社|一般社団法人|学校法人|医療法人|広島市|個人|[A-ZＡ-Ｚ]|$))", line)
        if not m:
            continue
        address = m.group(1).strip()
        # Require a street-number-like token so headings don't become records.
        if not re.search(r"\d+番|\d+号|\d+－\d+", address):
            continue
        ward = next((w for w in WARD_NAMES if w in address), "")
        if not ward:
            continue
        before = " ".join(lines[max(0, i-3):i])
        after = " ".join(lines[i:min(len(lines), i+3)])
        category = "簡易宿所営業" if "簡易宿所営業" in after else ("旅館・ホテル営業" if "旅館・ホテル営業" in after else "旅館業")
        # Name is the text immediately before the ward address. Remove table labels.
        name = before
        name = re.sub(r".*(?:施設名|区分)\s*", "", name)
        name = re.sub(r"(旅館・ホテル営業|簡易宿所営業|下宿営業)$", "", name).strip()
        # Keep only the last plausible chunk.
        if len(name) > 120:
            name = name[-120:]
        operator = ""
        opm = re.search(r"((?:株式会社|有限会社|合同会社|一般社団法人|学校法人|医療法人).{0,80})", after)
        if opm:
            operator = opm.group(1).strip()
        records.append({
            "type": "旅館業",
            "category": category,
            "date": as_of,
            "ward": ward,
            "address": address,
            "name": name,
            "operator": operator,
            "rooms": extract_rooms(address),
            "source_pdf": "旅館業法に基づく許可施設一覧",
        })
    return records


def dedupe_records(records):
    out, seen = [], set()
    for r in records:
        key = "|".join([norm(r.get("type")), norm(r.get("address")), norm(r.get("name")), norm(r.get("category"))])
        if key in seen:
            continue
        seen.add(key)
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
