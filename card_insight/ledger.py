"""統合台帳(ledger): Zaim の全支出 + 楽天e-navi を「1 決済 = 1 行、重複なし」に統合する。

分析ツールの元データはこの台帳。元データ(Zaim CSV / e-navi CSV)は触らず、毎回ここで作り直す。

行の出どころ(source_kind):
    card    楽天カード払い(Zaim と e-navi を突合。e-navi にしかない行も含む)
    cash    お財布(現金)
    bank    銀行口座からの支払い(家賃・光熱費など。Zaim の銀行連携は 2025/4 で停止している)
    unset   支払元未設定(Zaim のレシート読み取りなど。品目単位の行が多い)

除外(exclude_reason。in_total=False になり、集計に入らない):
    zaim_exclude        Zaim 側で「集計に含めない」(ATM 引出・証券口座への振込など)
    card_settlement     銀行口座からの楽天カード引き落とし(カード明細と二重になる)
    investment_deposit  楽天証券への振込(支出ではなく投資入金。別表で実績として出す)
    dup_cross_card      カード明細と同額・±2日の手入力行(二重計上の疑い)

重複フラグ(dup_flag。参考情報。除外はしない):
    zaim_same           Zaim 内で同日・同額・同店・同品目の 2 件目以降(レシート品目では本物のことが多い)

行種別(row_type): normal / discount(負の金額 = 値引き・ポイント利用・返金。相殺して集計) / zero
ledger_id: 内容(日付|支払元|金額|店|品目|支払方法)のハッシュ。同一内容は出現順で枝番。Zaim 行番号には依存しない
"""
from __future__ import annotations

import hashlib

import pandas as pd

from .normalize import apply_rules, load_rules
from .reconcile import merge_detail, reconcile
from .zaim_loader import extract_card

CARD_PATTERN = r"楽天カード"
BANK_PATTERN = r"UFJ|ゆうちょ|銀行"
CASH_PATTERN = r"お財布|現金"
CARD_SETTLEMENT_PATTERN = r"楽天カードサービ|ラクテンカード"
INVESTMENT_PATTERN = r"ラクテンシヨウケン|楽天証券|SBI証券|マネックス"

# ルール未適用行の必要度(Zaim カテゴリ → 必要度)。節約候補の優先度に使う
CATEGORY_NECESSITY = {
    "食費": "準必須", "医療・保険": "準必須", "交通": "準必須", "通信": "準必須", "電気代": "必須",
    "水道・光熱": "必須", "住まい": "必須", "教育・教養": "準必須", "健康": "準必須",
    "遊び": "裁量", "飲料": "裁量", "書籍": "裁量", "日用雑貨": "要確認", "大型出費": "要確認",
    "その他": "要確認", "未分類": "要確認",
}

LEDGER_COLUMNS = [
    "ledger_id", "date", "ym", "amount", "source_kind", "source_name",
    "shop", "shop_norm", "merchant", "category", "subcategory", "category_source",
    "kind", "necessity", "item", "memo", "label",
    "match_status", "enavi_pay_method", "enavi_is_installment",
    "row_type", "exclude_reason", "in_total", "dup_flag", "zaim_id", "enavi_id", "rule_note", "rule_hit",
]


def _source_kind(source: pd.Series) -> pd.Series:
    kind = pd.Series("unset", index=source.index)
    kind[source.str.contains(CARD_PATTERN, na=False)] = "card"
    kind[source.str.contains(CASH_PATTERN, na=False)] = "cash"
    kind[source.str.contains(BANK_PATTERN, na=False)] = "bank"
    return kind


def _content_keys(led: pd.DataFrame) -> pd.Series:
    """内容ベースの安定 ID。Zaim の行番号はエクスポートごとにずれるので使わない。
    同一内容の行が複数ある場合(レシート品目の同額 2 個など)は出現順の連番を付けて一意にする。"""
    base = (
        led["date"].dt.strftime("%Y-%m-%d") + "|" + led["source_kind"] + "|" + led["amount"].astype(str) + "|"
        + led["shop"].fillna("") + "|" + led["item"].fillna("") + "|" + led["enavi_pay_method"].fillna("")
    )
    seq = base.groupby(base).cumcount()
    return (base + "#" + seq.astype(str)).map(lambda k: hashlib.sha1(k.encode("utf-8")).hexdigest()[:12])


