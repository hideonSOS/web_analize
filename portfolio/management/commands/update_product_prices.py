"""投信の基準価額・金銀の円/g単価・ドル円レートを取得してDBへ蓄積する

日次バッチ（scripts/daily_update.sh に同梱）。画面は常にDBを見るだけなので、
このバッチを回さない限り投信・金銀の評価額は「取得単価による仮評価」のまま。

データ源（すべてAPIキー不要）:
- 投信: 投信協会「投信総合検索ライブラリー」のCSV（isinCd + associFundCd の両方が必要）。
  全履歴が一度に返るため毎回全期間を取得し、新しい日付だけ追記する
- ドル円: yfinance USDJPY=X
- 金銀: yfinance 先物（金=GC=F / 銀=SI=F・ドル/トロイオンス）× ドル円 → 円/グラム。
  楽天証券の店頭価格とはスプレッド分ずれる近似値（毎日同じ基準での比較が目的）

1商品の失敗で全体を止めない（既存バッチと同じ思想）。失敗があれば終了コード1。
"""
import csv
import io
import re
from datetime import date

import requests
from django.core.management.base import BaseCommand

from portfolio.models import FxRate, Product, ProductPrice

FUND_CSV_URL = ('https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download'
                '?isinCd={isin}&associFundCd={assoc}')
TROY_OUNCE_GRAMS = 31.1034768
METAL_TICKERS = {'gold': 'GC=F', 'silver': 'SI=F'}

_DATE_RE = re.compile(r'(\d{4})年(\d{2})月(\d{2})日')


def _parse_jp_date(text):
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


class Command(BaseCommand):
    help = '投信の基準価額・金銀の円/g・ドル円レートを取得してDBへ蓄積する'

    def handle(self, *args, **options):
        failed = False
        fx = self._update_fx()
        if fx is None:
            failed = True
        if not self._update_funds():
            failed = True
        if not self._update_metals(fx):
            failed = True
        if failed:
            raise SystemExit(1)

    # ── ドル円 ────────────────────────────────
    def _update_fx(self):
        """USDJPYの直近終値を保存し、レートを返す。失敗時はDBの最新値を返す"""
        try:
            import yfinance as yf
            hist = yf.Ticker('USDJPY=X').history(period='10d', auto_adjust=True)
            if hist.empty:
                raise RuntimeError('USDJPY=X: データが空')
            saved = 0
            for idx, row in hist.iterrows():
                _, created = FxRate.objects.update_or_create(
                    pair='USDJPY', date=idx.date(),
                    defaults={'rate': float(row['Close'])})
                saved += created
            latest = hist['Close'].iloc[-1]
            self.stdout.write(f'ドル円: {latest:.2f}（新規{saved}日分）')
            return float(latest)
        except Exception as e:
            self.stderr.write(f'ドル円の取得に失敗: {e}')
            row = FxRate.objects.filter(pair='USDJPY').order_by('-date').first()
            return row.rate if row else None

    # ── 投信 ─────────────────────────────────
    def _update_funds(self):
        ok = True
        funds = Product.objects.filter(category='fund')
        for product in funds:
            if not (product.isin and product.assoc_fund_code):
                self.stdout.write(
                    f'{product.display_name}: ISIN/協会コード未設定のためスキップ'
                    '（登録ページの「商品情報を編集」で追記すると自動取得に乗る）')
                continue
            try:
                url = FUND_CSV_URL.format(isin=product.isin, assoc=product.assoc_fund_code)
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                r.encoding = 'shift_jis'
                rows = list(csv.reader(io.StringIO(r.text)))
                if len(rows) < 2 or '基準価額' not in ''.join(rows[0]):
                    raise RuntimeError('CSVの形式が想定と違う（コード誤りの可能性）')
                latest_saved = (ProductPrice.objects
                                .filter(product=product).order_by('-date')
                                .values_list('date', flat=True).first())
                objs = []
                for row in rows[1:]:
                    if len(row) < 2:
                        continue
                    d = _parse_jp_date(row[0])
                    if d is None or not row[1]:
                        continue
                    if latest_saved and d <= latest_saved:
                        continue  # 差分のみ追記（基準価額は過去分が改定されない）
                    objs.append(ProductPrice(product=product, date=d, price=float(row[1])))
                ProductPrice.objects.bulk_create(objs, ignore_conflicts=True)
                last = ProductPrice.objects.filter(product=product).order_by('-date').first()
                # ⚠️ '¥'(U+00A5)はWindowsコンソール(cp932)に無く出力自体が例外になるため使わない
                self.stdout.write(
                    f'{product.display_name}: 新規{len(objs)}日分 '
                    f'（最新 {last.date} {last.price:,.0f}円）')
            except Exception as e:
                self.stderr.write(f'{product.display_name} の取得に失敗: {e}')
                ok = False
        return ok

    # ── 金銀 ─────────────────────────────────
    def _update_metals(self, fx):
        metals = Product.objects.filter(category='metal')
        if not metals:
            return True
        if fx is None:
            self.stderr.write('金銀: ドル円レートが無いため円換算できずスキップ')
            return False
        ok = True
        try:
            import yfinance as yf
        except ImportError:
            self.stderr.write('yfinanceが見つかりません')
            return False
        for product in metals:
            ticker = METAL_TICKERS.get(product.metal)
            if not ticker:
                continue
            try:
                hist = yf.Ticker(ticker).history(period='10d', auto_adjust=True)
                if hist.empty:
                    raise RuntimeError(f'{ticker}: データが空')
                usd_per_toz = float(hist['Close'].iloc[-1])
                bar_date = hist.index[-1].date()
                yen_per_gram = usd_per_toz / TROY_OUNCE_GRAMS * fx
                ProductPrice.objects.update_or_create(
                    product=product, date=bar_date,
                    defaults={'price': yen_per_gram})
                self.stdout.write(
                    f'{product.display_name}: {yen_per_gram:,.0f}円/g（{bar_date}・近似値）')
            except Exception as e:
                self.stderr.write(f'{product.display_name} の取得に失敗: {e}')
                ok = False
        return ok
