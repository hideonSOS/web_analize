"""楽天e-navi「ご利用明細」CSV の読み込み。

取得方法(手動): 楽天e-navi にログイン → ご利用明細 → 対象月を選択 → 「明細CSVをダウンロード」。
ファイル名は `enavi{YYYYMM}({カード下4桁}).csv`、文字コードは Shift-JIS(cp932)。
2026-09 時点の列:
    利用日, 利用店名・商品名, 利用者, 支払方法, 利用金額, 支払手数料, 支払総額,
    当月支払金額, 翌月繰越残高, 新規サイン
列名は変わることがあるので、候補名の先頭一致で拾う。

※ ログインは自動化しない。認証情報をコードや設定ファイルに置かない(申し送り.md 参照)。
"""
from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

from .zaim_loader import normalize_shop_name

# 内部名 -> e-navi 側で使われうる列名の候補(先頭一致)
COLUMN_CANDIDATES = {
    "date": ["利用日"],
    "merchant": ["利用店名・商品名", "利用店名", "ご利用先"],
    "user": ["利用者"],
    "pay_method": ["支払方法", "支払区分"],
    "amount": ["利用金額", "ご利用金額"],
    "fee": ["支払手数料"],
    "total": ["支払総額"],
    "this_month": ["当月支払金額"],
    "carry_over": ["翌月繰越残高"],
    "new_flag": ["新規サイン"],
}


def _pick(columns: list[str], candidates: list[str]) -> str | None:
    for cand in candidates:
        for col in columns:
            if str(col).strip().startswith(cand):
                return col
    return None


def _read_csv_auto(path: str | Path, encoding: str | None) -> pd.DataFrame:
    """e-navi の CSV は時期により UTF-8(BOM付き) と Shift-JIS が混在するので順に試す。"""
    encodings = [encoding] if encoding else ["utf-8-sig", "cp932"]
    last: Exception | None = None
    for enc in encodings:
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str)
            if any(str(c).startswith("利用日") for c in df.columns):
                return df
        except (UnicodeDecodeError, pd.errors.ParserError) as e:
            last = e
    if last:
        raise last
    return pd.read_csv(path, encoding=encodings[-1], dtype=str)


def load_enavi_file(path: str | Path, encoding: str | None = None) -> pd.DataFrame:
    """e-navi CSV 1 ファイルを内部列名に正規化して返す。"""
    raw = _read_csv_auto(path, encoding)
    raw.columns = [str(c).strip() for c in raw.columns]
    out = pd.DataFrame()
    for key, cands in COLUMN_CANDIDATES.items():
        col = _pick(list(raw.columns), cands)
        out[key] = raw[col] if col is not None else ""
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("amount", "fee", "total", "this_month", "carry_over"):
        out[col] = (
            out[col].astype(str).str.replace(",", "", regex=False).str.replace("¥", "", regex=False)
        )
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    out["merchant"] = out["merchant"].fillna("").astype(str).str.strip()
    out["merchant_norm"] = out["merchant"].map(normalize_shop_name)
    out["pay_method"] = out["pay_method"].fillna("").astype(str).str.strip()
    out["is_installment"] = ~out["pay_method"].isin(["", "1回払い", "1回", "一括"])
    out["source_file"] = Path(path).name
    out = out.dropna(subset=["date"])
    out["ym"] = out["date"].dt.to_period("M").astype(str)
    return out


def load_enavi(paths: list[str | Path] | str | Path) -> pd.DataFrame:
    """複数月の e-navi CSV をまとめて読み込む。glob パターン可。重複行(同ファイル再取込)は除く。"""
    if isinstance(paths, (str, Path)):
        paths = sorted(glob.glob(str(paths)))
    frames = [load_enavi_file(p) for p in paths]
    if not frames:
        return pd.DataFrame(
            columns=[*COLUMN_CANDIDATES.keys(), "merchant_norm", "is_installment", "source_file", "ym"]
        )
    df = pd.concat(frames, ignore_index=True)
    df = dedupe_across_files(df)
    df["enavi_id"] = range(len(df))
    return df


def dedupe_across_files(df: pd.DataFrame) -> pd.DataFrame:
    """ファイル間の重複を除く。

    同じ決済が複数ファイルに現れるケース: 同じ月を再取得して別名で保存した、カード再発行で下 4 桁が変わり
    同じ月のファイルが 2 つある、など。一方、同一ファイル内で同日・同店・同額が 2 行あるのは本物の 2 決済。
    → キー(日付・店・金額・支払方法・利用者)ごとに、各ファイル内の出現数を数え、最も多いファイルの行だけ残す。
    """
    if df.empty:
        return df
    key = ["date", "merchant", "amount", "pay_method", "user"]
    cnt = df.groupby(key + ["source_file"]).size().rename("n").reset_index()
    # 同一キーで出現数が最大のファイル(同数なら名前順で最後 = 通常は新しいカード/新しい取得)を採用
    best = cnt.sort_values(["n", "source_file"]).groupby(key).tail(1)[key + ["source_file"]]
    keep = df.merge(best, on=key + ["source_file"], how="inner")
    return keep.sort_values(["date", "source_file"]).reset_index(drop=True)
