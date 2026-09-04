"""Zaim エクスポート CSV の読み込みと正規化。

Zaim の CSV は Shift-JIS(cp932)、列は以下の 16 列(2026-09 時点):
    日付, 方法, カテゴリ, カテゴリの内訳, 支払元, 入金先, 品目, メモ, お店, 通貨,
    収入, 支出, 振替, 残高調整, 通貨変換前の金額, 集計の設定
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# 列名の日本語 -> 内部名。列が増減しても壊れないよう存在するものだけ改名する。
COLUMN_MAP = {
    "日付": "date",
    "方法": "method",          # payment / income / transfer / balance
    "カテゴリ": "category",
    "カテゴリの内訳": "subcategory",
    "支払元": "source",
    "入金先": "dest",
    "品目": "item",
    "メモ": "memo",
    "お店": "shop",
    "通貨": "currency",
    "収入": "income",
    "支出": "expense",
    "振替": "transfer",
    "残高調整": "adjust",
    "通貨変換前の金額": "amount_raw",
    "集計の設定": "aggregate",
}

DEFAULT_CARD_PATTERN = r"楽天カード"


def load_zaim(path: str | Path, encoding: str = "cp932") -> pd.DataFrame:
    """Zaim CSV を読み込み、内部列名に正規化した DataFrame を返す。"""
    df = pd.read_csv(path, encoding=encoding)
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("income", "expense", "transfer", "adjust", "amount_raw"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in ("category", "subcategory", "source", "dest", "shop", "item", "memo"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
            df.loc[df[col] == "-", col] = ""
    df["include"] = df.get("aggregate", "").astype(str).str.contains("常に集計", na=False)
    df["ym"] = df["date"].dt.to_period("M").astype(str)
    df["zaim_id"] = range(len(df))  # 突合用の安定 ID(行番号)
    return df


def extract_card(df: pd.DataFrame, card_pattern: str = DEFAULT_CARD_PATTERN) -> pd.DataFrame:
    """支払元がカードの支出行だけを返す。balance(残高調整)行は除く。"""
    mask = (df["method"] == "payment") & df["source"].str.contains(card_pattern, na=False, regex=True)
    card = df.loc[mask].copy()
    card = card.rename(columns={"expense": "amount"})
    return card[
        ["zaim_id", "date", "ym", "category", "subcategory", "shop", "item", "memo", "amount", "include"]
    ].reset_index(drop=True)


def card_balance_rows(df: pd.DataFrame, card_pattern: str = DEFAULT_CARD_PATTERN) -> pd.DataFrame:
    """Zaim がカード連携時に付ける残高調整(balance)行。e-navi の請求額との突合に使える。"""
    mask = (df["method"] == "balance") & df["dest"].str.contains(card_pattern, na=False, regex=True)
    return df.loc[mask, ["date", "ym", "adjust"]].reset_index(drop=True)


def unclassified_rate(card: pd.DataFrame) -> float:
    """Zaim 側でカテゴリ未設定の割合(件数ベース)。"""
    if len(card) == 0:
        return 0.0
    unc = (card["category"] == "") | (card["subcategory"].isin(["", "未分類"]))
    return float(unc.mean())


import unicodedata

_ENAVI_PREFIX = re.compile(r"^(?:マスター|VISA|JCB|AMEX)?(?:国内|海外)?利用\s*\d*\s*")  # e-navi「マスター国内利用」「海外利用 1」
_PREFIX = re.compile(r"^(?:M[A-Z]{2}|MZ\w?)\s+")          # MZZ / MYY / MWP などの楽天側プレフィックス
_OVERSEAS = re.compile(r"^海外利用\s*\d+\s*")               # 「海外利用 1 」
_COUNTRY = re.compile(r"\s*利用国\s*[A-Z]{3}\s*$")           # 「利用国USA」


def normalize_shop_name(name: str) -> str:
    """加盟店名の表記ゆれを吸収する(Zaim / e-navi 共通で使う)。

    NFKC で全角英数・半角カナ・全角スペースを統一 → 楽天側の接頭辞/接尾辞を除去 → 大文字化。
    """
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKC", name).strip()
    s = _ENAVI_PREFIX.sub("", s)
    s = _PREFIX.sub("", s)
    s = _OVERSEAS.sub("", s)
    s = _COUNTRY.sub("", s)
    s = s.replace("・", ".").replace("　", " ")
    s = re.sub(r"(?<=[ァ-ヶ])-", "ー", s)                 # カナ直後の半角ハイフンは長音(e-navi の半角カナ由来)
    s = re.sub(r"\s+", " ", s)
    return s.strip().upper()
