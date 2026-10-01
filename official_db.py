from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

MINPAKU_URL = "https://www.city.hiroshima.lg.jp/_res/projects/default_project/_page_/001/013/508/0807.pdf"
RYOKAN_URL = "https://www.city.hiroshima.lg.jp/_res/projects/default_project/_page_/001/013/507/r808.pdf"
DB_PATH = Path("official_db.json")


def _norm(s: str) -> str:
    s = s or ""
    s = s.replace("　", " ")
    s = re.sub(r"\s+", "", s)
    s = s.replace("ー", "- ").replace("−", "-").replace("―", "-")
    return s.strip().lower()


def _download(url: str) -> bytes:
    r = requests.get(url, timeout=40, headers={
        "User-Agent": "Mozilla/5.0 (compatible; HiroshimaMinpakuSearch/8.4)",
        "Accept": "application/pdf,*/*",
    })
    r.raise_for_status()
    return r.content


def _extract_pdf(url: str, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import pdfplumber
    except Exception as e:
        return [], {"status": "ERROR", "error": f"pdfplumber unavailable: {e}"}

    try:
        data = _download(url)
        records: list[dict[str, Any]] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        if not row:
                            continue
                        cells = [(c or "").strip() for c in row]
                        joined = " ".join(cells)
                        if "施設名" in joined and "所在地" in joined:
                            continue
                        # PDFの列崩れに耐えるため、先頭4列を基本とする。
                        if len(cells) >= 2:
                            name = cells[0]
                            address = cells[1]
                            operator = cells[2] if len(cells) >= 3 else ""
                            category = cells[3] if len(cells) >= 4 else ""
                            if name and address and ("区" in address or "広島市" in address):
                                records.append({
                                    "kind": kind,
                                    "name": name,
                                    "address": address,
                                    "operator": operator,
                                    "category": category,
                                })

            # table extractionが空のPDFに対するテキストフォールバック
            if not records:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for line in text.splitlines():
                        line = re.sub(r"\s{2,}", " | ", line.strip())
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 2 and "区" in parts[1] and "施設名" not in line:
                            records.append({
                                "kind": kind,
                                "name": parts[0],
                                "address": parts[1],
                                "operator": parts[2] if len(parts) > 2 else "",
                                "category": parts[3] if len(parts) > 3 else "",
                            })
        # 重複排除
        uniq = {}
        for x in records:
            key = (_norm(x["name"]), _norm(x["address"]), x["kind"])
            uniq[key] = x
        records = list(uniq.values())
        return records, {"status": "OK", "records": len(records), "url": url}
    except Exception as e:
        return [], {"status": "ERROR", "error": str(e), "url": url}


def update_official_db() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    minpaku, d1 = _extract_pdf(MINPAKU_URL, "minpaku")
    ryokan, d2 = _extract_pdf(RYOKAN_URL, "ryokan")
    records = minpaku + ryokan
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "sources": {"minpaku": d1, "ryokan": d2},
    }
    DB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return records, payload["sources"]


def load_official_db() -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
        return data.get("records", [])
    except Exception:
        return []


def normalize_address(s: str) -> str:
    s = _norm(s)
    s = s.replace("広島県", "").replace("広島市", "")
    s = re.sub(r"丁目", "-", s)
    s = re.sub(r"番地", "-", s)
    s = re.sub(r"番", "-", s)
    s = re.sub(r"号", "", s)
    return s


def room_number(s: str) -> str:
    m = re.search(r"(?<!\d)(\d{3,4})\s*(?:号室)?", s or "")
    return m.group(1) if m else ""


def match_candidate(candidate: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    c_name = _norm(candidate.get("building_name", ""))
    c_addr = normalize_address(candidate.get("address", ""))
    c_room = room_number(candidate.get("room", "") or candidate.get("title", ""))
    matches = []
    for r in records:
        r_name = _norm(r.get("name", ""))
        r_addr = normalize_address(r.get("address", ""))
        r_room = room_number(r.get("name", ""))
        addr_exact = bool(c_addr and r_addr and c_addr == r_addr)
        name_exact = bool(c_name and r_name and c_name == r_name)
        room_match = bool(c_room and r_room and c_room == r_room)
        if addr_exact or name_exact or room_match:
            matches.append((r, addr_exact, name_exact, room_match))

    # 部屋番号だけの一致は「一致」としない。建物名または住所の整合を要求。
    valid = []
    for r, addr, name, room in matches:
        if room and not (addr or name):
            continue
        valid.append((r, addr, name, room))

    if not valid:
        return {"match_type": "none", "matches": []}

    # 表示用に部屋→住所→建物名の情報を分離して保持する。
    out = []
    for r, addr, name, room in valid:
        if room and addr:
            typ = "room+address"
        elif room and name:
            typ = "room+building"
        elif addr:
            typ = "address"
        elif name:
            typ = "building"
        else:
            typ = "none"
        out.append({"type": typ, "record": r})

    return {"match_type": out[0]["type"], "matches": out}
