"""分析: 月次推移 / サブスク検出 / 歪み検出 / 節約候補 / 入金力試算。

入力はすべて apply_rules 済み(必要なら merge_detail 済み)の明細 DataFrame。
必須列: date, ym, amount, merchant, kind, necessity, category_final, subcategory_final, shop_norm
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 歪み判定のしきい値。アプリ側では設定画面に出す想定
THRESHOLDS = {
    "recent_months": 12,          # 直近何か月を評価対象にするか
    "subscription_min_months": 4, # 何か月以上出現したらサブスク候補か
    "subscription_cv_max": 0.35,  # 金額の変動係数がこれ以下なら「定額」とみなす
    "spike_z": 2.0,               # 月次合計のzスコアがこれ以上なら突出月
    "concentration_share": 0.25,  # 1加盟店がカード支出のこの割合以上なら集中
    "small_order_amount": 3000,   # この金額未満の Amazon 等の注文を「小口」とみなす
    "small_order_per_month": 4,   # 月にこれ以上の小口注文で「衝動買い傾向」
    "unclassified_rate_warn": 0.3,
}


def _recent(df: pd.DataFrame, months: int) -> pd.DataFrame:
    if df.empty:
        return df
    end = df["date"].max().to_period("M")
    start = (end - (months - 1)).to_timestamp()
    return df[df["date"] >= start]


def monthly_totals(df: pd.DataFrame) -> pd.DataFrame:
    """月 × kind(サブスク/年会費/変動) の合計と合計列。"""
    if df.empty:
        return pd.DataFrame()
    pv = df.pivot_table(index="ym", columns="kind", values="amount", aggfunc="sum", fill_value=0)
    for k in ("サブスク", "年会費", "変動"):
        if k not in pv.columns:
            pv[k] = 0
    pv = pv[["サブスク", "年会費", "変動"]]
    pv["合計"] = pv.sum(axis=1)
    pv["件数"] = df.groupby("ym")["amount"].count()
    return pv.reset_index()


def monthly_by_category(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    pv = df.pivot_table(index="ym", columns="category_final", values="amount", aggfunc="sum", fill_value=0)
    return pv.reset_index()


def detect_subscriptions(df: pd.DataFrame, th: dict = THRESHOLDS) -> pd.DataFrame:
    """定期支払い(サブスク)候補を検出する。

    ルールで kind=サブスク/年会費 と明示されたもの + 出現月数と金額安定性から推定したもの。
    """
    r = _recent(df, th["recent_months"])
    if r.empty:
        return pd.DataFrame()
    g = r.groupby("merchant").agg(
        months=("ym", "nunique"),
        n=("amount", "count"),
        total=("amount", "sum"),
        median=("amount", "median"),
        mean=("amount", "mean"),
        std=("amount", "std"),
        first=("date", "min"),
        last=("date", "max"),
        kind=("kind", "first"),
        necessity=("necessity", "first"),
        category=("category_final", "first"),
        note=("rule_note", "first"),
    )
    g["cv"] = (g["std"].fillna(0) / g["mean"]).round(2)
    inferred = (g["months"] >= th["subscription_min_months"]) & (g["cv"] <= th["subscription_cv_max"])
    explicit = g["kind"].isin(["サブスク", "年会費"])
    g = g[inferred | explicit].copy()
    g["判定"] = np.where(g["kind"].isin(["サブスク", "年会費"]), "ルール", "推定")
    g["月額換算"] = (g["total"] / th["recent_months"]).round(0).astype(int)
    g["年額換算"] = g["月額換算"] * 12
    last_ym = r["date"].max().to_period("M")
    g["直近月に発生"] = g["last"].dt.to_period("M") >= (last_ym - 1)
    g = g.sort_values("年額換算", ascending=False).reset_index()
    return g[
        ["merchant", "判定", "kind", "necessity", "category", "months", "n", "median", "cv",
         "月額換算", "年額換算", "first", "last", "直近月に発生", "note"]
    ]


def detect_distortions(df: pd.DataFrame, th: dict = THRESHOLDS) -> pd.DataFrame:
    """支出の「歪み」を列挙する。1 行 1 指摘。"""
    findings: list[dict] = []
    r = _recent(df, th["recent_months"])
    if r.empty:
        return pd.DataFrame(columns=["種別", "対象", "指標", "値", "説明"])
    total = r["amount"].sum()

    # 1) 未分類率
    unc = (r["category_final"] == "未分類").mean()
    if unc >= th["unclassified_rate_warn"]:
        findings.append(dict(種別="分類", 対象="未分類率", 指標="件数割合", 値=round(unc, 2),
                             説明="分類できていない明細が多い。merchant_rules.csv にルール追加、Amazon は注文履歴で品目補完"))

    # 2) 加盟店集中
    share = r.groupby("merchant")["amount"].sum().sort_values(ascending=False) / total
    for m, s in share.head(5).items():
        if s >= th["concentration_share"]:
            findings.append(dict(種別="集中", 対象=m, 指標="カード支出に占める割合", 値=round(s, 2),
                                 説明="1 加盟店に支出が集中。内訳(品目)を分解しないと節約判断ができない"))

    # 3) 月次スパイク
    mt = r.groupby("ym")["amount"].sum()
    if len(mt) >= 4 and mt.std() > 0:
        z = (mt - mt.mean()) / mt.std()
        for ym, zz in z.items():
            if zz >= th["spike_z"]:
                top = r[r["ym"] == ym].groupby("merchant")["amount"].sum().sort_values(ascending=False).head(3)
                findings.append(dict(種別="突出月", 対象=ym, 指標="zスコア", 値=round(float(zz), 2),
                                     説明="上位: " + ", ".join(f"{k} {int(v):,}" for k, v in top.items())))

    # 4) 小口注文の頻度(衝動買い傾向)
    small = r[(r["amount"] < th["small_order_amount"]) & (r["kind"] == "変動")]
    per_month = small.groupby(["ym", "merchant"]).size().reset_index(name="n")
    hot = per_month[per_month["n"] >= th["small_order_per_month"]]
    for m, g in hot.groupby("merchant"):
        findings.append(dict(種別="小口頻発", 対象=m, 指標="該当月数", 値=int(len(g)),
                             説明=f"{th['small_order_amount']:,}円未満の注文が月{th['small_order_per_month']}件以上ある月が{len(g)}か月。まとめ買い/欲しいものリスト運用で削減余地"))

    # 5) 同一目的サブスクの重複
    ai = r[(r["subcategory_final"] == "AIツール") & (r["kind"] == "サブスク")]["merchant"].unique()
    if len(ai) >= 2:
        findings.append(dict(種別="重複", 対象="AIツール", 指標="契約数", 値=int(len(ai)),
                             説明="同目的のサブスクが複数: " + ", ".join(ai) + "。1 本に寄せられないか"))
    srv = r[(r["subcategory_final"] == "VPN・サーバ")]["merchant"].unique()
    if len(srv) >= 2:
        findings.append(dict(種別="重複", 対象="VPN・サーバ", 指標="契約数", 値=int(len(srv)),
                             説明="サーバ/VPN 系が複数: " + ", ".join(srv) + "。用途と台数の棚卸し"))

    # 6) 用途不明な海外決済
    unknown = r[(r["necessity"] == "要確認") & r["shop"].astype(str).str.contains("利用国", na=False)]
    if len(unknown):
        tot = int(unknown["amount"].sum())
        findings.append(dict(種別="用途不明", 対象="海外決済", 指標="合計", 値=tot,
                             説明="内容が特定できない海外決済。" + ", ".join(unknown["merchant"].unique()[:6])))

    # 7) 直近月のサブスク比率
    last_ym = r["date"].max().to_period("M")
    last3 = r[r["date"] >= (last_ym - 2).to_timestamp()]
    if len(last3):
        fixed = last3[last3["kind"].isin(["サブスク", "年会費"])]["amount"].sum() / last3["amount"].sum()
        findings.append(dict(種別="構成", 対象="固定費比率(直近3か月)", 指標="金額割合", 値=round(float(fixed), 2),
                             説明="カード支出のうちサブスク・年会費の割合。高いほど解約 1 回で効く"))

    return pd.DataFrame(findings)


def savings_candidates(subs: pd.DataFrame, df: pd.DataFrame, th: dict = THRESHOLDS) -> pd.DataFrame:
    """節約候補を「年間効果額」順に並べる。

    候補 = necessity が 裁量/要確認 のサブスク + 小口頻発の変動費(半減を仮定)。
    効果額は「解約した場合」または「半減した場合」の年額。実際にやるかは人が決める。
    """
    rows = []
    if subs is not None and len(subs):
        for _, s in subs.iterrows():
            if s["necessity"] in ("裁量", "要確認") and s["直近月に発生"]:
                rows.append(dict(
                    候補=s["merchant"], 種別="サブスク解約/見直し", 現状年額=int(s["年額換算"]),
                    想定効果_年=int(s["年額換算"]), 前提="解約した場合", 優先度=s["necessity"], メモ=s["note"],
                ))
    r = _recent(df, th["recent_months"])
    if len(r):
        # 変動費は「裁量」だけを候補にする(食費・医療などの準必須/要確認は機械的に半減させない)
        var = r[(r["kind"] == "変動") & (r["necessity"] == "裁量") & (r["merchant"].astype(str).str.strip() != "")]
        g = var.groupby("merchant")["amount"].sum().sort_values(ascending=False).head(8)
        for m, v in g.items():
            annual = int(v * 12 / th["recent_months"])
            rows.append(dict(
                候補=m, 種別="変動費の抑制", 現状年額=annual, 想定効果_年=annual // 2,
                前提="半減した場合", 優先度="裁量", メモ="内訳を確認してから目標額を決める",
            ))
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("想定効果_年", ascending=False).reset_index(drop=True)
    return out


def investment_capacity(df_all_expense_monthly: pd.Series, candidates: pd.DataFrame) -> pd.DataFrame:
    """入金力の試算。現状の月平均支出と、節約候補を実行した場合の増分。"""
    cur = float(df_all_expense_monthly.mean()) if len(df_all_expense_monthly) else 0.0
    eff = int(candidates["想定効果_年"].sum()) if candidates is not None and len(candidates) else 0
    return pd.DataFrame(
        [
            dict(項目="現状の月平均支出(Zaim 全体・集計対象)", 値=int(round(cur))),
            dict(項目="節約候補の年間効果額(全部実行時)", 値=eff),
            dict(項目="→ 月あたりの追加入金力", 値=eff // 12),
            dict(項目="→ 年 5% 複利で 10 年積立した場合の概算", 値=int(eff * ((1.05 ** 10 - 1) / 0.05))),
        ]
    )
