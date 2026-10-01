from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import (MAX_RAW_PER_SITE_WARD, MIN_BODY_CHARS, PAGE_TIMEOUT_MS,
                    RENT_MAX, RETRY_COUNT, TARGET_WARDS)
from mailer import send_email
from official_db import load_official_db, match_candidate, update_official_db

ROOT = Path(__file__).resolve().parent
SEEN_PATH = ROOT / 'seen.json'
PRICE_PATH = ROOT / 'price_history.json'
CAND_PATH = ROOT / 'candidate_history.json'
DIAG_PATH = ROOT / 'diagnostics.json'

WARD_SLUGS = {
    '中区': {'suumo': 'sc_hiroshimashinaka', 'homes': 'hiroshima_naka-city', 'athome': 'hiroshima_naka-city'},
    '南区': {'suumo': 'sc_hiroshimashiminami', 'homes': 'hiroshima_minami-city', 'athome': 'hiroshima_minami-city'},
    '西区': {'suumo': 'sc_hiroshimashinishiku', 'homes': 'hiroshima_nishi-city', 'athome': 'hiroshima_nishi-city'},
    '東区': {'suumo': 'sc_hiroshimashihigashi', 'homes': 'hiroshima_higashi-city', 'athome': 'hiroshima_higashi-city'},
}

SEARCH_URLS = {
    'suumo': lambda w: f"https://suumo.jp/chintai/hiroshima/{WARD_SLUGS[w]['suumo']}/?sort=1&cb=0.0&ct=10.0",
    'homes': lambda w: f"https://www.homes.co.jp/chintai/hiroshima/{WARD_SLUGS[w]['homes']}/list/",
    'athome': lambda w: f"https://www.athome.co.jp/chintai/hiroshima/{WARD_SLUGS[w]['athome']}/list/",
}

REAL_URL_RE = re.compile(r'^https?://', re.I)
PRICE_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:万円|万)')
AREA_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:㎡|m2|m²)')


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def norm(s: str) -> str:
    return re.sub(r'\s+', '', (s or '').replace('　', ' ')).lower()


def parse_price(text: str) -> int | None:
    m = PRICE_RE.search(text or '')
    if not m:
        m2 = re.search(r'(\d{2,6})\s*円', text or '')
        return int(m2.group(1)) if m2 else None
    return int(float(m.group(1)) * 10000)


def extract_room(text: str) -> str:
    m = re.search(r'(?<!\d)(\d{3,4})\s*(?:号室|号)?', text or '')
    return m.group(1) if m else ''


def classify_house(text: str) -> str:
    t = text or ''
    if re.search(r'一戸建|戸建|貸家|タウンハウス|メゾネット|テラスハウス', t):
        return '戸建て系'
    if re.search(r'マンション|アパート|ハイツ|コーポ|レジデンス|ビル|団地', t):
        return '集合住宅'
    return 'その他'


def likely_candidate(item: dict) -> tuple[bool, list[str]]:
    text = item['text']
    price = item.get('rent')
    if price is None or price > RENT_MAX:
        return False, []
    kind = classify_house(text)
    reasons = []
    if kind == '戸建て系':
        reasons.append('戸建・一戸建・タウンハウス等')
    elif kind == '集合住宅':
        reasons.append('集合住宅（公式DB照合対象）')
    else:
        reasons.append('種別判定要確認')
    if price <= 60000:
        reasons.append('家賃6万円以下')
    elif price <= 80000:
        reasons.append('家賃8万円以下')
    else:
        reasons.append('家賃10万円以下')
    return True, reasons


