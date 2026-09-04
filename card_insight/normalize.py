"""加盟店名の正規化と分類ルールの適用。

ルールは merchant_rules.csv(ユーザーが Excel で編集できる)に置く。
    pattern     : 正規化後の店名に対する正規表現(部分一致、大文字小文字無視)。上から順に最初に当たった行を採用
    merchant    : 表示用の統一名
    category / subcategory : アプリ側の分類。Zaim のカテゴリ体系に寄せてある
    kind        : サブスク / 年会費 / 変動
    necessity   : 必須 / 準必須 / 裁量 / 要確認   (節約候補の優先度に使う)
    note        : 棚卸し時のメモ
Zaim 側で既にカテゴリが付いている行は Zaim を優先し、ルールは「未分類」の穴埋めにだけ使う
(既存の手入力を壊さないため)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .zaim_loader import normalize_shop_name

RULES_PATH = Path(__file__).with_name("merchant_rules.csv")


def load_rules(path: str | Path = RULES_PATH) -> pd.DataFrame:
    rules = pd.read_csv(path, encoding="utf-8").fillna("")
    rules["_re"] = [re.compile(p, re.IGNORECASE) for p in rules["pattern"]]
    return rules


def classify_name(name_norm: str, rules: pd.DataFrame) -> dict:
    for _, r in rules.iterrows():
        if r["_re"].search(name_norm):
            return {
                "merchant": r["merchant"],
                "rule_category": r["category"],
                "rule_subcategory": r["subcategory"],
                "kind": r["kind"],
                "necessity": r["necessity"],
                "rule_note": r["note"],
                "rule_hit": True,
            }
    return {
        "merchant": name_norm,
        "rule_category": "",
        "rule_subcategory": "",
        "kind": "変動",
        "necessity": "要確認",
        "rule_note": "",
        "rule_hit": False,
    }


def apply_rules(card: pd.DataFrame, rules: pd.DataFrame | None = None, shop_col: str = "shop") -> pd.DataFrame:
    """カード明細に正規化名・統一店名・分類を付与する。

    出力列: shop_norm, merchant, kind, necessity, rule_note, rule_hit,
            category_final, subcategory_final, category_source(zaim|rule|none)
    """
    rules = load_rules() if rules is None else rules
    out = card.copy()
    out["shop_norm"] = out[shop_col].map(normalize_shop_name)
    cache: dict[str, dict] = {}
    rows = []
    for name in out["shop_norm"]:
        if name not in cache:
            cache[name] = classify_name(name, rules)
        rows.append(cache[name])
    out = pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)

    has_zaim_cat = out.get("category", pd.Series("", index=out.index)).astype(str).ne("")
    has_zaim_sub = ~out.get("subcategory", pd.Series("", index=out.index)).astype(str).isin(["", "未分類"])
    zaim_ok = has_zaim_cat | has_zaim_sub
    out["category_final"] = out["rule_category"]
    out["subcategory_final"] = out["rule_subcategory"]
    out.loc[zaim_ok, "category_final"] = out.loc[zaim_ok, "category"]
    out.loc[zaim_ok, "subcategory_final"] = out.loc[zaim_ok, "subcategory"]
    out["category_source"] = "none"
    out.loc[out["rule_hit"], "category_source"] = "rule"
    out.loc[zaim_ok, "category_source"] = "zaim"
    out.loc[out["category_final"] == "", "category_final"] = "未分類"
    out.loc[out["subcategory_final"] == "", "subcategory_final"] = "未分類"
    return out
