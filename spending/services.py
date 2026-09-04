"""CSV の取り込みと画面用データの組み立て

方針（収支/申し送り.md）:
- 元データ（アップロードされた CSV）は data/spending/ に蓄積し、**台帳は毎回ゼロから作り直す**。
  決定的な処理なので、同じ CSV からは必ず同じ台帳ができる。
- 取り込みは ledger_id で upsert。画面で直した分類（manual_*）は上書きしない。
- ⚠️ CSV の置き場所は MEDIA_ROOT ではない。本番では nginx が /media/ を直接配信して
  Django の認証を通らないため（実測で確認済み）。data/ は nginx から見えない場所に置く。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings

from .models import AmazonOrderItem, ImportLog, MerchantRule, Transaction

# アップロードされた CSV の保管先（nginx が配信しない場所・.gitignore 済み）
DATA_DIR = Path(settings.BASE_DIR) / 'data' / 'spending'
ENAVI_DIR = DATA_DIR / 'enavi'
AMAZON_DIR = DATA_DIR / 'amazon'

MAX_UPLOAD_SIZE = 20 * 1024 * 1024   # 1ファイル20MB（Zaim全期間で約1.5MB）


def _ensure_dirs():
    ENAVI_DIR.mkdir(parents=True, exist_ok=True)
    AMAZON_DIR.mkdir(parents=True, exist_ok=True)


def detect_csv_kind(head: str) -> str:
    """CSV の先頭からどちらの形式かを判定する。'zaim' / 'enavi' / ''

    ファイル名に頼らない（ユーザーがリネームしても動くように）。
    Zaim: 「日付,方法,カテゴリ,...」/ e-navi: 「利用日,利用店名・商品名,...」
    """
    h = head.replace('"', '').replace('﻿', '')
    if '利用日' in h and ('利用店名' in h or '利用者' in h):
        return 'enavi'
    if '日付' in h and ('支出' in h or '収入' in h or '方法' in h):
        return 'zaim'
    # Amazon は書き出し元によって列名が和英・形式ともばらばらなので、
    # 固定文字列ではなく列名の対応表で判定する（amazon_loader 参照）
    from card_insight.amazon_loader import looks_like_amazon
    if looks_like_amazon(h):
        return 'amazon'
    return ''


def read_head(django_file, size=2048) -> str:
    """文字コードを問わず先頭を文字列で読む（Shift-JIS / UTF-8 BOM の揺れに対応）"""
    django_file.seek(0)
    raw = django_file.read(size)
    django_file.seek(0)
    for enc in ('utf-8-sig', 'cp932', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def save_upload(django_file, kind: str) -> Path:
    """アップロードされた CSV を保管先へ保存し、保存先パスを返す。

    Zaim は「最新の1本」を使うので世代を残しつつ最新を採用、
    e-navi は月ごとのファイルなので同名は上書き（毎回16か月分を取り直す運用のため）。
    """
    _ensure_dirs()
    name = Path(django_file.name).name.replace('/', '_').replace('\\', '_')
    dest = (ENAVI_DIR if kind == 'enavi' else DATA_DIR) / name
    with open(dest, 'wb') as f:
        for chunk in django_file.chunks():
            f.write(chunk)
    return dest


def latest_zaim() -> Path | None:
    """保管先にある最新の Zaim CSV（ファイル名にタイムスタンプが入るので名前順で最新）"""
    files = sorted(DATA_DIR.glob('Zaim*.csv'))
    return files[-1] if files else None


def enavi_glob() -> str | None:
    return str(ENAVI_DIR / '*.csv') if any(ENAVI_DIR.glob('*.csv')) else None


def amazon_glob() -> str | None:
    return str(AMAZON_DIR / '*.csv') if any(AMAZON_DIR.glob('*.csv')) else None


def import_amazon() -> dict:
    """Amazon 注文履歴を取り込み、カードの Amazon 行に紐づける。

    ⚠️ 台帳（Transaction）の取り込みより**後**に呼ぶこと。突合先の id が要る。
    台帳と同じく毎回作り直す（手で直す項目を持たないので消して入れ直してよい）。
    """
    import pandas as pd
    from card_insight.amazon_loader import load_amazon
    from card_insight.amazon_match import match

    pattern = amazon_glob()
    if not pattern:
        return {'rows': 0, 'matched': 0, 'charges': 0, 'explained': 0, 'message': ''}

    items = load_amazon(pattern)
    charges = pd.DataFrame(list(
        Transaction.objects.filter(merchant__startswith='Amazon', source_kind='card')
        .values('id', 'date', 'amount')))
    res = match(items, charges)

    AmazonOrderItem.objects.all().delete()
    AmazonOrderItem.objects.bulk_create([
        AmazonOrderItem(
            order_id=r['order_id'], product_name=r['product_name'][:300],
            order_date=(None if pd.isna(r['order_date']) else pd.Timestamp(r['order_date']).date()),
            quantity=int(r['quantity']), item_total=int(r['item_total']),
            order_total=int(r['order_total']), status=str(r['status'])[:40],
            source_file=str(r['source_file'])[:200],
            transaction_id=(None if pd.isna(r['charge_id']) else int(r['charge_id'])),
            match_how=r['match_how'],
        ) for r in res.to_dict('records')
    ], batch_size=500)

    linked = res['charge_id'].notna()
    explained = int(charges[charges['id'].isin(res.loc[linked, 'charge_id'])]['amount'].sum()) if len(charges) else 0
    total = int(charges['amount'].sum()) if len(charges) else 0
    return {
        'rows': int(len(res)), 'matched': int(linked.sum()),
        'charges': int(res.loc[linked, 'charge_id'].nunique()),
        'explained': explained, 'total': total,
        'message': (f'Amazon注文 {len(res):,}商品 → カード請求 '
                    f'{res.loc[linked, "charge_id"].nunique():,}/{len(charges):,}件に紐付け'
                    f'（{explained:,}円 / {total:,}円）'),
    }


def seed_rules_if_empty() -> int:
    """merchant_rules.csv を MerchantRule へ初回シードする。以後は DB が正。"""
    if MerchantRule.objects.exists():
        return 0
    import pandas as pd
    csv = Path(settings.BASE_DIR) / 'card_insight' / 'merchant_rules.csv'
    if not csv.exists():
        return 0
    df = pd.read_csv(csv, encoding='utf-8-sig').fillna('')
    objs = [
        MerchantRule(
            priority=int(r.get('priority') or 100), pattern=str(r['pattern']),
            merchant=str(r.get('merchant') or ''), category=str(r.get('category') or ''),
            subcategory=str(r.get('subcategory') or ''), kind=str(r.get('kind') or '変動'),
            necessity=str(r.get('necessity') or '要確認'), note=str(r.get('note') or ''),
        )
        for _, r in df.iterrows() if str(r.get('pattern') or '').strip()
    ]
    MerchantRule.objects.bulk_create(objs)
    return len(objs)


def rules_dataframe():
    """DB の MerchantRule を card_insight が期待する DataFrame にする。

    DB が空なら CSV をシードしてから読む。card_insight 側は列名で参照するので合わせる。
    """
    import re

    import pandas as pd
    seed_rules_if_empty()
    rows = list(MerchantRule.objects.values(
        'priority', 'pattern', 'merchant', 'category', 'subcategory', 'kind', 'necessity', 'note'))
    if not rows:
        from card_insight.normalize import load_rules
        return load_rules()
    # ⚠️ kind='stable' は必須。classify_name は**先に当たったルールが勝つ**ので、
    # 同じ priority のルール同士の順番が変わると、当たるルールが黙って入れ替わる
    # （既定の quicksort は不安定ソート）。Meta.ordering の priority,id 順を保つこと
    df = pd.DataFrame(rows).fillna('').sort_values('priority', kind='stable').reset_index(drop=True)
    # classify_name が参照するコンパイル済み正規表現。load_rules と同じ形にする
    # （壊れたパターンを1つ入れても全体が落ちないよう、個別に握り潰して無効化する）
    compiled = []
    for p in df['pattern']:
        try:
            compiled.append(re.compile(str(p), re.IGNORECASE))
        except re.error:
            compiled.append(re.compile(r'(?!x)x'))   # 決してマッチしないパターン
    df['_re'] = compiled
    return df


def _trim_to_enavi_period(led, enavi):
    """台帳を「Zaimとe-naviの両方が揃った期間」に絞る（ユーザー方針）

    なぜ絞るか: e-navi は過去16か月しか取得できないため、それ以前は Zaim 単独に
    なり「カード明細の答え合わせができない期間」が混ざる。直近1年の見直しが目的
    なので、揃っている期間だけを台帳に載せて精度を揃える。

    ⚠️ 絞るのは**開始側だけ**。終端は切らない。e-navi は当月分の反映が数日遅れる
    ので、終端を切ると「Zaimにはあるが e-navi にまだ無い直近の支出」が消えてしまう。

    開始月は e-navi の最初の月。ただしその月が途中から始まる場合（例: 4/29 開始）は
    月次グラフで不自然に小さい月になるため、翌月から採用する。
    """
    import pandas as pd
    if enavi is None or not len(enavi) or 'date' not in enavi.columns:
        return led, None
    first = pd.to_datetime(enavi['date']).min()
    if pd.isna(first):
        return led, None
    start = first if first.day == 1 else (first + pd.offsets.MonthBegin(1))
    start_ym = start.strftime('%Y-%m')
    return led[led['ym'] >= start_ym].copy(), start_ym


# --- 取り込み本体 -----------------------------------------------------------

# card_insight の列名 → モデルのフィールド名（同名はそのまま）
_FIELDS = [
    'ledger_id', 'date', 'ym', 'amount', 'source_kind', 'source_name', 'shop', 'shop_norm',
    'merchant', 'label', 'item', 'memo', 'category', 'subcategory', 'category_source',
    'kind', 'necessity', 'match_status', 'enavi_pay_method', 'enavi_is_installment',
    'row_type', 'exclude_reason', 'in_total', 'dup_flag',
]


def import_from_files(zaim_path: Path | None = None, enavi_pattern: str | None = None) -> ImportLog:
    """CSV → 台帳を再構築 → Transaction を upsert。ImportLog を返す。

    手動編集（manual_* / exclude_override）は保持する。台帳から消えた行は削除する
    （Zaim 側で記録を消した場合に追随するため）。
    """
    import pandas as pd
    from card_insight.ledger import build_ledger
    from card_insight.enavi_loader import load_enavi
    from card_insight.zaim_loader import load_zaim

    zaim_path = zaim_path or latest_zaim()
    if zaim_path is None:
        return ImportLog.objects.create(ok=False, message='Zaim の CSV がまだアップロードされていません。')

    enavi_pattern = enavi_pattern if enavi_pattern is not None else enavi_glob()
    try:
        zaim = load_zaim(zaim_path)
        enavi = load_enavi(enavi_pattern) if enavi_pattern else pd.DataFrame()
        led = build_ledger(zaim, enavi, rules_dataframe())['ledger']
        before = len(led)
        led, start_ym = _trim_to_enavi_period(led, enavi)
        trimmed = before - len(led)
    except Exception as e:  # noqa: BLE001  取り込み失敗は画面に出して原因を追えるようにする
        return ImportLog.objects.create(
            ok=False, zaim_file=Path(zaim_path).name,
            message=f'取り込みに失敗しました: {type(e).__name__}: {e}')

    existing = {t.ledger_id: t for t in Transaction.objects.all()}
    seen, to_create, to_update = set(), [], []

    for row in led.to_dict('records'):
        lid = row['ledger_id']
        seen.add(lid)
        values = {}
        for f in _FIELDS:
            v = row.get(f)
            if f == 'date':
                v = pd.Timestamp(v).date()
            elif f in ('in_total', 'enavi_is_installment'):
                v = bool(v)
            elif f == 'amount':
                v = int(v)
            elif v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
                v = ''
            values[f] = v

        obj = existing.get(lid)
        if obj is None:
            to_create.append(Transaction(**values))
            continue
        for k, v in values.items():
            setattr(obj, k, v)
        to_update.append(obj)

    removed = [t for lid, t in existing.items() if lid not in seen]

    Transaction.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        Transaction.objects.bulk_update(to_update, _FIELDS[1:], batch_size=500)
    if removed:
        Transaction.objects.filter(id__in=[t.id for t in removed]).delete()

    amazon = import_amazon()

    return ImportLog.objects.create(
        ok=True,
        zaim_file=Path(zaim_path).name,
        enavi_files=len(list(ENAVI_DIR.glob('*.csv'))),
        rows_total=len(led), rows_added=len(to_create), rows_removed=len(removed),
        message=(
            f'{len(led):,}行を取り込みました'
            f'（新規{len(to_create):,} / 更新{len(to_update):,} / 削除{len(removed):,}）'
            + (f' ／ {start_ym} 以降に限定（e-navi の無い古い{trimmed:,}行は対象外）'
               if start_ym else '')
            + (f' ／ {amazon["message"]}' if amazon['message'] else '')
        ),
    )


def clear_data():
    """保管中の CSV と取り込み済みデータを全部消す（やり直し用）"""
    # Transaction への FK は SET_NULL なので、消し忘れると品目だけが孤児として残る
    AmazonOrderItem.objects.all().delete()
    Transaction.objects.all().delete()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    _ensure_dirs()
