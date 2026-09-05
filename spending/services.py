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

from .models import AmazonOrderItem, ImportLog, LabelRule, MerchantRule, MonthlyIncome, Transaction

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


def decode_head(raw: bytes) -> str:
    """先頭バイト列を文字列にする（Shift-JIS / UTF-8 BOM の揺れに対応）

    ⚠️ 固定長で切ったバイト列は日本語の途中で切れるので、どの文字コードでも
    デコードに失敗しうる。最後は errors='ignore' で必ず文字列を返すこと。
    ここで空文字を返すと形式判別が丸ごと不発になる（実際に踏んだ）。
    """
    for enc in ('utf-8-sig', 'cp932', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def read_head(django_file, size=2048) -> str:
    """アップロードされたファイルの先頭を文字列で読む"""
    django_file.seek(0)
    raw = django_file.read(size)
    django_file.seek(0)
    return decode_head(raw)


def save_upload(django_file, kind: str) -> Path:
    """アップロードされた CSV を保管先へ保存し、保存先パスを返す。

    Zaim は「最新の1本」を使うので世代を残しつつ最新を採用、
    e-navi は月ごとのファイルなので同名は上書き（毎回16か月分を取り直す運用のため）。
    """
    _ensure_dirs()
    name = Path(django_file.name).name.replace('/', '_').replace('\\', '_')
    dest = {'enavi': ENAVI_DIR, 'amazon': AMAZON_DIR}.get(kind, DATA_DIR) / name
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


def adopt_stray_amazon_csvs() -> list[str]:
    """DATA_DIR 直下に紛れた Amazon の CSV を amazon/ へ移す。

    なぜ要るか: 保存先の振り分けに不具合があり、アップロードされた Amazon の CSV が
    DATA_DIR 直下に落ちて**黙って無視されていた**（実際に本番で発生）。
    振り分けは直したが、既に置かれてしまったファイルは拾えないままになる。
    Zaim は `Zaim*.csv` で名前指定、e-navi は enavi/ にあるので、直下の CSV のうち
    中身が Amazon のものだけを移せば取り違えない。
    """
    moved = []
    for p in DATA_DIR.glob('*.csv'):
        if p.name.startswith('Zaim'):
            continue
        try:
            with open(p, 'rb') as f:
                head = decode_head(f.read(2048))
        except OSError:
            continue
        # ⚠️ looks_like_amazon を直接呼ばないこと。実ファイルの列名は "Product Name" と
        # 引用符付きで、detect_csv_kind が引用符を外してから渡している。生のまま渡すと
        # 常に False になる（実際にこれで救済が不発だった）
        if detect_csv_kind(head) == 'amazon':
            _ensure_dirs()
            p.replace(AMAZON_DIR / p.name)
            moved.append(p.name)
    return moved


def amazon_glob() -> str | None:
    adopt_stray_amazon_csvs()
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


def import_income(zaim_path: Path) -> dict:
    """Zaim CSV の収入行 → MonthlyIncome（source='zaim'）を作り直す。

    ⚠️ 台帳と違い**期間で絞らない**。収入は e-navi と突合しないので、
    揃っている期間に合わせる理由が無い。むしろ過去の水準が分かる方が有益。
    手入力（source='manual'）には触らない。
    """
    import pandas as pd
    try:
        raw = None
        for enc in ('cp932', 'utf-8-sig', 'utf-8'):
            try:
                raw = pd.read_csv(zaim_path, encoding=enc)
                break
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        if raw is None or '収入' not in raw.columns:
            return {'months': 0, 'total': 0, 'message': ''}
        raw['amount'] = pd.to_numeric(raw['収入'], errors='coerce').fillna(0)
        inc = raw[raw['amount'] > 0].copy()
        if inc.empty:
            return {'months': 0, 'total': 0, 'message': ''}
        inc['ym'] = inc['日付'].astype(str).str[:7]
        g = inc.groupby('ym')['amount'].sum()
    except Exception:  # noqa: BLE001  収入が取れなくても支出の取り込みは通したい
        return {'months': 0, 'total': 0, 'message': ''}

    MonthlyIncome.objects.filter(source='zaim').delete()
    MonthlyIncome.objects.bulk_create([
        MonthlyIncome(ym=ym, amount=int(v), source='zaim') for ym, v in g.items()
    ])
    last = max(g.index)
    return {
        'months': int(len(g)), 'total': int(g.sum()), 'last': last,
        'message': f'収入 {len(g)}か月分を取り込み（最終 {last}）',
    }


def seed_label_rules_if_empty() -> int:
    """label_rules.csv を LabelRule へ初回シードする。以後は DB が正（sync_label_rules で反映）。"""
    if LabelRule.objects.exists():
        return 0
    import pandas as pd
    csv = Path(settings.BASE_DIR) / 'card_insight' / 'label_rules.csv'
    if not csv.exists():
        return 0
    df = pd.read_csv(csv, encoding='utf-8-sig').fillna('')
    objs = [
        LabelRule(priority=int(r.get('priority') or 100), pattern=str(r['pattern']),
                  category=str(r.get('category') or ''), subcategory=str(r.get('subcategory') or ''),
                  note=str(r.get('note') or ''))
        for _, r in df.iterrows() if str(r.get('pattern') or '').strip()
    ]
    LabelRule.objects.bulk_create(objs)
    return len(objs)


def apply_label_rules(led):
    """品目名に当たる LabelRule で category/subcategory を差し替える。

    ⚠️ ここは **Zaim の分類より優先**する唯一の経路。Zaim のレシート撮影は品目名から
    分類を学習し、一度誤ると同じ品目が毎回同じ誤りで入ってくる（ごぼう→通信 が18回）。
    明細の手動修正はその行にしか効かないので、翌月の同じ品目には取り込み時に効く
    仕組みが要る。差し替えた行は category_source='fix' にして出どころを追えるようにする。

    ⚠️ レシート付随行（外税・割引・レジ袋）には当てない。正しい分類はレシート次第で
    品目名だけでは決まらないため（label_kind が item の行だけを対象にする）。
    """
    import re
    seed_label_rules_if_empty()
    rules = list(LabelRule.objects.all())
    if not rules or 'label' not in led.columns:
        return led
    compiled = []
    for r in rules:
        try:
            compiled.append((re.compile(r.pattern), r))
        except re.error:
            continue   # 壊れたパターンを1つ入れても全体は止めない
    target = led['label_kind'].eq('item') if 'label_kind' in led.columns else led['label'].notna()
    for idx in led.index[target]:
        name = str(led.at[idx, 'label'] or '')
        if not name:
            continue
        for pat, r in compiled:
            if pat.search(name):
                led.at[idx, 'category'] = r.category
                led.at[idx, 'subcategory'] = r.subcategory or led.at[idx, 'subcategory']
                led.at[idx, 'category_source'] = 'fix'
                break
    return _default_food_for_formulaic_categories(led)


# 「本物の行は定型句」なカテゴリ。ここに入った商品行のうち定型句に当たらないものは、
# ユーザーの実データではほぼ食料品のレシートだった（交通に10行のスーパーのレシート等）。
# ⚠️ 医療・保険／健康／日用雑貨は入れないこと。ドラッグストアのレシートは薬と食品が
# 本当に混ざるので、品目名が読めない行を食費に倒すと薬を食費にしてしまう
DEFAULT_FOOD_CATEGORIES = ('交通', '通信', '教育・教養', '遊び')


def _default_food_for_formulaic_categories(led):
    """辞書（nonfood_phrases.csv）に無い商品行を食費に倒す。品目ルールの後に効く。

    ユーザーの発案: 通信・交通・学習は本物の行がほぼ同じ文言で出るのに対し、
    そこに紛れる不明な行はほぼ食料品。なら「本物の文言」を辞書にして、
    それ以外を食費にする方が、食べ物の語を全部列挙するより漏れが少ない。
    対象は Zaim 由来の商品行だけ（ルールや手動で決まった行は触らない）。
    """
    import re
    import pandas as pd
    csv = Path(settings.BASE_DIR) / 'card_insight' / 'nonfood_phrases.csv'
    if not csv.exists() or 'label_kind' not in led.columns:
        return led
    try:
        df = pd.read_csv(csv, encoding='utf-8-sig').fillna('')
    except Exception:  # noqa: BLE001  辞書が壊れていても取り込みは止めない
        return led
    keep = {}
    for _, r in df.iterrows():
        cat, pat = str(r.get('category') or '').strip(), str(r.get('pattern') or '').strip()
        if cat and pat:
            try:
                keep[cat] = re.compile(pat, re.IGNORECASE)
            except re.error:
                continue
    mask = (led['label_kind'].eq('item') & led['category_source'].eq('zaim')
            & led['category'].isin(DEFAULT_FOOD_CATEGORIES) & led['label'].fillna('').ne(''))
    for idx in led.index[mask]:
        cat = led.at[idx, 'category']
        pat = keep.get(cat)
        if pat is not None and pat.search(str(led.at[idx, 'label'])):
            continue                      # 定型句に当たる＝本物
        led.at[idx, 'category'] = '食費'
        led.at[idx, 'subcategory'] = '食料品'
        led.at[idx, 'category_source'] = 'fix'
    return led


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
    'label_kind', 'label_clean',
]


def import_from_files(zaim_path: Path | None = None, enavi_pattern: str | None = None) -> ImportLog:
    """CSV → 台帳を再構築 → Transaction を upsert。ImportLog を返す。

    手動編集（manual_* / exclude_override）は保持する。台帳から消えた行は削除する
    （Zaim 側で記録を消した場合に追随するため）。
    """
    import pandas as pd
    from card_insight.labels import classify_label, clean_label
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
        # レシート付随行（外税・割引・袋代など）に印を付ける。集計からは外さない
        led['label_kind'] = led['label'].map(classify_label)
        led['label_clean'] = led['label'].map(clean_label)
        # 品目ルール（Zaim の誤学習を打ち消す）。⚠️ Zaim の分類より上で効く唯一の経路
        led = apply_label_rules(led)
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
    income = import_income(zaim_path)

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
            + (f' ／ {income["message"]}' if income['message'] else '')
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
