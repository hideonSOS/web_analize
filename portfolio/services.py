"""保有資産の導出と評価額計算

設計の中心原則（models.py の Holding docstring と対応）:
- Holding は「棚卸し時点の期首残高」で固定。日々更新しない
- 現在の保有数 = 期首 + baseline_date より後の売買日記(DiaryEntry)の増減
  （日記は編集不可の設計なので、この導出は再現可能で安定する）
- 日記で新規に買った銘柄は Holding が無くても自動で保有に現れる
- 価格は全てDB（Stock.close / ProductPrice / FxRate）から読む。
  ここで外部APIを叩かないこと（表示の高速化と既存機能との方針統一）
"""
from collections import defaultdict

from diary.models import DiaryEntry
from japan_kabu.impulse import IMPULSE_SECTORS
from japan_kabu.models import Stock

from .models import CashFlow, FxRate, Holding, PortfolioSetting, Product, ProductPrice


def _build_impulse_theme_map():
    """(国, 表示コード) -> インパルスのセクター名 の逆引き表

    保有銘柄のセクター表示の自動判定に使う。決定順位は
    ①登録フォームでの手動選択 → ②この逆引き（メンバー銘柄なら自動一致）→ ③公式業種。
    インパルス側にセクター・銘柄を足すと、ここも自動で追従する。
    """
    mapping = {}
    for country, sectors in IMPULSE_SECTORS.items():
        for sec in sectors:
            for code in sec['codes']:
                mapping[(country, code)] = sec['name']
    return mapping


IMPULSE_THEME = _build_impulse_theme_map()

# 大分類（ダッシュボードのドーナツ・目標比較と対応）
ASSET_CLASSES = [
    ('stock_jp', '日本株'),
    ('stock_us', '米国株'),
    ('fund', '投資信託'),
    ('metal', '貴金属'),
    ('crypto', '暗号資産'),
    ('cash', '現金'),
]


def latest_fx_rate():
    """最新のドル円レート。未取得なら None"""
    row = FxRate.objects.filter(pair='USDJPY').order_by('-date').first()
    return (row.rate, row.date) if row else (None, None)


def _diary_trades_by_stock(after_date=None):
    """銘柄コード -> [(日付, action, 株数, 約定価格)] （時系列順）

    株数・価格が未入力のエントリは保有計算に使えないためスキップする
    （画面側で「反映できない日記あり」と警告する材料に unusable を返す）。
    """
    qs = (DiaryEntry.objects
          .filter(action__in=['buy', 'sell'], stock__isnull=False)
          .order_by('recorded_at'))
    trades = defaultdict(list)
    unusable = []
    for e in qs:
        if e.shares is None or e.price is None:
            unusable.append(e)
            continue
        trades[e.stock_id].append((e.recorded_at.date(), e.action, e.shares, e.price))
    return trades, unusable


def current_stock_holdings(setting=None):
    """個別株の現在保有リストを導出する

    返り値: (rows, unusable_entries)
    rows の各要素: {stock, quantity, avg_cost, from_diary(期首なしで日記から発生したか)}
    - 同じ銘柄の複数行（口座区分違い）は合算し、平均取得単価は加重平均する
    - 日記連動ON（link_diary_to_holdings）のときのみ:
      買い増しは平均取得単価を移動平均で更新、売りは数量のみ減らす
    - 全量売却済み（数量<=0）は返さない
    """
    setting = setting or PortfolioSetting.get()
    if setting.link_diary_to_holdings:
        trades, unusable = _diary_trades_by_stock()
    else:
        # 連動OFF: 日記は保有計算に一切影響しない（棚卸し登録だけが保有の正）
        trades, unusable = {}, []
    bases = defaultdict(list)
    for h in Holding.objects.filter(stock__isnull=False):
        bases[h.stock_id].append(h)

    rows = []
    stock_ids = set(bases) | set(trades)
    stocks = {s.code: s for s in Stock.objects.filter(code__in=stock_ids)}
    for code in stock_ids:
        base_rows = bases.get(code, [])
        qty = sum(h.quantity for h in base_rows)
        avg = (sum(h.quantity * h.avg_cost for h in base_rows) / qty) if qty else 0.0
        # baseline_date 当日の売買は「棚卸しで入力した数量に反映済み」とみなし、
        # 翌日以降のエントリだけを加算する（同日分の二重計上を防ぐ）。
        # 区分ごとに棚卸し日が違う場合は最新の日付を採用する（通常は同日に棚卸しする想定）
        cutoff = max((h.baseline_date for h in base_rows), default=None)
        for date, action, shares, price in trades.get(code, []):
            if cutoff and date <= cutoff:
                continue
            if action == 'buy':
                new_qty = qty + shares
                avg = (qty * avg + shares * price) / new_qty if new_qty else 0.0
                qty = new_qty
            else:  # sell
                qty -= shares
        if qty <= 0:
            continue
        rows.append({
            'stock': stocks.get(code),
            'quantity': qty,
            'avg_cost': avg,
            'from_diary': not base_rows,
            # 手動セクター（複数行なら最初の設定値）。空なら表示時に公式業種で代用
            'sector': next((h.sector for h in base_rows if h.sector), ''),
            # 投資スタイル（複数行なら最初の設定値）
            'style': next((h.style for h in base_rows if h.style), ''),
        })
    return rows, unusable


def latest_product_prices():
    """商品ID -> (価格, 日付)。各商品の最新1件だけ返す"""
    result = {}
    for p in ProductPrice.objects.order_by('product_id', '-date'):
        if p.product_id not in result:
            result[p.product_id] = (p.price, p.date)
    return result


