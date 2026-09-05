"""Amazon 注文履歴 → カード明細の突合

カードの請求は**注文単位ではなく出荷単位**で立つ。1 注文が複数の請求に割れるし、
同じ日に複数注文があれば別々の請求になる。そのため「注文合計 = 請求額」が必ず
成り立つとは限らない。次の順に、確実なものから割り当てる:

  1. クレカ請求日・請求額を持つ形式（注文履歴フィルタ）→ その値で直接突合
  2. 注文単位の合計で突合
  3. 商品 1 行の小計で突合（1 商品だけの注文が請求 1 本になっている場合）

いずれも**金額完全一致 + 日付が許容内**で、1 請求に 1 グループのグリーディ割当。
金額が合わないものを近い順に無理やり結び付けない（間違った品目を表示するくらいなら
「不明のまま」の方がよい。この機能の目的は使途の解明であって、埋めることではない）。
"""
from __future__ import annotations

import pandas as pd

DATE_TOLERANCE = 7   # 注文日と請求日のずれ。出荷時に請求が立つので e-navi 突合(4日)より広い


def _base_date(g: pd.DataFrame):
    """突合の基準日。請求は出荷時に立つので、出荷日があればそちらを使う。"""
    if 'ship_date' in g.columns and g['ship_date'].notna().any():
        return g['ship_date'].min()
    return g['order_date'].min()


def _groups(items: pd.DataFrame) -> list[dict]:
    """突合の単位（請求1本に対応しうるまとまり）を、確度の高い順に作る。"""
    out = []
    has_charge = items['charge_amount'].gt(0) & items['charge_date'].notna()
    if has_charge.any():
        # ⚠️ 注文番号もキーに含めること。同じ日に同じ金額の請求が2本立つことは
        # 実際にあり（別々の注文が同額）、日付と金額だけでまとめると1本に潰れて
        # もう一方の請求が永久に未突合のまま残る（合成データで5件取りこぼした）
        for (d, amt, _oid), g in items[has_charge].groupby(['charge_date', 'charge_amount', 'order_id']):
            out.append({'date': d, 'amount': int(amt), 'idx': list(g.index), 'how': 'charge'})
    rest = items[~has_charge]

    for oid, g in rest.groupby('order_id'):
        if not oid:
            continue
        # ⚠️ order_total 列の意味は書き出し形式によって逆になる。
        # データをリクエスト形式の Total Owed は**行単位**なので合計が注文額、
        # 注文履歴フィルタの「注文合計」は**注文単位**で全行に同じ値が入るので最大値。
        # どちらか決め打ちすると片方の形式で必ず外すので、候補を全部出して試す
        # （金額完全一致でしか結ばないので、外れの候補は自然に落ちる）。
        for total in {int(g['order_total'].sum()), int(g['order_total'].max()),
                      int(g['item_total'].sum())}:
            if total > 0:
                out.append({'date': _base_date(g), 'amount': total,
                            'idx': list(g.index), 'how': 'order'})

    for i, r in rest.iterrows():
        base = r['ship_date'] if pd.notna(r.get('ship_date')) else r['order_date']
        for amt in {int(r['item_total']), int(r['order_total'])}:
            if amt > 0:
                out.append({'date': base, 'amount': amt, 'idx': [i], 'how': 'item'})
    return out


def match(items: pd.DataFrame, charges: pd.DataFrame,
          tolerance: int = DATE_TOLERANCE) -> pd.DataFrame:
    """items（1商品1行）に、対応するカード請求の id を付けて返す。

    charges は ['id', 'date', 'amount'] を持つ DataFrame（Amazon の請求行だけ）。
    戻り値は items に `charge_id` と `match_how` を足したもの（未突合は欠損）。
    """
    res = items.copy()
    res['charge_id'] = pd.NA
    res['match_how'] = ''
    if res.empty or charges is None or charges.empty:
        return res

    ch = charges.copy()
    ch['date'] = pd.to_datetime(ch['date'])
    used: set = set()
    # 確度の高い順（charge → order → item）に処理し、既に埋まった商品は飛ばす
    order = {'charge': 0, 'order': 1, 'item': 2}
    for g in sorted(_groups(res), key=lambda g: order[g['how']]):
        idx = [i for i in g['idx'] if pd.isna(res.at[i, 'charge_id'])]
        if not idx or pd.isna(g['date']):
            continue
        cand = ch[(~ch['id'].isin(used)) & (ch['amount'] == g['amount'])]
        if cand.empty:
            continue
        diff = (cand['date'] - g['date']).abs().dt.days
        cand = cand[diff <= tolerance]
        if cand.empty:
            continue
        pick = cand.loc[(cand['date'] - g['date']).abs().idxmin()]
        used.add(pick['id'])
        res.loc[idx, 'charge_id'] = pick['id']
        res.loc[idx, 'match_how'] = g['how']
    return res
