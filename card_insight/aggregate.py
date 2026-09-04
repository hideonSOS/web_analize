"""統合台帳から分析用の集計テーブルを作る(見せ方は扱わない。数表だけ)。

すべて in_total=True の行だけを対象にする。負の金額(値引き・返金)はそのまま相殺して合計する。

出力(run.py が CSV に書く):
    agg_monthly_source     ym × source_kind            金額・件数
    agg_monthly_category   ym × category × subcategory 金額・件数
    agg_monthly_kind       ym × kind(サブスク/年会費/変動) 金額・件数
    agg_monthly_merchant   ym × merchant × category    金額・件数(店名なしは "(店名なし) カテゴリ")
    agg_yearly_category    year × category             金額・件数・月平均
    agg_merchant_12m       merchant                    直近12か月の合計・件数・月数・中央値・最終日
    agg_items_12m          label(メモ>品目>店名の順で採用した表示名) 直近12か月の合計・件数
"""
from __future__ import annotations

import pandas as pd


def _base(led: pd.DataFrame) -> pd.DataFrame:
    df = led[led["in_total"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    return df


def _recent(df: pd.DataFrame, months: int = 12) -> pd.DataFrame:
    end = df["date"].max().to_period("M")
    return df[df["date"] >= (end - (months - 1)).to_timestamp()]


def _sum_count(g) -> pd.DataFrame:
    return g["amount"].agg(amount="sum", n="count").reset_index()


def monthly_source(led: pd.DataFrame) -> pd.DataFrame:
    return _sum_count(_base(led).groupby(["ym", "source_kind"]))


def monthly_category(led: pd.DataFrame) -> pd.DataFrame:
    return _sum_count(_base(led).groupby(["ym", "category", "subcategory"]))


def monthly_kind(led: pd.DataFrame) -> pd.DataFrame:
    return _sum_count(_base(led).groupby(["ym", "kind"]))


def monthly_merchant(led: pd.DataFrame) -> pd.DataFrame:
    return _sum_count(_base(led).groupby(["ym", "merchant", "category"]))


def yearly_category(led: pd.DataFrame) -> pd.DataFrame:
    df = _base(led)
    g = df.groupby(["year", "category"]).agg(amount=("amount", "sum"), n=("amount", "count"),
                                            months=("ym", "nunique")).reset_index()
    g["monthly_avg"] = (g["amount"] / g["months"]).round(0).astype(int)
    return g


def merchant_12m(led: pd.DataFrame, months: int = 12) -> pd.DataFrame:
    df = _recent(_base(led), months)
    g = df.groupby("merchant").agg(
        amount=("amount", "sum"), n=("amount", "count"), months=("ym", "nunique"),
        median=("amount", "median"), first=("date", "min"), last=("date", "max"),
        category=("category", "first"), kind=("kind", "first"), necessity=("necessity", "first"),
        source_kind=("source_kind", "first"),
    ).reset_index()
    g["monthly_avg"] = (g["amount"] / months).round(0).astype(int)
    return g.sort_values("amount", ascending=False).reset_index(drop=True)


def items_12m(led: pd.DataFrame, months: int = 12) -> pd.DataFrame:
    df = _recent(_base(led), months)
    df = df[df["label"].fillna("").astype(str).str.strip() != ""]
    g = df.groupby(["label", "category"]).agg(amount=("amount", "sum"), n=("amount", "count"),
                                            unit_median=("amount", "median")).reset_index()
    return g.sort_values("amount", ascending=False).reset_index(drop=True)


def build_all(led: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "agg_monthly_source": monthly_source(led),
        "agg_monthly_category": monthly_category(led),
        "agg_monthly_kind": monthly_kind(led),
        "agg_monthly_merchant": monthly_merchant(led),
        "agg_yearly_category": yearly_category(led),
        "agg_merchant_12m": merchant_12m(led),
        "agg_items_12m": items_12m(led),
    }
