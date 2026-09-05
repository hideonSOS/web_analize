"""Amazon 注文履歴 → カード明細の突合

カードの請求は**注文単位ではなく出荷単位**で立つ。1 注文が複数の請求に割れるし、
同じ日に複数注文があれば別々の請求になる。そのため「注文合計 = 請求額」が必ず
成り立つとは限らない。次の順に、確実なものから割り当てる:

  1. クレカ請求日・請求額を持つ形式（注文履歴フィルタ）→ その値で直接突合
  2. **出荷単位**の合計で突合 ← 請求が立つ単位そのものなので本命
  3. 注文単位の合計で突合（1 注文 = 1 出荷だった場合）
  4. 商品 1 行の金額で突合（1 商品だけの出荷）
  5. 1 注文が複数の請求に割れた分（実データで確認: 3,986 円の注文が 1,993 円 ×2 で
     請求されていた）。**合計が完全一致する組み合わせがある場合のみ**結ぶ

出荷は追跡番号（Carrier Name & Tracking Number）で特定する。無い形式では
(注文番号, 出荷日, 出荷小計) を代わりの鍵にする。

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

    # 出荷単位。請求が立つ単位そのものなので、注文単位より先に試す
    if len(rest):
        key = rest['tracking'].fillna('').astype(str).str.strip() if 'tracking' in rest.columns else pd.Series('', index=rest.index)
        # 追跡番号が無い行は (注文番号, 出荷日, 出荷小計) で代用する
        fallback = (rest['order_id'].astype(str) + '|'
                    + rest['ship_date'].astype(str) + '|'
                    + rest.get('shipment_subtotal', pd.Series(0, index=rest.index)).astype(str))
        key = key.where(key != '', fallback)
        for _k, g in rest.groupby(key):
            # 行ごとの金額の合計。これが基本
            cands = {int(g['item_total'].sum())}
            # 行ごとの金額を持たない形式のために、出荷小計そのものも候補に入れる
            # （出荷単位で全行に同じ値が入る列なので合計せず代表値を取る）
            if 'shipment_subtotal' in g.columns:
                cands.add(int(g['shipment_subtotal'].max()))
            for total in cands:
                if total > 0 and len(g) < len(rest):
                    out.append({'date': _base_date(g), 'amount': total,
                                'idx': list(g.index), 'how': 'shipment'})

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
    order = {'charge': 0, 'shipment': 1, 'order': 2, 'item': 3}
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

    _match_split_orders(res, ch, used, tolerance)
    return res


MAX_SPLIT = 3        # 1 注文がいくつの請求に割れるところまで見るか
MAX_CANDIDATES = 12  # 組み合わせ探索に渡す請求の上限（総当たりが膨らむのを防ぐ）


def _match_split_orders(res: pd.DataFrame, ch: pd.DataFrame, used: set, tolerance: int) -> None:
    """1 注文が複数の請求に割れた分を、合計が完全一致する組み合わせでだけ結ぶ。

    Amazon は出荷ごとに請求を立てるので、まとめ買い 1 注文が同額の請求 2 本に
    割れることが実際にある（実データ: 烏龍茶 2L×9 本 3,986 円 → 1,993 円 ×2）。
    ⚠️ 近い金額に寄せない。合計がぴったり合う組み合わせが見つかったときだけ結ぶ。
    合わないものを無理に埋めると、間違った品目を表示することになる。
    """
    from itertools import combinations

    still = res[res['charge_id'].isna()]
    for oid, g in still.groupby('order_id'):
        if not oid:
            continue
        total = int(g['item_total'].sum())
        base = _base_date(g)
        if total <= 0 or pd.isna(base):
            continue
        cand = ch[(~ch['id'].isin(used))
                  & ((ch['date'] - base).abs().dt.days <= tolerance)
                  & (ch['amount'] <= total)]
        if len(cand) < 2:
            continue
        cand = cand.nlargest(MAX_CANDIDATES, 'amount')
        rows = list(cand.itertuples(index=False))
        for n in range(2, MAX_SPLIT + 1):
            hit = next((c for c in combinations(rows, n)
                        if sum(x.amount for x in c) == total), None)
            if hit is None:
                continue
            # 商品行は最初の請求に寄せる（1 品目を複数請求に按分しても意味がないため）
            for x in hit:
                used.add(x.id)
            res.loc[g.index, 'charge_id'] = hit[0].id
            res.loc[g.index, 'match_how'] = 'split'
            break
