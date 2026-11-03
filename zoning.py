"""Best-effort zoning lookup for Hiroshima City listings.

Address geocoding uses OpenStreetMap Nominatim (one request/second).
Zoning polygons are queried from the public ArcGIS service that republishes
MLIT National Land Numerical Information zoning data.  This is a screening
reference only; final confirmation must be made with Hiroshima City's official
Map Navi / Planning Division.
"""
import json
import re
import time
import unicodedata
from urllib.parse import quote

import requests

VERSION = "8.17"
CACHE_FILE = "zoning_cache.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Public ArcGIS layer described as a nationwide compilation of MLIT zoning data.
ZONING_QUERY_URL = (
    "https://services.arcgis.com/wlVTGRSYTzAbjjiC/arcgis/rest/services/"
    "CityPlanningArea/FeatureServer/0/query"
)
MAP_NAVI_URL = "https://www2.wagmap.jp/hiroshimacity/Portal"

# The table supplied by the user (民泊 / 旅館業 only).
RULES = {
    "第一種低層住居専用地域": ("○（条例制限あり）", "×"),
    "第二種低層住居専用地域": ("○（条例制限あり）", "×"),
    "第一種中高層住居専用地域": ("○（条例制限あり）", "×"),
    "第二種中高層住居専用地域": ("○（条例制限あり）", "×"),
    "第一種住居地域": ("○", "○（床面積3,000㎡以下等）"),
    "第二種住居地域": ("○", "○"),
    "準住居地域": ("○", "○"),
    "田園住居地域": ("○（条例制限あり）", "×"),
    "近隣商業地域": ("○", "○"),
    "商業地域": ("○", "○"),
    "準工業地域": ("○", "○"),
    "工業地域": ("○", "×"),
    "工業専用地域": ("×", "×"),
}


def _norm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.replace("広島県", "").replace("広島市", "")
    s = re.sub(r"[\s　,，・、。\-‐‑‒–—−ー/／]", "", s)
    return s


def _zone_from_attributes(attrs):
    if not isinstance(attrs, dict):
        return ""
    # ArcGIS field names can differ between data revisions; inspect all values.
    zone_names = sorted(RULES, key=len, reverse=True)
    values = [str(v) for v in attrs.values() if v is not None]
    joined = _norm(" ".join(values))
    for name in zone_names:
        if _norm(name) in joined:
            return name
    # Accept common GIS abbreviation variants (第1種 / 第１種).
    compact = joined.replace("第1種", "第一種").replace("第2種", "第二種")
    for name in zone_names:
        if _norm(name) in compact:
            return name
    return ""


def _load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _geocode(address, session):
    # Do not geocode town-only addresses; their representative point can fall
    # in a different zone from the actual property.
    if not re.search(r"\d+", address or ""):
        return None, "番地情報なし"
    params = {"q": address, "format": "jsonv2", "limit": 3, "countrycodes": "jp", "addressdetails": 1}
    try:
        r = session.get(NOMINATIM_URL, params=params, timeout=20,
                        headers={"User-Agent": "HiroshimaMinpakuSearch/8.17 (property zoning screening)"})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None, "住所を座標に変換できず"
        # Require a house/building-level match. Road/street and town centroids
        # are too coarse for a parcel-level zoning decision.
        for row in rows:
            at = str(row.get("addresstype", "")).lower()
            typ = str(row.get("type", "")).lower()
            if at in ("house", "building") or typ in ("house", "building"):
                return {"lat": float(row["lat"]), "lon": float(row["lon"]),
                        "display_name": row.get("display_name", ""),
                        "precision": at or typ}, "OK"
        return None, "建物位置まで特定できず（町丁目・道路レベル）"
    except Exception as e:
        return None, "ジオコーディング通信エラー: " + repr(e)[:160]


def _lookup_zone(point, session):
    params = {
        "where": "1=1",
        "geometry": f"{point['lon']},{point['lat']}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        r = session.get(ZONING_QUERY_URL, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            return "", "GIS照合エラー: " + str(data["error"].get("message", "unknown"))[:120]
        features = data.get("features") or []
        zones = sorted(set(z for f in features if (z := _zone_from_attributes(f.get("attributes", {})))))
        if len(zones) == 1:
            return zones[0], "OK"
        if len(zones) > 1:
            return "", "用途地域境界の可能性（複数区域）"
        if not features:
            return "", "用途地域ポリゴン該当なし"
        return "", "GIS属性から用途地域を特定できず"
    except Exception as e:
        return "", "GIS照合通信エラー: " + repr(e)[:160]


def classify_zone(zone):
    if zone not in RULES:
        return {"minpaku": "要確認", "ryokan": "要確認", "zoning_status": "要確認"}
    minpaku, ryokan = RULES[zone]
    status = "両方可" if minpaku.startswith("○") and ryokan.startswith("○") else (
        "民泊のみ可" if minpaku.startswith("○") else (
            "旅館業のみ可" if ryokan.startswith("○") else "用途地域上は両方不可"
        )
    )
    return {"minpaku": minpaku, "ryokan": ryokan, "zoning_status": status}


def enrich_candidates(candidates):
    cache = _load_cache()
    # Keep the state file present even on runs with zero candidates so the
    # GitHub Actions commit step never fails on a missing path.
    _save_cache(cache)
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    last_geocode = 0.0
    counts = {"checked": 0, "minpaku_possible": 0, "ryokan_possible": 0, "both_impossible": 0, "unknown": 0}

    for p in candidates:
        address = (p.get("address") or "").strip()
        key = _norm(address)
        if not key:
            result = {"zoning": "", "minpaku": "要確認", "ryokan": "要確認",
                      "zoning_status": "要確認", "zoning_reason": "住所なし", "zoning_source": ""}
        elif key in cache:
            result = dict(cache[key])
        else:
            wait = 1.05 - (time.time() - last_geocode)
            if wait > 0:
                time.sleep(wait)
            point, geo_status = _geocode(address, session)
            last_geocode = time.time()
            if not point:
                result = {"zoning": "", "minpaku": "要確認", "ryokan": "要確認",
                          "zoning_status": "要確認", "zoning_reason": geo_status,
                          "zoning_source": "OpenStreetMap Nominatim（住所座標）"}
            else:
                zone, gis_status = _lookup_zone(point, session)
                rule = classify_zone(zone)
                result = {"zoning": zone, **rule,
                          "zoning_reason": gis_status,
                          "zoning_source": "MLIT用途地域データ（ArcGIS公開レイヤー）",
                          "zoning_lat": point["lat"], "zoning_lon": point["lon"],
                          "zoning_geocode_precision": point.get("precision", "")}
            cache[key] = result
            _save_cache(cache)
        p.update(result)
        counts["checked"] += 1
        if result.get("zoning_status") == "要確認":
            counts["unknown"] += 1
        else:
            if str(result.get("minpaku", "")).startswith("○"):
                counts["minpaku_possible"] += 1
            if str(result.get("ryokan", "")).startswith("○"):
                counts["ryokan_possible"] += 1
            if result.get("zoning_status") == "用途地域上は両方不可":
                counts["both_impossible"] += 1
    return candidates, counts
