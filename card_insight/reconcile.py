"""Zaim のカード行と e-navi 明細の突合。

方針:
  * 金額完全一致 + 利用日の差が ±date_tolerance 日以内 を候補にする
    (Zaim 連携は e-navi 反映日で取り込むことがあり、数日ずれる)
  * 候補が複数なら 店名の類似度(正規化名の共通接頭辞/部分一致) → 日付差 の順で決める
  * 1 対 1 のグリーディ割当。片方にしかない行は unmatched として残す
  * e-navi の分割/リボ行(is_installment)は「利用金額」で突合する(Zaim も利用額で入る)

結果 DataFrame の列:
  zaim_id, enavi_id, match_status(matched | zaim_only | enavi_only), date_diff_days, name_score
"""
from __future__ import annotations

import pandas as pd


def _name_score(a: str, b: str) -> float:
    a, b = a.lower(), b.lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.8
    # 先頭 4 文字一致でゆるく評価
    if a[:4] == b[:4]:
        return 0.5
    return 0.0


def reconcile(
    card: pd.DataFrame,
    enavi: pd.DataFrame,
    date_tolerance: int = 4,
) -> pd.DataFrame:
    """card: apply_rules 済みの Zaim カード行(zaim_id, date, amount, shop_norm)
    enavi: load_enavi の出力(enavi_id, date, amount, merchant_norm)"""
    if enavi is None or len(enavi) == 0:
        return pd.DataFrame(
            {
                "zaim_id": card["zaim_id"],
                "enavi_id": pd.NA,
                "match_status": "zaim_only",
                "date_diff_days": pd.NA,
                "name_score": pd.NA,
            }
        )

    e = enavi[["enavi_id", "date", "amount", "merchant_norm"]].copy()
    by_amount = {amt: g for amt, g in e.groupby("amount")}
    used_enavi: set[int] = set()
    rows = []
    for _, z in card.sort_values("date").iterrows():
        cands = by_amount.get(int(z["amount"]))
        best = None
        if cands is not None:
            for _, c in cands.iterrows():
                if c["enavi_id"] in used_enavi:
                    continue
                diff = abs((c["date"] - z["date"]).days)
                if diff > date_tolerance:
                    continue
                score = _name_score(str(z.get("shop_norm", "")), str(c["merchant_norm"]))
                key = (score, -diff)
                if best is None or key > best[0]:
                    best = (key, c["enavi_id"], diff, score)
        if best is None:
            rows.append((z["zaim_id"], pd.NA, "zaim_only", pd.NA, pd.NA))
        else:
            used_enavi.add(best[1])
            rows.append((z["zaim_id"], best[1], "matched", best[2], best[3]))
    for eid in e["enavi_id"]:
        if eid not in used_enavi:
            rows.append((pd.NA, eid, "enavi_only", pd.NA, pd.NA))
    return pd.DataFrame(rows, columns=["zaim_id", "enavi_id", "match_status", "date_diff_days", "name_score"])


def merge_detail(card: pd.DataFrame, enavi: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """突合結果を 1 枚の明細に統合する(アプリ側の「カード明細」テーブルの元)。

    優先順位: e-navi の店名・支払方法を正とし、Zaim の分類・メモを付ける。
    e-navi 側にしかない行は Zaim 未取込として amount を e-navi から埋める。
    """
    m = matches.merge(card, on="zaim_id", how="left")
    if enavi is not None and len(enavi):
        e = enavi.rename(
            columns={
                "date": "enavi_date",
                "amount": "enavi_amount",
                "merchant": "enavi_merchant",
                "merchant_norm": "enavi_merchant_norm",
                "pay_method": "enavi_pay_method",
                "user": "enavi_user",
                "is_installment": "enavi_is_installment",
                "source_file": "enavi_file",
                "ym": "enavi_ym",
            }
        )
        m = m.merge(e, on="enavi_id", how="left")
        only_e = m["match_status"] == "enavi_only"
        m.loc[only_e, "date"] = m.loc[only_e, "enavi_date"]
        m.loc[only_e, "ym"] = m.loc[only_e, "enavi_ym"]
        m.loc[only_e, "amount"] = m.loc[only_e, "enavi_amount"]
        m.loc[only_e, "shop"] = m.loc[only_e, "enavi_merchant"]
        m.loc[only_e, "shop_norm"] = m.loc[only_e, "enavi_merchant_norm"]
    return m


def reconcile_summary(matches: pd.DataFrame) -> pd.DataFrame:
    s = matches["match_status"].value_counts().rename_axis("status").reset_index(name="count")
    return s
