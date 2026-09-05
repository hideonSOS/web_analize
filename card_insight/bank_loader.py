"""銀行明細（三菱UFJ の CSV）→ 台帳行と収入行

なぜ要るか: 家賃・電気・水道・電話は口座振替で払っていて、Zaim の記録経路
（レシート撮影・楽天カード連携・お財布）のどれにも乗らない。実測で10年分の Zaim に
電気代が1件、台帳には0件。月約8.4万円の基礎支出が丸ごと抜けていた。
さらに給料・賞与も入っているので、2025-04 で止まっていた Zaim の収入を埋められる。

⚠️ 銀行明細には**別の経路で既に持っている行**が混ざる。仕分けを間違えると二重計上:
  - 楽天カードの引き落とし … 中身は e-navi の明細で1件ずつ持っている → 除外
  - ATM の引き出し           … 使った先は Zaim のレシートで持っている → 除外
  - 証券口座への振込         … 支出ではない（投資への入金）→ 除外
仕分けは bank_rules.csv（摘要＋摘要内容に対する正規表現）。除外は理由付きで残す。

CSV の列（三菱UFJ ダイレクトの書き出し・cp932）:
  日付, 摘要, 摘要内容, 支払い金額, 預かり金額, 差引残高, メモ, 未資金化区分, 入払区分
"""
from __future__ import annotations

import glob as globmod
import re
from pathlib import Path

import pandas as pd

TREATS = {
    'expense': '支出として台帳へ',
    'card_settlement': 'カード引き落とし（e-naviで明細を持つ）',
    'cash_withdrawal': 'ATM引き出し（使途はZaimのレシート）',
    'investment_transfer': '証券口座との入出金（投資）',
    'income': '収入',
    'ignore': '無視',
}

BANK_NAME = '三菱UFJ銀行'


def looks_like_bank(head: str) -> bool:
    """CSV の見出し行から銀行明細かを判定する。ファイル名には頼らない。"""
    first = (head.replace('"', '').replace('﻿', '').splitlines() or [''])[0]
    cols = [c.strip() for c in re.split(r'[,\t]', first)]
    return '摘要' in cols and '支払い金額' in cols and '預かり金額' in cols


def load_rules(path: str | Path) -> list[tuple[re.Pattern, dict]]:
    df = pd.read_csv(path, encoding='utf-8-sig').fillna('')
    rules = []
    for _, r in df.iterrows():
        pat = str(r.get('pattern') or '').strip()
        if not pat:
            continue
        try:
            rules.append((re.compile(pat), {
                'treat': str(r.get('treat') or 'expense'),
                'category': str(r.get('category') or ''),
                'subcategory': str(r.get('subcategory') or ''),
                'merchant': str(r.get('merchant') or ''),
            }))
        except re.error:
            continue   # 壊れた行を1つ入れても全体は止めない
    return rules


def _to_int(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r'[^\d\-]', '', regex=True), errors='coerce').fillna(0).astype(int)


def load_bank(pattern: str | Path | list, rules_path: str | Path) -> pd.DataFrame:
    """銀行 CSV（複数可）→ 1 取引 1 行。列: date, ym, amount, deposit, summary, detail,
    treat, category, subcategory, merchant, label, shop, balance, source_file

    同じ期間を含むファイルを重ねて置いても (date, summary, detail, amount, deposit, balance)
    で重複を落とす。差引残高まで鍵に入れるのは、同日・同額・同摘要の取引が本当に
    2件あることがあるため（残高が違えば別取引）。
    """
    if isinstance(pattern, (str, Path)):
        paths = sorted(globmod.glob(str(pattern))) if '*' in str(pattern) else [Path(pattern)]
    else:
        paths = list(pattern)
    rules = load_rules(rules_path)

    frames = []
    for p in paths:
        raw = None
        for enc in ('cp932', 'utf-8-sig', 'utf-8'):
            try:
                raw = pd.read_csv(p, encoding=enc)
                break
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        if raw is None or raw.empty or '摘要' not in raw.columns:
            continue
        df = pd.DataFrame({
            'date': pd.to_datetime(raw['日付'], errors='coerce'),
            'summary': raw['摘要'].fillna('').astype(str).str.strip(),
            'detail': raw.get('摘要内容', pd.Series('', index=raw.index)).fillna('').astype(str).str.strip(),
            'amount': _to_int(raw.get('支払い金額', pd.Series(0, index=raw.index))),
            'deposit': _to_int(raw.get('預かり金額', pd.Series(0, index=raw.index))),
            'balance': _to_int(raw.get('差引残高', pd.Series(0, index=raw.index))),
        })
        df['source_file'] = Path(p).name
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=['date', 'ym', 'amount', 'deposit', 'summary', 'detail', 'treat',
                                     'category', 'subcategory', 'merchant', 'label', 'shop', 'balance', 'source_file'])

    df = pd.concat(frames, ignore_index=True)
    df = df[df['date'].notna()]
    df = df.drop_duplicates(['date', 'summary', 'detail', 'amount', 'deposit', 'balance'])
    df['ym'] = df['date'].dt.strftime('%Y-%m')

    def classify(row):
        text = f"{row['summary']} {row['detail']}"
        for pat, r in rules:
            if pat.search(text):
                return r
        # 辞書に無いもの: 入金なら収入扱いにせず無視、支払いなら「未分類」の支出として残す
        # （見落としより見えている方が直しやすい）
        return {'treat': 'expense' if row['amount'] > 0 else 'ignore',
                'category': '未分類', 'subcategory': '未分類', 'merchant': ''}

    cls = df.apply(classify, axis=1, result_type='expand')
    df = pd.concat([df, cls], axis=1)
    # 表示名: 摘要内容があればそれ、無ければ摘要（「給料」「カードＣ１」等）
    df['label'] = df['detail'].where(df['detail'] != '', df['summary'])
    df['shop'] = df['merchant'].where(df['merchant'] != '', df['label'])
    return df.sort_values('date').reset_index(drop=True)