def extract_items(page, site: str, ward: str) -> list[dict]:
    # 一覧ページのアンカーから「実在URL」を持つものだけを拾う。javascript:void(0)は絶対に採用しない。
    anchors = page.locator('a').all()
    out = []
    seen_urls = set()
    for a in anchors:
        try:
            href = a.get_attribute('href') or ''
            txt = (a.inner_text(timeout=800) or '').strip()
            aria = a.get_attribute('aria-label') or ''
            title = a.get_attribute('title') or ''
            data_href = a.get_attribute('data-href') or a.get_attribute('data-url') or ''
            candidate_href = href if REAL_URL_RE.match(href) else data_href
            if candidate_href and candidate_href.startswith('/'):
                base = {'suumo':'https://suumo.jp','homes':'https://www.homes.co.jp','athome':'https://www.athome.co.jp'}[site]
                candidate_href = urljoin(base, candidate_href)
            if not REAL_URL_RE.match(candidate_href):
                continue
            low = candidate_href.lower()
            if any(x in low for x in ['javascript:', '#', '/chintai/hiroshima/', '/search/', '/list/']) and site != 'suumo':
                # 詳細URLでない可能性が高いが、下のテキスト/親カードで補完する
                continue
            if candidate_href in seen_urls:
                continue
            # 親カードを最大6段階まで上り、物件情報をまとめる
            card_text = txt
            locator = a
            for _ in range(6):
                try:
                    locator = locator.locator('..')
                    ptxt = locator.inner_text(timeout=500) or ''
                    if len(ptxt) > len(card_text):
                        card_text = ptxt
                    if len(card_text) >= 80 and (PRICE_RE.search(card_text) or '万円' in card_text):
                        break
                except Exception:
                    break
            if not (PRICE_RE.search(card_text) or '円' in card_text):
                continue
            rent = parse_price(card_text)
            if rent is None or rent > RENT_MAX:
                continue
            # 物件名候補：anchor textを優先し、短すぎる場合はカード冒頭を利用
            name = txt.strip() or title.strip() or aria.strip()
            if not name or len(name) < 2:
                lines = [x.strip() for x in card_text.splitlines() if x.strip()]
                name = next((x for x in lines if len(x) >= 2), '')
            area_m = AREA_RE.search(card_text)
            area = float(area_m.group(1)) if area_m else None
            room = extract_room(card_text)
            kind = classify_house(card_text)
            # 住所は広島市の区名から、町丁目を含む文字列を拾う
            addr = ''
            maddr = re.search(r'(?:広島県)?広島市[^\n]+?区[^\n]{0,35}', card_text)
            if maddr:
                addr = maddr.group(0).strip()
            item = {
                'site': site, 'ward': ward, 'title': name[:200], 'building_name': name[:150],
                'address': addr, 'room': room, 'rent': rent, 'area': area,
                'kind': kind, 'url': candidate_href, 'text': card_text[:3000]
            }
            ok, reasons = likely_candidate(item)
            if ok:
                item['reasons'] = reasons
                out.append(item)
                seen_urls.add(candidate_href)
            if len(out) >= MAX_RAW_PER_SITE_WARD:
                break
        except Exception:
            continue
    return out


def retrieve(page, site: str, ward: str) -> tuple[list[dict], dict]:
    urls = [SEARCH_URLS[site](ward)]
    # HOME'S/at homeは /list/ なし・パラメータ付きも試す。サイト側のリダイレクト/短本文対策。
    base = urls[0]
    if site == 'homes':
        urls += [base.replace('/list/', '/'), base + '?sort=price']
    elif site == 'athome':
        urls += [base.replace('/list/', '/'), base + '?sort=price']
    else:
        urls += [base + '&page=2']

    attempts = []
    for attempt in range(min(RETRY_COUNT, len(urls))):
        url = urls[attempt]
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(1800 + attempt * 1000)
            # 画面下部まで一度スクロールし、遅延ロードを起こす。
            for _ in range(3):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(500)
            body = page.locator('body').inner_text(timeout=5000) or ''
            items = extract_items(page, site, ward)
            attempts.append({'url': url, 'body': len(body), 'raw': len(items)})
            if len(body) >= MIN_BODY_CHARS and items:
                return items, {'status': 'OK', 'attempts': attempts, 'body': len(body)}
            if len(body) >= MIN_BODY_CHARS and site in ('homes', 'athome'):
                # 本文は取れているがパーサーが合わない場合、次URLを試す
                continue
        except (PlaywrightTimeoutError, Exception) as e:
            attempts.append({'url': url, 'error': type(e).__name__})
        time.sleep(1)
    status = 'RETRIEVAL_ZERO' if attempts and all(x.get('raw', 0) == 0 for x in attempts) else 'ERROR'
    return [], {'status': status, 'attempts': attempts, 'body': attempts[-1].get('body', 0) if attempts else 0}


