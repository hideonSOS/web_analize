"""前回の台帳との差分。毎回の取込で「何が増え、何が消え、何が変わったか」を機械的に検証する。

ledger_id は内容ハッシュなので、内容が変わった行は「削除 + 追加」として現れる。
それを (date, source_kind, amount) で突き合わせて「変更」に寄せる。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

COMPARE_COLS = ["date", "ym", "amount", "source_kind", "shop", "item", "category", "subcategory",
                "exclude_reason", "in_total", "match_status"]


def diff_ledgers(prev: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """戻り値: change(added / removed / changed) と両方の主要列を持つ DataFrame。"""
    prev = prev.copy(); new = new.copy()
    for f in (prev, new):   # 片方は CSV 由来の文字列、片方は datetime なので揃える
        f["date"] = pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")
        f["amount"] = pd.to_numeric(f["amount"], errors="coerce").fillna(0).astype(int)
    p = prev.set_index("ledger_id")
    n = new.set_index("ledger_id")
    added_ids = n.index.difference(p.index)
    removed_ids = p.index.difference(n.index)
    added = n.loc[added_ids, [c for c in COMPARE_COLS if c in n.columns]].copy()
    removed = p.loc[removed_ids, [c for c in COMPARE_COLS if c in p.columns]].copy()

    # 「削除 + 追加」で日付・支払元・金額が同じものは「変更」(店名や分類の表記変更)として対にする
    key = ["date", "source_kind", "amount"]
    a = added.reset_index()
    r = removed.reset_index()
    a["_k"] = a.groupby(key).cumcount()
    r["_k"] = r.groupby(key).cumcount()
    pair = a.merge(r, on=key + ["_k"], suffixes=("", "_prev"))
    changed_ids_new = set(pair["ledger_id"])
    changed_ids_old = set(pair["ledger_id_prev"])

    rows = []
    for _, x in a[~a["ledger_id"].isin(changed_ids_new)].iterrows():
        rows.append({"change": "added", **{c: x.get(c) for c in ["ledger_id"] + COMPARE_COLS if c in x}})
    for _, x in r[~r["ledger_id"].isin(changed_ids_old)].iterrows():
        rows.append({"change": "removed", **{c: x.get(c) for c in ["ledger_id"] + COMPARE_COLS if c in x}})
    for _, x in pair.iterrows():
        what = [c for c in COMPARE_COLS if c in x and c + "_prev" in x and str(x[c]) != str(x[c + "_prev"])]
        rows.append({"change": "changed", "ledger_id": x["ledger_id"], "ledger_id_prev": x["ledger_id_prev"],
                     "changed_cols": ",".join(what),
                     **{c: x.get(c) for c in COMPARE_COLS if c in x},
                     **{c + "_prev": x.get(c + "_prev") for c in ("shop", "item", "category", "subcategory", "exclude_reason", "match_status") if c + "_prev" in x}})
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["change", "date"]).reset_index(drop=True)
    return out


def diff_summary(d: pd.DataFrame) -> str:
    if d is None or len(d) == 0:
        return "前回台帳との差分: なし"
    c = d["change"].value_counts().to_dict()
    amt = d[d["change"] == "added"]["amount"].sum() - d[d["change"] == "removed"]["amount"].sum()
    return (f"前回台帳との差分: 追加 {c.get('added', 0)} / 削除 {c.get('removed', 0)} / 変更 {c.get('changed', 0)}"
            f" (追加−削除の金額 {int(amt):+,} 円)")


def load_prev(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path, dtype={"ledger_id": str})