def cash_balance(setting=None):
    """現金残高 = 期首現金 + 入出金 (+ 日記の売買代金・設定ONのとき)

    ⚠️ 米国株の売買代金の円換算は「最新レート」を使う近似
    （約定日ごとのレート履歴が貯まるまでの割り切り。誤差は数%以内）。
    """
    setting = setting or PortfolioSetting.get()
    balance = setting.baseline_cash
    cutoff = setting.baseline_cash_date

    flows = CashFlow.objects.all()
    if cutoff:
        flows = flows.filter(date__gt=cutoff)
    for f in flows:
        balance += f.signed_amount

    if setting.link_diary_to_cash:
        fx, _ = latest_fx_rate()
        trades, _ = _diary_trades_by_stock()
        for code, entries in trades.items():
            is_us = code.startswith('US-')
            for date, action, shares, price in entries:
                if cutoff and date <= cutoff:
                    continue
                amount = shares * price
                if is_us:
                    amount *= fx or 0  # レート未取得なら反映しない（0円扱いより安全）
                balance += -amount if action == 'buy' else amount
    return balance


def build_portfolio(setting=None):
    """ダッシュボード用の全データを組み立てる

    返り値 dict:
      items: 資産ごとの明細（現金含む・評価額の円換算済み・降順ソート）
      by_class: 大分類ごとの小計 {key: {'label','value','pct'}}
      total / total_cost / unrealized(含み損益) / cash_ratio
      fx_rate / fx_date / unusable_diary(保有へ反映できなかった日記)
      estimated(価格未取得で取得単価による仮評価が混ざっているか)
    """
    setting = setting or PortfolioSetting.get()
    fx, fx_date = latest_fx_rate()
    prod_prices = latest_product_prices()

    items = []
    estimated = False

    stock_rows, unusable = current_stock_holdings(setting)
    for row in stock_rows:
        stock = row['stock']
        if stock is None:
            continue
        is_us = stock.country == 'US'
        price = stock.close
        price_date = stock.price_date
        # 株価未取得（バッチ対象外の銘柄など）は取得単価で仮評価する
        if price is None:
            price = row['avg_cost']
            price_date = None
            estimated = True
        rate = (fx or 0) if is_us else 1
        if is_us and fx is None:
            estimated = True
            rate = 0
        value = row['quantity'] * price * rate
        cost = row['quantity'] * row['avg_cost'] * rate
        items.append({
            'kind': 'stock_us' if is_us else 'stock_jp',
            'code': stock.display_code,
            'name': stock.name,
            'quantity': row['quantity'],
            'unit': '株',
            'avg_cost': row['avg_cost'],
            'price': price,
            'price_date': price_date,
            'currency': '$' if is_us else '¥',
            'value': value,
            'native_value': row['quantity'] * price if is_us else None,
            'pnl': value - cost if cost else None,
            'pnl_pct': (value - cost) / cost * 100 if cost else None,
            'from_diary': row['from_diary'],
            'sector': (row['sector']
                       or IMPULSE_THEME.get((stock.country, stock.display_code))
                       or stock.sector17 or ''),
            'style': row['style'],
            'master_code': stock.code,          # 個別株分析ページでのDD統計参照用
            'change_pct': stock.change_pct,     # 前日比%（バッチ更新値）
        })

    # 商品（投信・貴金属）: 同じ商品の複数行（口座区分違い）は合算・加重平均する
    prod_bases = defaultdict(list)
    for h in Holding.objects.filter(product__isnull=False).select_related('product'):
        prod_bases[h.product].append(h)
    for prod, base_rows in prod_bases.items():
        qty = sum(h.quantity for h in base_rows)
        if qty <= 0:
            continue
        avg = sum(h.quantity * h.avg_cost for h in base_rows) / qty
        price, price_date = prod_prices.get(prod.id, (None, None))
        if price is None:
            price = avg
            price_date = None
            estimated = True
        if prod.category == 'fund':
            value = qty * price / 10000
            cost = qty * avg / 10000
        else:
            value = qty * price
            cost = qty * avg
        items.append({
            'kind': prod.category,
            'code': prod.kind_label,     # 投信 / 金・銀 / ビットコイン 等（category ごとに分岐）
            'name': prod.display_name,
            'quantity': qty,
            'unit': prod.unit_label,
            'avg_cost': avg,
            'price': price,
            'price_date': price_date,
            'currency': '¥',
            'value': value,
            'native_value': None,
            'pnl': value - cost if cost else None,
            'pnl_pct': (value - cost) / cost * 100 if cost else None,
            'from_diary': False,
            'sector': '',
        })

    cash = cash_balance(setting)
    if cash:
        items.append({
            'kind': 'cash', 'code': '￥', 'name': '現金',
            'quantity': None, 'unit': '', 'avg_cost': None,
            'price': None, 'price_date': None, 'currency': '¥',
            'value': cash, 'native_value': None,
            'pnl': None, 'pnl_pct': None, 'from_diary': False, 'sector': '',
        })

    items.sort(key=lambda x: -x['value'])
    total = sum(i['value'] for i in items)

    by_class = {}
    for key, label in ASSET_CLASSES:
        value = sum(i['value'] for i in items if i['kind'] == key)
        by_class[key] = {
            'label': label,
            'value': value,
            'pct': value / total * 100 if total else 0,
        }

    unrealized = sum(i['pnl'] for i in items if i['pnl'] is not None)

    return {
        'items': items,
        'by_class': by_class,
        'total': total,
        'unrealized': unrealized,
        'cash_ratio': by_class['cash']['pct'],
        'fx_rate': fx,
        'fx_date': fx_date,
        'unusable_diary': unusable,
        'estimated': estimated,
    }
