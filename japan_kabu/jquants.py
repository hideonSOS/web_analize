"""J-Quants API V2 クライアント

認証は x-api-key ヘッダー方式（settings.JQUANTS_API_KEY）。
V2ではレスポンスが {"data": [...]} 形式で、カラム名は短縮形。
  master: Code, CoName, S33Nm, MktNm, ProdCat など
  bars  : Date, Code, C(終値), AdjC など
  fins  : DiscDate, Code, ShOutFY(期末発行済株式数・自己株含む) など
"""
import time

import requests
from django.conf import settings

BASE_URL = "https://api.jquants.com/v2"
# 連続呼び出し時のウェイト（秒）。レートリミット対策
REQUEST_WAIT = 0.3
# 429（レートリミット）時のリトライ回数と待機秒
RETRY_MAX = 5
RETRY_WAIT = 15


def _get(path, params=None):
    headers = {"x-api-key": settings.JQUANTS_API_KEY}
    for attempt in range(RETRY_MAX + 1):
        res = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=60)
        if res.status_code == 429 and attempt < RETRY_MAX:
            time.sleep(RETRY_WAIT * (attempt + 1))
            continue
        res.raise_for_status()
        return res.json()


def get_master():
    """上場銘柄マスタ（全銘柄）"""
    return _get("/equities/master").get('data', [])


def get_bars_by_date(date):
    """指定日の全銘柄株価四本値。date: 'YYYY-MM-DD'"""
    return _get("/equities/bars/daily", {"date": date}).get('data', [])


def get_bars_by_code(code, from_date=None):
    """指定銘柄の株価四本値"""
    params = {"code": code}
    if from_date:
        params["from"] = from_date
    return _get("/equities/bars/daily", params).get('data', [])


def get_fins_by_date(date):
    """指定開示日の決算サマリー"""
    time.sleep(REQUEST_WAIT)
    return _get("/fins/summary", {"date": date}).get('data', [])


def get_fins_by_code(code):
    """指定銘柄の決算サマリー（全期間）"""
    return _get("/fins/summary", {"code": code}).get('data', [])
