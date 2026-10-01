import os

EMAIL_USER = os.environ.get('EMAIL_USER', '')
EMAIL_PASS = os.environ.get('EMAIL_PASS', '')
EMAIL_TO = os.environ.get('EMAIL_TO', '')

RENT_MAX = 100000
TARGET_WARDS = ['中区', '南区', '西区', '東区']
SITES = ['suumo', 'homes', 'athome']

# 1 runあたりの取得件数上限。検索サイト側のページングがあるため、十分大きくする。
MAX_RAW_PER_SITE_WARD = 100

# 取得失敗判定。本文が短い場合は検索URLを変えて再試行する。
MIN_BODY_CHARS = 1200
RETRY_COUNT = 3

# Playwright
PAGE_TIMEOUT_MS = 30000
WAIT_AFTER_GOTO_MS = 1800

STATE_FILES = {
    'seen': 'seen.json',
    'price': 'price_history.json',
    'candidate': 'candidate_history.json',
    'diagnostics': 'diagnostics.json',
    'official': 'official_db.json',
}
