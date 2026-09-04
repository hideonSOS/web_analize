"""Amazon 注文履歴 CSV → 正規化 DataFrame

なぜ要るか: カード明細（e-navi）も Zaim も、Amazon の支出は加盟店名
`Amazon.co.jp` としか記録しない。実測で 82 件・約 20 万円が「何を買ったか
分からない支出」として残っており、カード支出の中で最大の塊になっている。
品目まで降りるには注文履歴を 3 つ目のソースとして足すしかない。

⚠️ Amazon の書き出し形式は 1 つではない。実際に使われるのは主に次の 3 系統で、
どれが手に入るかは時期とアカウントによって変わる。**列名で判別**する:

  A. データをリクエスト（Your Orders）… Retail.OrderHistory.1.csv・英語列
  B. 注文履歴レポート（旧 Download order reports）… 和英どちらもある
  C. 注文履歴フィルタ（ブラウザ拡張）… 日本語列。**クレカ請求日/請求額を持つ**ので
     突合精度が最も高い

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
    'product_name': ('productname', 'title', '商品名', 'name'),
    'quantity': ('quantity', '数量', '個数'),
    'item_total': ('shipmentitemsubtotal', 'itemtotal', 'itemsubtotal', '商品小計', '価格',
                   'purchasepriceperunit', 'unitprice'),
    'order_total': ('totalowed', 'ordertotal', '注文合計', '請求額'),
    'charge_date': ('クレカ請求日', 'creditcarddate'),
    'charge_amount': ('クレカ請求額', 'creditcardamount'),
    'status': ('orderstatus', '状態', '注文状態'),
}

OUT_COLUMNS = ['order_id', 'order_date', 'product_name', 'quantity',
               'item_total', 'order_total', 'charge_date', 'charge_amount', 'status']

# 注文が成立していない行は請求も発生しないので落とす
CANCELLED = re.compile(r'cancel|キャンセル', re.IGNORECASE)


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
    cols = [c for c in re.split(r'[,\t]', head.splitlines()[0] if head else '')]
    got = _resolve(cols)
    return 'product_name' in got and ('order_id' in got or 'order_date' in got)


def _to_int(series: pd.Series) -> pd.Series:
    """「￥1,234」「1,234円」「1234.0」などを整数の円にする。"""
    s = series.astype(str).str.replace(r'[^\d\.\-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce').fillna(0).round().astype(int)


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
        got = _resolve(raw.columns)
        if 'product_name' not in got:
            continue
        out = pd.DataFrame(index=raw.index)
        for name in OUT_COLUMNS:
            col = got.get(name)
            out[name] = raw[col] if col is not None else pd.NA
        out['source_file'] = Path(p).name
        frames.append(out)

    if not frames:
        return pd.DataFrame(columns=OUT_COLUMNS + ['source_file'])

    df = pd.concat(frames, ignore_index=True)
    df['order_date'] = pd.to_datetime(df['order_date'], errors='coerce')
    df['charge_date'] = pd.to_datetime(df['charge_date'], errors='coerce')
    for c in ('item_total', 'order_total', 'charge_amount'):
        df[c] = _to_int(df[c])
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int)
    for c in ('order_id', 'product_name', 'status'):
        df[c] = df[c].fillna('').astype(str).str.strip()

    df = df[df['product_name'] != '']
    df = df[~df['status'].str.contains(CANCELLED, na=False)]
    # 注文合計しか無い形式では、商品小計に注文合計を按分せずそのまま入れない
    # （按分すると金額が実在しない値になり、突合で余計に外す）
    df = df.drop_duplicates(['order_id', 'product_name', 'item_total'])
    return df.sort_values('order_date').reset_index(drop=True)
