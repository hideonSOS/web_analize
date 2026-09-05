"""Amazon 注文履歴 CSV → 正規化 DataFrame

なぜ要るか: カード明細（e-navi）も Zaim も、Amazon の支出は加盟店名
`Amazon.co.jp` としか記録しない。実測で 82 件・約 20 万円が「何を買ったか
分からない支出」として残っており、カード支出の中で最大の塊になっている。
品目まで降りるには注文履歴を 3 つ目のソースとして足すしかない。

⚠️ Amazon の書き出し形式は 1 つではない。実際に使われるのは主に次の 3 系統で、
どれが手に入るかは時期とアカウントによって変わる。**列名で判別**する:

  A. データをリクエスト（アカウントサービス → データをリクエストする → 注文履歴）
     … Your Orders.zip の中の Retail.OrderHistory.3/Retail.OrderHistory.3.csv。
     日本のアカウントでも**列名は英語**。2026年3月に形式が変わり連番が 3 になった。
     デジタル注文は Digital-Ordering.3/Digital Items.csv に分かれる
  B. 注文履歴レポート（注文履歴ページ下部「注文履歴レポートを作成」）… 和英どちらもある
  C. 注文履歴フィルタ（ブラウザ拡張）… 日本語列。**クレカ請求日/請求額を持つ**ので
     突合精度が最も高い

⚠️ A の `Total Owed` は**注文単位ではなく行単位**（その商品にかかった税・送料込みの
負担額）。C の「注文合計」は注文単位で全行に同じ値が入る。同じ order_total 列でも
意味が逆なので、合計の取り方を決め打ちしないこと（amazon_match が両方試す）。

列の対応表さえ増やせば新しい形式にも追従できるようにしてある。
"""
from __future__ import annotations

import glob as globmod
import re
from pathlib import Path

import pandas as pd

# 正規化後の列名 → その列に相当しうる CSV の列名（小文字・空白除去で比較する）
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    'order_id': ('orderid', '注文番号', 'orderid(注文番号)'),
    'order_date': ('orderdate', '注文日', 'ordereddate'),
    # 請求はふつう出荷時に立つので、あるなら注文日より出荷日の方が請求日に近い
    'ship_date': ('shipdate', '出荷日', 'shippeddate'),
    'product_name': ('productname', 'title', '商品名', 'name'),
    'quantity': ('quantity', 'originalquantity', '数量', '個数'),
    # ⚠️ 実物の列の意味（2026-09に実データで確認）:
    #   Total Amount           … その商品1行の負担額（単価＋税＋送料−割引）。これが品目の金額
    #   Shipment Item Subtotal … **出荷単位の小計が全行に繰り返し入る**。行の金額ではない
    # 以前は item_total に Shipment Item Subtotal を当てていたため、
    # 5商品の出荷では同じ913円が5行に入り、合計が実額の5倍になっていた
    # Total Owed は Total Amount の旧称でどちらも行単位。注文合計ではないので注意
    'item_total': ('totalamount', 'totalowed', 'itemtotal', 'itemsubtotal', '商品小計', '価格',
                   'ourpricetax', 'purchasepriceperunit', 'unitprice'),
    'shipment_subtotal': ('shipmentitemsubtotal',),
    # 出荷を一意に特定できる唯一の列。これがあれば出荷単位でまとめられる
    'tracking': ('carriernametrackingnumber', 'carriername&trackingnumber'),
    # ⚠️ 実物(2026-09取得)の列は Total Amount。Total Owed は別形式の名前なので両方要る
    'order_total': ('ordertotal', '注文合計', '請求額'),
    'charge_date': ('クレカ請求日', 'creditcarddate'),
    'charge_amount': ('クレカ請求額', 'creditcardamount'),
    'status': ('orderstatus', '状態', '注文状態'),
    # 支払いに使ったカード。「MasterCard - 9213」のように下4桁が入る。
    # ギフト券払いや別カードの注文はそもそもこのカードの明細に出ないので、
    # 突合できなくて当然だと分かるようにするために持っておく
    'pay_method': ('paymentmethodtype', 'paymentinstrumenttype', '支払方法', 'クレカ種類'),
    # ⚠️ デジタル注文(Digital Content Orders.csv)は 1 商品が「Price Amount」「Tax」の
    # 複数行に分かれて入っている。行をそのまま品目として扱うと金額が本体価格だけに
    # なったり税だけの行が混ざるので、読み込み時に足し合わせて 1 行に畳む
    'component_type': ('componenttype',),
    'transaction_amount': ('transactionamount',),
    'digital_item_id': ('digitalorderitemid',),
}

OUT_COLUMNS = ['order_id', 'order_date', 'ship_date', 'product_name', 'quantity',
               'item_total', 'order_total', 'shipment_subtotal', 'tracking',
               'charge_date', 'charge_amount', 'status', 'pay_method',
               # デジタル注文を1商品1行に畳むために使う（_collapse_components）
               'component_type', 'transaction_amount', 'digital_item_id']

# 注文が成立していない行は請求も発生しないので落とす
CANCELLED = re.compile(r'cancel|キャンセル', re.IGNORECASE)

# 返品・返金のファイルを弾く語。列名にこれを含むものが1つでもあれば購入履歴ではない。
# ⚠️ 完全一致にしないこと。実物は Return Reason Code / Amount Refunded のように
# 語が合成されており、決め打ちのリストでは取りこぼす（実際に Return Requests.csv が
# すり抜けた）。購入履歴側の列に return/refund を含むものは無いと確認済み
RETURN_MARKERS = ('return', 'refund')