def candidate_key(x: dict) -> str:
    # URL優先。URLがない候補でも建物+住所+部屋+家賃で安定化。
    if x.get('url'):
        return norm(x['url'])
    return '|'.join(norm(str(x.get(k,''))) for k in ['site','ward','building_name','address','room','rent'])


def merge_current(items: list[dict]) -> list[dict]:
    d = {}
    for x in items:
        k = candidate_key(x)
        if k not in d:
            d[k] = x
        else:
            # URLや住所等の情報が豊富な方を残す
            old = d[k]
            for field in ['url','address','room','area','building_name']:
                if not old.get(field) and x.get(field):
                    old[field] = x[field]
    return list(d.values())


def update_candidate_history(current: list[dict], official_records: list[dict]) -> tuple[list[dict], list[dict]]:
    history = load_json(CAND_PATH, [])
    if not isinstance(history, list):
        history = []
    index = {h.get('key'): h for h in history if h.get('key')}
    now = datetime.now(timezone.utc).astimezone().isoformat()
    new_items = []
    for x in current:
        key = candidate_key(x)
        m = match_candidate(x, official_records)
        x2 = dict(x)
        x2.pop('text', None)
        x2['key'] = key
        x2['official_match'] = m
        if key in index:
            h = index[key]
            h.update({k:v for k,v in x2.items() if k not in ('first_seen_at','review_status','review_notes')})
            h['last_seen_at'] = now
            h['listing_status'] = '現在取得'
            h.setdefault('review_status', '未確認')
            h.setdefault('review_notes', '')
        else:
            x2['first_seen_at'] = now
            x2['last_seen_at'] = now
            x2['listing_status'] = '現在取得'
            x2['review_status'] = '未確認'
            x2['review_notes'] = ''
            history.append(x2)
            index[key] = x2
            new_items.append(x2)
    current_keys = {candidate_key(x) for x in current}
    for h in history:
        if h.get('key') not in current_keys and h.get('listing_status') == '現在取得':
            h['listing_status'] = '現在未取得'
    save_json(CAND_PATH, history)
    return history, new_items


