"""実行エントリ。

    python -m card_insight.run --zaim Zaim.20260904171044.csv --enavi "enavi/enavi*.csv" --out output

出力:
    output/ledger.csv                 統合台帳(全支出、1 決済 = 1 行、重複なし)。分析ツールの元データはこれ
    output/card_detail.csv            カード分だけの明細(後方互換)
    output/card_insight_report.xlsx   Excel レポート
    output/dashboard.html             ダッシュボード
e-navi CSV が無ければ Zaim 単独で動く。既存アプリへ組み込む場合は build_dataset() を呼ぶ。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import glob
import shutil
from datetime import date

from . import aggregate, analyze, dashboard, diff, report
from .enavi_loader import load_enavi
from .ledger import build_ledger, exclusion_summary, ledger_summary
from .normalize import load_rules
from .reconcile import reconcile_summary
from .zaim_loader import card_balance_rows, extract_card, load_zaim, unclassified_rate


def _analysis_frame(led: pd.DataFrame) -> pd.DataFrame:
    """analyze.* が期待する列名(category_final 等)に合わせた、集計対象のみのフレーム。"""
    df = led[led["in_total"]].copy()
    df = df.rename(columns={"category": "category_final", "subcategory": "subcategory_final"})
    return df


def build_dataset(zaim_path: str | Path, enavi_glob: str | None = None, card_pattern: str = r"楽天カード") -> dict:
    zaim = load_zaim(zaim_path)
    rules = load_rules()
    enavi = load_enavi(enavi_glob) if enavi_glob else pd.DataFrame()

    lg = build_ledger(zaim, enavi, rules)
    led, matches = lg["ledger"], lg["matches"]
    detail = led[led["source_kind"] == "card"].copy()      # カード分(後方互換の card_detail)

    th = analyze.THRESHOLDS
    allf = _analysis_frame(led)                              # 全支出(集計対象)
    cardf = _analysis_frame(detail)                          # カードのみ
    recent_all = analyze._recent(allf, th["recent_months"])
    recent_card = analyze._recent(cardf, th["recent_months"])

    monthly = analyze.monthly_totals(cardf)                  # カードの kind 別月次(既存)
    monthly_all = ledger_summary(led)                        # 全支出の source 別月次
    by_cat = analyze.monthly_by_category(recent_all)         # カテゴリはカード外も含める
    subs = analyze.detect_subscriptions(allf)
    merchants = (
        recent_all.groupby("merchant")
        .agg(total=("amount", "sum"), n=("amount", "count"), kind=("kind", "first"),
             necessity=("necessity", "first"), category=("category_final", "first"),
             source=("source_kind", "first"))
        .sort_values("total", ascending=False).reset_index()
    )
    distortions = analyze.detect_distortions(allf)
    candidates = analyze.savings_candidates(subs, allf)

    all_monthly = allf.groupby("ym")["amount"].sum()
    if len(recent_all):
        all_monthly = all_monthly[all_monthly.index >= str(recent_all["date"].min().to_period("M"))]
    capacity = analyze.investment_capacity(all_monthly, candidates)

    unmatched_rules = (
        recent_all[~recent_all["rule_hit"].fillna(False).astype(bool) & (recent_all["category_source"] != "zaim")]
        .groupby("shop_norm").agg(n=("amount", "count"), total=("amount", "sum"))
        .sort_values("total", ascending=False).reset_index()
    )
    review = led[led["dup_flag"] != ""].copy()
    excluded = led[~led["in_total"]].copy()
    inv = lg["investment_deposits"]
    inv_monthly = inv.groupby("ym")["amount"].sum().reset_index() if len(inv) else pd.DataFrame(columns=["ym", "amount"])

    card_raw = extract_card(zaim, card_pattern)
    return dict(
        zaim=zaim, enavi=enavi, matches=matches, ledger=led, detail=detail,
        monthly=monthly, monthly_all=monthly_all, by_category=by_cat, subscriptions=subs, merchants=merchants,
        distortions=distortions, candidates=candidates, capacity=capacity,
        reconcile_summary=reconcile_summary(matches), unmatched_rules=unmatched_rules,
        review=review, excluded=excluded, exclusion_summary=exclusion_summary(led),
        investment_deposits=inv, investment_monthly=inv_monthly,
        card_balance=card_balance_rows(zaim, card_pattern),
        unclassified_rate_before=unclassified_rate(card_raw),
        unclassified_rate_after=float((allf["category_final"] == "未分類").mean()) if len(allf) else 0.0,
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--zaim", default="Zaim.*.csv",
                    help="Zaim CSV のパスまたは glob。複数あれば名前順で最新(= 最新エクスポート)を使う")
    ap.add_argument("--enavi", default=None, help='glob e.g. "enavi/enavi*.csv"')
    ap.add_argument("--out", default="output")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    zaim_files = sorted(glob.glob(a.zaim))
    if not zaim_files:
        raise SystemExit(f"Zaim CSV が見つかりません: {a.zaim}")
    zaim_path = zaim_files[-1]
    print("zaim:", zaim_path, "" if len(zaim_files) == 1 else f"(他 {len(zaim_files) - 1} 件は無視)")

    ds = build_dataset(zaim_path, a.enavi)
    led = ds["ledger"]

    # 前回台帳との差分(取込のたびに何が変わったかを残す)
    prev = diff.load_prev(out / "ledger.csv")
    if prev is not None:
        d = diff.diff_ledgers(prev, led)
        print(diff.diff_summary(d))
        hist = out / "history"
        hist.mkdir(exist_ok=True)
        stamp = date.today().strftime("%Y%m%d")
        if len(d):
            d.to_csv(hist / f"ledger_diff_{stamp}.csv", index=False, encoding="utf-8-sig")
        shutil.copy(out / "ledger.csv", hist / f"ledger_prev_{stamp}.csv")
    tot = led[led["in_total"]]
    notes = [
        "このブックは card_insight の出力。元データは Zaim CSV(全支出)と enavi/ の楽天e-navi CSV。",
        "『統合台帳』が分析ツールの元データ。1 決済 = 1 行。in_total=TRUE の行だけが支出集計に入る。",
        "source_kind: card=楽天カード(Zaim と e-navi を突合) / cash=お財布 / bank=銀行口座 / unset=支払元未設定(レシート品目など)。",
        "exclude_reason: zaim_exclude=Zaim で集計外 / card_settlement=カード引落(カード明細と二重) / investment_deposit=証券口座への振込 / dup_cross_card=カードと同額±2日の手入力(二重計上疑い)。",
        "dup_flag=zaim_same は Zaim 内の同日同額同店同品目の 2 件目以降。レシート品目では本物が多いので除外していない。『重複候補』シートで確認。",
        "category_source: zaim=Zaim の分類を優先 / rule=merchant_rules.csv で補完 / none=未分類。",
        f"カード明細の未分類率: {ds['unclassified_rate_before']:.0%} → 全支出のルール適用後: {ds['unclassified_rate_after']:.0%}",
        "注意: Zaim の銀行連携は 2025/4 で止まっており、以降の家賃・光熱費(bank)が入っていない。『月次_全支出』の bank 列が 0 の月はその影響。",
        "『投資入金実績』は楽天証券への振込(Zaim で集計外)。入金力の実績値として使う。",
        "『節約候補』の想定効果は『解約した場合』『半減した場合』の機械的な試算。実行可否は人が判断する。",
    ]
    report.write_report(
        out / "card_insight_report.xlsx",
        {
            "サマリー": ds["capacity"],
            "歪み指摘": ds["distortions"],
            "節約候補": ds["candidates"],
            "サブスク一覧": ds["subscriptions"],
            "月次_全支出": ds["monthly_all"],
            "月次推移": ds["monthly"],
            "カテゴリ月次": ds["by_category"],
            "加盟店別": ds["merchants"],
            "統合台帳": led,
            "除外行": ds["excluded"],
            "除外サマリー": ds["exclusion_summary"],
            "重複候補": ds["review"],
            "投資入金実績": ds["investment_deposits"],
            "突合サマリー": ds["reconcile_summary"],
            "ルール未適用店": ds["unmatched_rules"],
            "Zaim残高調整": ds["card_balance"],
        },
        notes,
    )
    payload = dashboard.build_payload(
        ds["monthly"], ds["by_category"], ds["subscriptions"], ds["merchants"],
        ds["distortions"], ds["candidates"], ds["capacity"], ds["reconcile_summary"],
        monthly_all=ds["monthly_all"], investment_monthly=ds["investment_monthly"],
        exclusion_summary=ds["exclusion_summary"],
    )
    dashboard.write_dashboard(out / "dashboard.html", payload)
    led.to_csv(out / "ledger.csv", index=False, encoding="utf-8-sig")
    agg_dir = out / "agg"
    agg_dir.mkdir(exist_ok=True)
    for name, df in aggregate.build_all(led).items():
        df.to_csv(agg_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    ds["detail"].to_csv(out / "card_detail.csv", index=False, encoding="utf-8-sig")
    print(f"ledger rows: {len(led):,} | in_total: {int(led['in_total'].sum()):,} | 除外: {int((~led['in_total']).sum()):,}")
    print("unclassified:", f"{ds['unclassified_rate_before']:.0%} -> {ds['unclassified_rate_after']:.0%}")
    print(ds["reconcile_summary"].to_string(index=False))
    print(ds["exclusion_summary"].to_string(index=False))
    print("wrote:", out / "ledger.csv", out / "agg/*.csv", out / "card_insight_report.xlsx", out / "dashboard.html")


if __name__ == "__main__":
    main()