def _is_return_file(columns) -> bool:
    return any(m in _key(c) for c in columns for m in RETURN_MARKERS)


def _key(col: str) -> str:
    return re.sub(r'[\s_\-　]', '', str(col)).lower()


def _resolve(columns) -> dict[str, str]:
    """CSV の実列名 → 正規化列名 の対応を作る。先に現れたものを採用する。"""
    found: dict[str, str] = {}
    by_key = {_key(c): c for c in columns}
    for out, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            col = by_key.get(_key(a))
            if col is not None:
                found[out] = col
                break
    return found


def looks_like_amazon(head: str) -> bool:
    """CSV の先頭行から Amazon の注文履歴かを判定する。

    ファイル名に頼らない（書き出し元によって名前がばらばらなため）。
    注文を特定する列と商品名の列が揃っていることを条件にする。
    """
    # ⚠️ 引用符は自分で外すこと。実ファイルの見出しは "Product Name" のように
    # 引用符付きで、外さないと列名が一致せず常に False になる
    first = (head.replace('"', '').replace('﻿', '').splitlines() or [''])[0]
    cols = re.split(r'[,\t]', first)
    # ⚠️ 返品・返金のファイルを弾く。Your Orders.zip には Digital Returns.csv や
    # Return Requests.csv が同居していて、商品名と注文番号を持つため見分けが付かない。
    # 取り込むと「買っていない品目」が請求に紐づきうるので、返品特有の列で除外する
    if _is_return_file(cols):
        return False
    got = _resolve(cols)
    return 'product_name' in got and ('order_id' in got or 'order_date' in got)


def _to_int(series: pd.Series) -> pd.Series:
    """「￥1,234」「1,234円」「1234.0」などを整数の円にする。"""
    s = series.astype(str).str.replace(r'[^\d\.\-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce').fillna(0).round().astype(int)


def _collapse_components(df: pd.DataFrame) -> pd.DataFrame:
    """「本体価格」「税」に分かれた行を 1 商品 1 行に畳む（デジタル注文の形式）

    畳まないと 1 冊の本が 2〜3 行に見え、金額も本体だけ・税だけになって突合できない。
    実測: 「米国会社四季報」は本体 2,946 円 + 税 295 円 の 3 行で、合計 3,241 円が
    実際のカード請求額だった。
    """
    if 'component_type' not in df.columns or df['component_type'].isna().all():
        return df
    if 'transaction_amount' not in df.columns:
        return df
    amt = pd.to_numeric(
        df['transaction_amount'].astype(str).str.replace(r'[^\d.\-]', '', regex=True),
        errors='coerce').fillna(0)
    key = df['digital_item_id'] if df.get('digital_item_id') is not None else None
    if key is None or key.isna().all():
        key = df['order_id'].astype(str) + '|' + df['product_name'].astype(str)
    grouped = df.assign(_amt=amt, _key=key).groupby('_key', sort=False)
    out = grouped.first().reset_index(drop=True)
    out['item_total'] = grouped['_amt'].sum().values
    return out.drop(columns=[c for c in ('_amt', '_key') if c in out.columns])


def load_amazon(pattern: str | Path | list) -> pd.DataFrame:
    """Amazon 注文履歴 CSV（複数可）→ 1 商品 1 行の DataFrame

    同じ注文を含むファイルを重ねて置いても、(注文番号, 商品名, 商品小計) で
    重複を落とす。運用上は「毎回まとめて書き出して置き直す」ことを想定している。
    """
    if isinstance(pattern, (str, Path)):
        paths = sorted(globmod.glob(str(pattern))) if '*' in str(pattern) else [Path(pattern)]
    else:
        paths = list(pattern)

    frames = []
    for p in paths:
        raw = None
        for enc in ('utf-8-sig', 'cp932', 'utf-8'):
            try:
                raw = pd.read_csv(p, encoding=enc)
                break
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        if raw is None or raw.empty:
            continue
        # 返品ファイルがフォルダに紛れ込んでも「買っていない品目」を作らない
        if _is_return_file(raw.columns):
            continue
        got = _resolve(raw.columns)
        if 'product_name' not in got:
            continue
        out = pd.DataFrame(index=raw.index)
        for name in OUT_COLUMNS:
            col = got.get(name)
            out[name] = raw[col] if col is not None else pd.NA
        out['source_file'] = Path(p).name
        frames.append(_collapse_components(out))

    if not frames:
        return pd.DataFrame(columns=OUT_COLUMNS + ['source_file'])

    df = pd.concat(frames, ignore_index=True)
    for c in ('order_date', 'ship_date', 'charge_date'):
        # 「2026-08-26T09:12:00Z」のような形式も混ざるので UTC 解釈してから日付にする
        df[c] = pd.to_datetime(df[c], errors='coerce', utc=True).dt.tz_localize(None)
    for c in ('item_total', 'order_total', 'shipment_subtotal', 'charge_amount'):
        df[c] = _to_int(df[c])
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int)
    for c in ('order_id', 'product_name', 'status', 'pay_method', 'tracking'):
        df[c] = df[c].fillna('').astype(str).str.strip()

    df = df[df['product_name'] != '']
    df = df[~df['status'].str.contains(CANCELLED, na=False)]
    # 注文合計しか無い形式では、商品小計に注文合計を按分せずそのまま入れない
    # （按分すると金額が実在しない値になり、突合で余計に外す）
    df = df.drop_duplicates(['order_id', 'product_name', 'item_total', 'ship_date'])
    return df.sort_values('order_date').reset_index(drop=True)
