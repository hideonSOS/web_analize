"""保有資産の導出と評価額計算

設計の中心原則（models.py の Holding docstring と対応）:
- Holding は「棚卸し時点の期首残高」で固定。日々更新しない
- 現在の保有数 = 棚卸しの数量 + 棚卸しより後に記録した売買日記(DiaryEntry)の増減
  （日記は編集不可の設計なので、この導出は再現可能で安定する）
- 登録画面の数量はこの導出値を表示し、そこで保存した数量が新しい棚卸しになる
  （登録画面は日記に従属する。2026-09-27 ユーザー決定）
- 日記で新規に買った銘柄は Holding が無くても自動で保有に現れる
- 価格は全てDB（Stock.close / ProductPrice / FxRate）から読む。
  ここで外部APIを叩かないこと（表示の高速化と既存機能との方針統一）
"""
from collections import defaultdict

from django.utils import timezone

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
        trades[e.stock_id].append(
            (timezone.localtime(e.recorded_at).date(), e.action, e.shares, e.price, e.created_at))
    return trades, unusable


def _after_stocktake(base_rows, date, created_at):
    """この日記が棚卸しの数量にまだ入っていない（＝加算すべき）か

    棚卸し（登録画面での登録・保存）は、その時点で登録画面に出ていた数量＝それまでに
    記録した日記を反映済みの数量を確定させる操作。なので「棚卸しより後に記録した日記」
    （created_at > baseline_at）だけを加算する。日時で切るので、棚卸しと同じ日に
    後から記録した売買も漏れない。ただし棚卸し日より前の日付で後から書き足した日記は、
    その売買が棚卸しの数量に入っているはずなので加算しない。
    baseline_at の無い行（2026-09-27 より前の登録）は従来どおり日付で切る。
    区分ごとに棚卸しが違う場合は最新を採用する（通常は同時に棚卸しする想定）
    """
    if not base_rows:
        return True
    cutoff_date = max(h.baseline_date for h in base_rows)
    stamps = [h.baseline_at for h in base_rows if h.baseline_at]
    if stamps:
        return created_at > max(stamps) and date >= cutoff_date
    return date > cutoff_date


def _apply_trades(qty, avg, base_rows, entries):
    """棚卸しの数量・単価に、棚卸し後の日記を適用する -> (数量, 平均取得単価, 日記での増減)"""
    delta = 0
    for date, action, shares, price, created_at in entries:
        if not _after_stocktake(base_rows, date, created_at):
            continue
        if action == 'buy':
            new_qty = qty + shares
            avg = (qty * avg + shares * price) / new_qty if new_qty else 0.0
            qty = new_qty
            delta += shares
        else:  # sell
            qty -= shares
            delta -= shares
    return qty, avg, delta


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
        qty, avg, _ = _apply_trades(qty, avg, base_rows, trades.get(code, []))
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


def register_current_values(holdings, setting=None):
    """登録画面用: 保有行ごとの「いま」の数量・単価（日記を反映済み）

    返り値: ({holding.pk: {'quantity','avg_cost','delta'}}, [日記だけで持っている銘柄の行])
    - 登録画面は日記に従属する（2026-09-27 ユーザー決定）。ここで出した数量が入力欄に入り、
      保存するとそれが新しい棚卸しになる（views.register の holding_edit）
    - 同じ銘柄に複数行（口座区分違い）があるときは、日記の増減を最後に棚卸しした行に載せる
      （日記に口座区分が無いため。合計は current_stock_holdings と一致する）
    - 連動OFFなら棚卸しの数量そのまま
    """
    setting = setting or PortfolioSetting.get()
    trades, _ = _diary_trades_by_stock() if setting.link_diary_to_holdings else ({}, [])
    values = {h.pk: {'quantity': h.quantity, 'avg_cost': h.avg_cost, 'delta': 0} for h in holdings}
    by_stock = defaultdict(list)
    for h in holdings:
        if h.stock_id:
            by_stock[h.stock_id].append(h)
    for code, base_rows in by_stock.items():
        if code not in trades:
            continue
        target = max(base_rows, key=lambda h: (h.baseline_at or h.updated_at, h.pk))
        qty, avg, delta = _apply_trades(target.quantity, target.avg_cost, base_rows, trades[code])
        values[target.pk] = {'quantity': qty, 'avg_cost': avg, 'delta': delta}
    diary_only = []
    stocks = {s.code: s for s in Stock.objects.filter(code__in=set(trades) - set(by_stock))}
    for code, entries in trades.items():
        if code in by_stock or code not in stocks:
            continue
        qty, avg, delta = _apply_trades(0.0, 0.0, [], entries)
        if qty > 0:
            diary_only.append({'stock': stocks[code], 'quantity': qty, 'avg_cost': avg})
    return values, diary_only


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
            for date, action, shares, price, _ in entries:
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