def build_ledger(zaim: pd.DataFrame, enavi: pd.DataFrame | None, rules: pd.DataFrame | None = None,
                 date_tolerance: int = 4, cross_dup_days: int = 2) -> dict:
    """戻り値: dict(ledger, matches, investment_deposits)"""
    rules = load_rules() if rules is None else rules
    pay = zaim[zaim["method"] == "payment"].copy()
    pay["source_kind"] = _source_kind(pay["source"])
    pay["exclude_reason"] = ""
    pay.loc[~pay["include"], "exclude_reason"] = "zaim_exclude"
    inv = pay["shop"].str.contains(INVESTMENT_PATTERN, na=False)
    pay.loc[inv, "exclude_reason"] = "investment_deposit"
    settle = pay["shop"].str.contains(CARD_SETTLEMENT_PATTERN, na=False) & (pay["source_kind"] != "card")
    pay.loc[settle, "exclude_reason"] = "card_settlement"

    # ---- カード行: e-navi と突合(既存ロジック) ----
    card = extract_card(zaim, CARD_PATTERN)
    card = apply_rules(card, rules)
    matches = reconcile(card, enavi, date_tolerance) if enavi is not None and len(enavi) else reconcile(card, pd.DataFrame())
    detail = merge_detail(card, enavi, matches)
    only_e = detail["match_status"] == "enavi_only"
    if only_e.any():
        extra = apply_rules(
            detail.loc[only_e, ["zaim_id", "date", "ym", "amount", "shop"]].assign(category="", subcategory=""), rules
        )
        for c in ("shop_norm", "merchant", "kind", "necessity", "rule_note", "rule_hit",
                  "category_final", "subcategory_final", "category_source"):
            detail.loc[only_e, c] = extra[c].values
    detail["source_kind"] = "card"
    detail["source_name"] = "楽天カード"
    detail["exclude_reason"] = ""
    # Zaim 側の include フラグをカード行にも反映
    inc_map = pay.set_index("zaim_id")["exclude_reason"]
    has_z = detail["zaim_id"].notna()
    detail.loc[has_z, "exclude_reason"] = detail.loc[has_z, "zaim_id"].astype(int).map(inc_map).fillna("")
    detail["item"] = detail.get("item", "").fillna("") if "item" in detail.columns else ""
    detail["memo"] = detail.get("memo", "").fillna("") if "memo" in detail.columns else ""
    if "enavi_pay_method" not in detail.columns:
        detail["enavi_pay_method"] = ""
        detail["enavi_is_installment"] = False

    # ---- 非カード行 ----
    non = pay[pay["source_kind"] != "card"].copy()
    non = non.rename(columns={"expense": "amount"})
    non = apply_rules(non[["zaim_id", "date", "ym", "amount", "category", "subcategory", "shop", "item", "memo",
                           "source", "source_kind", "exclude_reason"]], rules)
    non["source_name"] = non["source"].replace("", "未設定")
    non["match_status"] = ""
    non["enavi_pay_method"] = ""
    non["enavi_is_installment"] = False
    non["enavi_id"] = pd.NA

    # ---- 結合 ----
    # Zaim 由来の category/subcategory は category_final に統合済みなので落としてから改名(列名の重複を防ぐ)
    detail = detail.drop(columns=[c for c in ("category", "subcategory") if c in detail.columns]).rename(columns={"category_final": "category", "subcategory_final": "subcategory"})
    non = non.drop(columns=[c for c in ("category", "subcategory") if c in non.columns]).rename(columns={"category_final": "category", "subcategory_final": "subcategory"})
    frames = []
    for f in (detail, non):
        for c in LEDGER_COLUMNS:
            if c not in f.columns:
                f[c] = pd.NA
        frames.append(f[LEDGER_COLUMNS])
    led = pd.concat(frames, ignore_index=True)
    led["date"] = pd.to_datetime(led["date"])
    led["ym"] = led["date"].dt.to_period("M").astype(str)
    led["amount"] = pd.to_numeric(led["amount"], errors="coerce").fillna(0).astype(int)
    led["exclude_reason"] = led["exclude_reason"].fillna("").astype(str)
    for c in ("shop", "shop_norm", "merchant", "item", "memo", "category", "subcategory", "rule_note",
              "enavi_pay_method", "match_status", "source_name"):
        led[c] = led[c].fillna("").astype(str)
    led["dup_flag"] = ""
    # 表示用の品目名: Zaim アプリで直せるのは「メモ」なので、メモがあればそれを優先し、無ければ品目、無ければ店名
    led["label"] = led["memo"].str.strip()
    led.loc[led["label"] == "", "label"] = led["item"].str.strip()
    led.loc[led["label"] == "", "label"] = led["shop"].str.strip()

    # ---- ルールに当たらなかった行の必要度と表示名を Zaim カテゴリから補う ----
    no_rule = ~led["rule_hit"].fillna(False).astype(bool)
    led.loc[no_rule, "necessity"] = led.loc[no_rule, "category"].map(CATEGORY_NECESSITY).fillna("要確認")
    blank = led["merchant"].str.strip() == ""
    led.loc[blank, "merchant"] = "(店名なし) " + led.loc[blank, "category"]

    # ---- 重複検出 1: Zaim 内の同日・同額・同店・同品目 ----
    z = led["zaim_id"].notna()
    key = ["date", "amount", "shop", "item", "source_name"]
    dup = led[z].duplicated(key, keep="first")
    led.loc[dup[dup].index, "dup_flag"] = "zaim_same"

    # ---- 重複検出 2: カード行と同額・±N日の非カード行(手入力の二重計上疑い) ----
    card_rows = led[(led["source_kind"] == "card") & (led["exclude_reason"] == "")][["date", "amount", "shop_norm"]]
    cand = led[(led["source_kind"] != "card") & (led["exclude_reason"] == "") & (led["amount"] > 0)]
    if len(card_rows) and len(cand):
        m = cand.reset_index().merge(card_rows, on="amount", suffixes=("", "_card"))
        m = m[(m["date"] - m["date_card"]).abs().dt.days <= cross_dup_days]
        # 店名が空(手入力の簡易登録)か、店名の先頭が一致するものだけを疑う
        sim = (m["shop_norm"] == "") | (m["shop_norm"].str[:4] == m["shop_norm_card"].str[:4])
        idx = m.loc[sim, "index"].unique()
        led.loc[idx, "dup_flag"] = "cross_card"
        led.loc[idx, "exclude_reason"] = "dup_cross_card"

    led["in_total"] = led["exclude_reason"] == ""
    led["ledger_id"] = _content_keys(led)
    assert led["ledger_id"].is_unique, "ledger_id が一意ではありません"
    # 行種別: normal / discount(負: 値引き・ポイント利用・返金) / zero
    led["row_type"] = "normal"
    led.loc[led["amount"] < 0, "row_type"] = "discount"
    led.loc[led["amount"] == 0, "row_type"] = "zero"
    led = led.sort_values(["date", "source_kind", "amount"]).reset_index(drop=True)

    invest = pay[inv][["date", "ym", "expense", "shop", "source"]].rename(columns={"expense": "amount"})
    return {"ledger": led[LEDGER_COLUMNS], "matches": matches, "investment_deposits": invest.reset_index(drop=True)}


def ledger_summary(led: pd.DataFrame) -> pd.DataFrame:
    """月 × source_kind の集計(in_total のみ)。"""
    t = led[led["in_total"]]
    pv = t.pivot_table(index="ym", columns="source_kind", values="amount", aggfunc="sum", fill_value=0)
    for k in ("card", "cash", "bank", "unset"):
        if k not in pv.columns:
            pv[k] = 0
    pv = pv[["card", "cash", "bank", "unset"]]
    pv["合計"] = pv.sum(axis=1)
    pv["件数"] = t.groupby("ym")["amount"].count()
    return pv.reset_index()


def exclusion_summary(led: pd.DataFrame) -> pd.DataFrame:
    ex = led[~led["in_total"]]
    return (ex.groupby("exclude_reason")["amount"].agg(件数="count", 合計="sum").reset_index()
            .rename(columns={"exclude_reason": "除外理由"}))