def make_report(all_current: list[dict], history: list[dict], new_items: list[dict], diagnostics: dict, official_sources: dict) -> str:
    counts = {k:0 for k in ['room','address','building','none']}
    for x in all_current:
        typ = x.get('official_match', {}).get('match_type', 'none')
        if typ.startswith('room'): counts['room'] += 1
        elif typ == 'address': counts['address'] += 1
        elif typ == 'building': counts['building'] += 1
        else: counts['none'] += 1

    current_keys = {candidate_key(x) for x in all_current}
    current_history = [h for h in history if h.get('key') in current_keys]
    unreviewed = [h for h in history if h.get('review_status','未確認') == '未確認']

    lines = [
        '広島 民泊候補物件 第8.4弾レポート',
        '='*56, '',
        f"今回取得した候補：{len(all_current)}件",
        f"累計抽出候補：{len(history)}件",
        f"現在掲載候補：{len(current_history)}件",
        f"未確認候補：{len(unreviewed)}件",
        f"今回の新着：{len(new_items)}件",
        '',
        '【第8弾の検索ルール】',
        '・戸建て系：SUUMO / HOME\'S / at home を広く検索',
        '・アパート/マンション等：広島市公式の「民泊届出」または「旅館業許可」一覧と、住所または建物名が明確に一致するものだけ採用',
        '・公式照合は町名だけの一致を採用しない',
        '・公式照合は建物名の部分一致を採用しない',
        '・部屋番号一致だけでは採用せず、建物名/住所の整合を確認',
        '・公式照合は「部屋一致」「住所一致」「建物名一致」を区別',
        '・家賃上限：10万円以下',
        '',
        '【広島市公式PDF取得状況】',
        f"minpaku：{official_sources.get('minpaku',{})}",
        f"ryokan：{official_sources.get('ryokan',{})}",
        '',
        '【サイト別取得状況】',
    ]
    for key, d in diagnostics.items():
        lines.append(f"{key}：{d}")
    if any(d.get('status') not in ('OK',) for d in diagnostics.values()):
        lines += ['', '⚠ 取得0件・タイムアウト・エラーがあります。', 'これは物件0件とは限らず、取得側の問題の可能性があります。']

    def render(title, arr):
        lines.extend(['', title])
        if not arr:
            lines.append('該当なし')
            return
        for i,x in enumerate(arr,1):
            stars = '★★★★★' if x.get('rent',0) <= 50000 else ('★★★★☆' if x.get('rent',0) <= 60000 else ('★★★☆☆' if x.get('rent',0) <= 80000 else '★★☆☆☆'))
            rent = f"{x.get('rent',0):,}円"
            lines.append(f"{i}. {stars} {x.get('ward','')} {rent}")
            lines.append(f"   {x.get('title','')}")
            lines.append(f"   種別：{x.get('kind','')} / 間取り：{x.get('layout','不明')} / 面積：{x.get('area') or '不明'}㎡")
            lines.append(f"   注目点：{'・'.join(x.get('reasons',[])) or '条件該当'}")
            u = x.get('url','')
            lines.append(f"   {u if REAL_URL_RE.match(u) else '物件URL取得失敗（javascript:void(0)等は掲載しません）'}")
    render('【新着（全件）】', new_items)
    render('【今回取得候補（全件）】', all_current)
    render('【累計抽出候補（全件）】', history)
    lines += ['', f"公式照合：部屋一致{counts['room']} / 住所一致{counts['address']} / 建物名一致{counts['building']} / 不一致{counts['none']}", '', '※ review_status / review_notes は candidate_history.json で管理。確認済み候補も履歴から自動削除しません。']
    return '\n'.join(lines)


def main():
    # 公式DBは毎回更新。取得失敗時も既存DBを使って検索を止めない。
    try:
        official_records, official_sources = update_official_db()
    except Exception as e:
        official_records = load_official_db()
        official_sources = {'error': str(e)}

    diagnostics = {}
    all_items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        context = browser.new_context(
            locale='ja-JP',
            timezone_id='Asia/Tokyo',
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1440, 'height': 1200},
            extra_http_headers={'Accept-Language':'ja,en-US;q=0.9,en;q=0.8'},
        )
        page = context.new_page()
        for ward in TARGET_WARDS:
            for site in ['suumo','homes','athome']:
                items, diag = retrieve(page, site, ward)
                key = f'{site} / {ward}'
                diagnostics[key] = diag
                for x in items:
                    all_items.append(x)
        browser.close()

    all_items = merge_current(all_items)

    # 戸建て系は広く採用。集合住宅は公式DBの住所/建物名一致があるものだけ採用。
    filtered = []
    for x in all_items:
        m = match_candidate(x, official_records)
        x['official_match'] = m
        if x.get('kind') == '戸建て系':
            filtered.append(x)
        elif x.get('kind') == '集合住宅' and m.get('match_type') != 'none':
            filtered.append(x)
    all_items = filtered
    history, new_items = update_candidate_history(all_items, official_records)
    save_json(DIAG_PATH, {'updated_at': datetime.now(timezone.utc).isoformat(), 'sites': diagnostics})

    body = make_report(all_items, history, new_items, diagnostics, official_sources)
    send_email('広島 民泊候補物件 第8.4弾レポート', body)


if __name__ == '__main__':
    main()
