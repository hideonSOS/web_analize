"""レシート由来の品目名を整える（分類 + 表示用の掃除）

なぜ要るか: Zaim のレシート撮影は 1 枚のレシートを商品ごとの行に分解するが、
そのとき**商品ではない行**も一緒に作る。実測で 964 件・70,012 円＝全行の 24% が
「外税」「割引」「袋代」「レジ袋」「不明」で、明細を読むときの主な雑音になっていた。

⚠️ これらを集計から外してはいけない。外税 55,913 円は実際に払った消費税であり、
袋代も割引も本物の金額。**商品ではないという印を付けるだけ**にして、画面側で
畳めるようにする。除外すると支出の総額が実態より小さくなる。

文字化け（`j*-,,,,,インヒート` のような OCR 崩れ）は実測 24 件しかなく、
辞書を作るほどの量ではない。表示のときに記号の連続を削るだけにして、
中身の復元は人が明細画面で直す（手動修正は再取込でも保持される）。
"""
from __future__ import annotations

import re

# 品目名 → 行の種類。上から順に評価し、最初に当たったものを採用する
LABEL_KINDS: list[tuple[str, str, str]] = [
    ('tax',      '税',     r'^(外税|内税|消費税|税込|税抜)$'),
    ('discount', '値引',   r'^(割引|値引|割引き|値引き|ポイント値引|クーポン)$'),
    ('bag',      'レジ袋', r'(レジ袋|袋代|手提げ袋|ショッピングバッグ)'),
    ('unknown',  '不明',   r'^(不明|金額|No\.?|小計|合計|お預り|お釣り|)$'),
]
_COMPILED = [(k, label, re.compile(p)) for k, label, p in LABEL_KINDS]

KIND_LABELS = {k: label for k, label, _ in LABEL_KINDS}
KIND_LABELS['item'] = '商品'

# 表示のときに落とす記号の連続（OCR 崩れの残骸）。日本語・英数字は残す
_SYMBOL_RUN = re.compile(r"[!-/:-@\[-`{-~]{2,}")
_SPACES = re.compile(r'\s+')


def classify_label(label: str) -> str:
    """品目名から行の種類を返す。'item' / 'tax' / 'discount' / 'bag' / 'unknown'"""
    s = (label or '').strip()
    for kind, _lbl, pattern in _COMPILED:
        if pattern.search(s):
            return kind
    return 'item'


def clean_label(label: str) -> str:
    """表示用に整える。

    - 改行・連続空白を 1 個の空白に畳む（複数行の品目が表に収まらないため）
    - 記号が 2 つ以上続く箇所を削る（`厳選??無糖` → `厳選無糖`）
    ⚠️ 元の label は残すこと。掃除しすぎて意味が変わったときに戻せなくなる。
    """
    s = _SPACES.sub(' ', (label or '').strip())
    s = _SYMBOL_RUN.sub('', s)
    return _SPACES.sub(' ', s).strip()
