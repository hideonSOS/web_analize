"""SEC EDGAR（公式・無料・キー不要）から米国株の財務データを取る（2026-09-16）

ユーザー決定: 米国株の決算は yfinance ではなく **SEC EDGAR の companyfacts API** を一次情報にする
（TradingView は公式 API 無し・株探は規約でスクレイピング禁止・FMP 等は無料枠が細い）。
価格だけは引き続き yfinance（EDGAR に株価は無い）。

エンドポイント
  - ティッカー→CIK: https://www.sec.gov/files/company_tickers.json（1本・約1MB・プロセス内キャッシュ）
  - 財務: https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json（1社1本・数MB）
  - ⚠️ SEC は User-Agent に連絡先（メール）を要求する（無いと 403 になることがある）。
    `config.json` の "edgar_contact" を使い、無ければ汎用の文字列で試みる。10 req/s 以下を守る

データの読み方（companyfacts の JSON）
  facts[taxonomy][tag]['units'][unit] = [{start?, end, val, fy, fp, form, filed, frame?}, ...]
  - 損益項目（売上・純利益…）は期間（start〜end）。**10-Q は四半期単独と期初来累計の両方**、
    10-K は通期が入る。期間の長さで Q（80〜100日）／FY（350〜380日）に分ける
  - 貸借項目（総資産・自己資本・株式数）は時点（end のみ）
  - 同じ期が複数の提出（訂正・翌年の比較表示）に出るので **filed が最新のもの** を採る
  - **Q4 単独は開示されない**（10-K は通期のみ）。同じ会計年度の Q1〜Q3 が揃っていれば
    Q4 = FY − (Q1+Q2+Q3) で補う（TTM = 直近4四半期の合計、に必要）
  - 値は **提出時点のまま（分割調整なし）**。そのため期末株価も **調整前（auto_adjust=False）** を
    合わせる（分割前の株数×分割前の株価で PER/PBR が整合する）。現在値の指標は最新四半期の
    株数（分割後）× 現在株価なので、こちらも整合する

タグは企業ごとに揺れるので候補を順に見る（TAGS）。取れない項目は None（指標側で「算出不可」）。
"""
import gzip
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime

from django.conf import settings

TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
_MIN_INTERVAL = 0.15   # 10 req/s の上限に余裕を持つ

# 項目 → (taxonomy, tag) の候補（先勝ち）。単位は USD（株式数は shares、EPS は USD/shares）
TAGS = {
    'sales': [('us-gaap', 'Revenues'),
              ('us-gaap', 'RevenueFromContractWithCustomerExcludingAssessedTax'),
              ('us-gaap', 'RevenueFromContractWithCustomerIncludingAssessedTax'),
              ('us-gaap', 'SalesRevenueNet'),
              ('us-gaap', 'RevenuesNetOfInterestExpense')],
    'op': [('us-gaap', 'OperatingIncomeLoss')],
    'np': [('us-gaap', 'NetIncomeLoss'),
           ('us-gaap', 'ProfitLoss'),
           ('us-gaap', 'NetIncomeLossAvailableToCommonStockholdersBasic')],
    'eps': [('us-gaap', 'EarningsPerShareDiluted'), ('us-gaap', 'EarningsPerShareBasic')],
    'total_assets': [('us-gaap', 'Assets')],
    'equity': [('us-gaap', 'StockholdersEquity'),
               ('us-gaap', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest')],
    'shares': [('dei', 'EntityCommonStockSharesOutstanding'),
               ('us-gaap', 'CommonStockSharesOutstanding'),
               ('us-gaap', 'WeightedAverageNumberOfDilutedSharesOutstanding'),
               ('us-gaap', 'WeightedAverageNumberOfSharesOutstandingBasic')],
    'div': [('us-gaap', 'CommonStockDividendsPerShareDeclared'),
            ('us-gaap', 'CommonStockDividendsPerShareCashPaid')],
}
DURATION_ITEMS = ('sales', 'op', 'np', 'eps', 'div')   # 期間項目（それ以外は時点項目）

_ticker_map = None
_last_call = 0.0


class EdgarError(Exception):
    pass


def _user_agent():
    contact = getattr(settings, 'EDGAR_CONTACT', '') or ''
    return f'web_kabuanalize/1.0 ({contact})' if contact else 'web_kabuanalize/1.0 (personal research tool)'


def _get_json(url):
    """SEC へ GET（gzip・User-Agent 必須・10 req/s 以下）"""
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={
        'User-Agent': _user_agent(), 'Accept-Encoding': 'gzip', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
    _last_call = time.time()
    return json.loads(raw.decode('utf-8'))


def ticker_to_cik(ticker):
    """ティッカー → CIK（int）。無ければ None。ドット付き（BRK.B）は SEC 表記 BRK-B も試す"""
    global _ticker_map
    if _ticker_map is None:
        data = _get_json(TICKERS_URL)
        _ticker_map = {v['ticker'].upper(): int(v['cik_str']) for v in data.values()}
    t = ticker.upper()
    return _ticker_map.get(t) or _ticker_map.get(t.replace('.', '-'))


def fetch_companyfacts(cik):
    return _get_json(FACTS_URL.format(cik=cik))


def _d(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def _entries(facts, item):
    """項目の候補タグ全部のエントリを (優先度, entry) で返す（優先度は TAGS の順。0 が最優先）

    企業によってタグが途中で切り替わる（Broadcom: NetIncomeLoss は 2020〜21 が欠け ProfitLoss は全期）
    ので、先に見つかったタグだけを採らず **期ごとに優先度の高いタグから埋める**。
    """
    out = []
    for prio, (taxo, tag) in enumerate(TAGS[item]):
        node = facts.get('facts', {}).get(taxo, {}).get(tag)
        if not node:
            continue
        units = node.get('units', {})
        for unit in ('USD', 'shares', 'USD/shares'):
            if unit in units and units[unit]:
                out.extend((prio, e) for e in units[unit])
                break
    return out


def _is_annual_filing(e):
    """通期の値かどうか。10-Q に載る「直近12か月（TTM）」の値を通期と取り違えないため
    （Amazon は 10-Q に twelve months ended を出す）、fp=FY か 10-K/20-F/40-F 提出に限る"""
    form = (e.get('form') or '').upper()
    return e.get('fp') == 'FY' or form.startswith(('10-K', '20-F', '40-F'))


def _latest_by_period(entries, kind, allow_duration=False):
    """kind='Q'|'FY'|'I'（時点）ごとに {end: val}

    同じ期が複数あるときは (タグ優先度, filed が新しい) の順で採る。
    allow_duration=True（株式数用）: 時点値が無い企業（複数クラス株の META/MSTR は dei の
    時点値が無く加重平均株式数しか無い）のため、期間値も end をキーに採る（時点値より低優先）。
    """
    best = {}
    for prio, e in entries:
        try:
            end = _d(e['end'])
            has_start = 'start' in e
            if kind == 'I':
                if has_start:
                    if not allow_duration:
                        continue
                    days = (end - _d(e['start'])).days
                    if not (80 <= days <= 100 or 350 <= days <= 380):
                        continue
                    prio = prio + 100 + (0 if days <= 100 else 1)   # 四半期の加重平均を優先
            else:
                if not has_start:
                    continue
                days = (end - _d(e['start'])).days
                if kind == 'Q' and not (80 <= days <= 100):
                    continue
                if kind == 'FY' and not (350 <= days <= 380 and _is_annual_filing(e)):
                    continue
            key = (prio, -int((e.get('filed') or '0000-00-00').replace('-', '') or 0))
            cur = best.get(end)
            if cur is None or key < cur[0]:
                best[end] = (key, e.get('val'), e.get('filed'))
        except (KeyError, ValueError, TypeError):
            continue
    return {k: (v[1], v[2]) for k, v in best.items()}


def split_factor(splits, end, filed):
    """提出時点の1株あたり数値を、今の株数基準に直す倍率（Π 分割比率）。

    ⚠️ 株価（yfinance）は常に分割調整済みだが、EDGAR の株式数・EPS・1株配当は **提出時点のまま**。
    そのまま割ると分割前の期の PER/PBR が 10〜20 倍ズレる（NVDA 2021/10 の PER が 7.9 倍と出た。
    実際は約 79 倍。2026-09-16 に発見）。ただし後年の提出（比較表示）で **再表示された値は分割後**
    なので、「期末が分割前」かつ「提出日も分割前」のものだけ調整する。
    splits=[(date, ratio)]（yfinance の Ticker.splits）"""
    f = 1.0
    filed_d = _d(filed) if isinstance(filed, str) and filed else None
    for sd, ratio in splits or []:
        if end < sd and (filed_d is None or filed_d < sd):
            f *= ratio
    return f


def _nearest_instant(instants, end, tolerance_days=10):
    """時点項目: 期末日と同じ（±数日）の値"""
    if end in instants:
        return instants[end]
    for k, v in instants.items():
        if abs((k - end).days) <= tolerance_days:
            return v
    return None


def build_reports(facts, splits=None):
    """companyfacts → 期ごとの dict のリスト（per_type FY/Q・per_end 昇順）

    返す各 dict: per_type, per_end, sales, op, np, eps, total_assets, equity, shares, div_ann
    （div_ann は期末までの直近12か月の1株配当の合計）。
    splits=[(date, ratio)] を渡すと株式数・EPS・1株配当を今の株数基準に調整する（split_factor）
    """
    dur = {}   # item -> {'Q': {end: val}, 'FY': {end: val}}
    for item in DURATION_ITEMS:
        ents = _entries(facts, item)
        dur[item] = {'Q': _latest_by_period(ents, 'Q'), 'FY': _latest_by_period(ents, 'FY')}
    inst = {}
    for item in ('total_assets', 'equity', 'shares'):
        inst[item] = _latest_by_period(_entries(facts, item), 'I', allow_duration=(item == 'shares'))

    # 純利益がある期だけを対象にする（指標の主役。無い期は保存しない）
    fy_ends = sorted(dur['np']['FY'])
    q_ends = set(dur['np']['Q'])

    # Q4 の補完: FY − 同じ会計年度の Q1〜Q3（3つ揃っているときだけ）
    derived_q4 = {}
    for fe in fy_ends:
        if fe in q_ends:
            continue
        prev = [q for q in q_ends if 0 < (fe - q).days <= 300]
        if len(prev) != 3:
            continue
        derived_q4[fe] = prev
    q_ends |= set(derived_q4)

    # 配当: 四半期の宣言額を期末までの12か月で合計（FY の値があればそれを優先）。1株あたりなので分割調整
    div_q, div_fy = dur['div']['Q'], dur['div']['FY']

    def per_share(item_map, end):
        """1株あたり項目を今の株数基準に（値 ÷ 分割倍率）"""
        v = item_map.get(end)
        if v is None or v[0] is None:
            return None
        return float(v[0]) / split_factor(splits, end, v[1])

    def div_ttm(end):
        fy = per_share(div_fy, end)
        if fy:
            return fy
        total = sum(per_share(div_q, k) or 0 for k in div_q if 0 <= (end - k).days < 365)
        return total if total > 0 else None

    def raw(item, kind, end):
        v = dur[item][kind].get(end)
        return v[0] if v else None

    def dur_val(item, kind, end):
        v = raw(item, kind, end)
        if v is None and kind == 'Q' and end in derived_q4:
            fy = raw(item, 'FY', end)
            parts = [raw(item, 'Q', q) for q in derived_q4[end]]
            if fy is not None and all(p is not None for p in parts):
                v = fy - sum(parts)
        return v

    def inst_val(item, end, tol=10):
        v = _nearest_instant(inst[item], end, tol)
        return v[0] if v else None

    def shares_val(end):
        v = _nearest_instant(inst['shares'], end, 45)
        if not v or v[0] is None:
            return None
        return v[0] * split_factor(splits, end, v[1])   # 株式数は × 倍率

    rows = []
    for kind, ends in (('FY', fy_ends), ('Q', sorted(q_ends))):
        for end in ends:
            np_ = dur_val('np', kind, end)
            if np_ is None:
                continue
            rows.append({
                'per_type': kind, 'per_end': end,
                'sales': dur_val('sales', kind, end),
                'op': dur_val('op', kind, end),
                'np': np_,
                # EPS: Q4 補完は近似になるので入れない（指標は np/株数で計算するので未使用）。1株あたりなので分割調整
                'eps': per_share(dur['eps'][kind], end),
                'total_assets': inst_val('total_assets', end), 'equity': inst_val('equity', end),
                'shares': shares_val(end),
                'div_ann': div_ttm(end),
            })
    rows.sort(key=lambda r: (r['per_end'], r['per_type']))
    return rows


def coverage_note(facts):
    """項目ごとに見つかったタグ（デバッグ・ログ用）"""
    out = {}
    for item, cands in TAGS.items():
        found = sorted({p for p, _ in _entries(facts, item)})
        out[item] = [cands[p][1] for p in found]
    return out


# ====================================================================================
# 20-F（外国企業の年次報告書）の XBRL から通期の公表値を取る（2026-09-16・PayPay で導入）
# ====================================================================================
# companyfacts は IFRS/外貨建ての外国企業（日本企業の ADR 等）で財務タグを返さないことがある
# （PayPay は dei しか無い）。その場合 yfinance に落ちると、Yahoo が組み替えた「Operating Income」が
# 公表の営業利益と大きくズレる（PayPay 2026/3 期: 公表 800.8 億円 vs yfinance 1,053 億円）。
# 20-F には XBRL インスタンス（<primaryDocument>_htm.xml）が付いており、ifrs-full タグで
# 通期3期分（損益）＋2期分（貸借）が正確に取れる。四半期（6-K）には XBRL が無いので取れない。
SUBMISSIONS_URL = 'https://data.sec.gov/submissions/CIK{cik:010d}.json'
ARCHIVE_URL = 'https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}'
MAX_20F_FILINGS = 4    # 1提出=3期分なので4本で約10年。1本 10MB 超なので増やし過ぎない
_XBRLI = '{http://www.xbrl.org/2003/instance}'

# 項目 → ローカル名の候補（名前空間は ifrs-full / dei / us-gaap を問わない。先勝ち）
TAGS_20F = {
    'sales': ['Revenue', 'Revenues', 'RevenueFromContractsWithCustomers'],
    'op': ['ProfitLossFromOperatingActivities', 'OperatingIncomeLoss'],
    'np': ['ProfitLossAttributableToOwnersOfParent', 'ProfitLoss', 'NetIncomeLoss'],
    'eps': ['DilutedEarningsLossPerShare', 'BasicEarningsLossPerShare', 'EarningsPerShareDiluted'],
    'total_assets': ['Assets'],
    'equity': ['EquityAttributableToOwnersOfParent', 'Equity', 'StockholdersEquity'],
    'shares': ['EntityCommonStockSharesOutstanding', 'NumberOfSharesOutstanding', 'CommonStockSharesOutstanding'],
}


def _get_bytes(url):
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={'User-Agent': _user_agent(), 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
    _last_call = time.time()
    return raw


def list_20f_instances(cik):
    """その CIK の 20-F（訂正 20-F/A 含む）の XBRL インスタンス URL を新しい順に返す"""
    sub = _get_json(SUBMISSIONS_URL.format(cik=cik))
    rec = sub.get('filings', {}).get('recent', {})
    out = []
    for form, acc, doc in zip(rec.get('form', []), rec.get('accessionNumber', []), rec.get('primaryDocument', [])):
        if form in ('20-F', '20-F/A') and doc.endswith('.htm'):
            out.append(ARCHIVE_URL.format(cik=cik, acc=acc.replace('-', ''), doc=doc[:-4] + '_htm.xml'))
    return out[:MAX_20F_FILINGS]


def parse_xbrl_instance(xml_bytes):
    """XBRL インスタンス → (contexts{id: (start|None, end)}, units{id: 'JPY'}, facts[(local, ctx, unit, text)])
    次元付き（segment あり）のコンテキストは捨てる（セグメント別・クラス別の値を混ぜないため）"""
    root = ET.fromstring(xml_bytes)
    ctx, units, facts = {}, {}, []
    for c in root.iter(_XBRLI + 'context'):
        if c.find('.//' + _XBRLI + 'segment') is not None:
            continue
        p = c.find(_XBRLI + 'period')
        if p is None:
            continue
        s_, e_, i_ = p.find(_XBRLI + 'startDate'), p.find(_XBRLI + 'endDate'), p.find(_XBRLI + 'instant')
        ctx[c.get('id')] = (s_.text if s_ is not None else None, e_.text if e_ is not None else (i_.text if i_ is not None else None))
    for u in root.iter(_XBRLI + 'unit'):
        m = u.find('.//' + _XBRLI + 'measure')
        if m is not None and m.text:
            units[u.get('id')] = m.text.split(':')[-1].upper()   # iso4217:JPY → JPY / shares → SHARES
    for el in root:
        cref = el.get('contextRef')
        if cref and cref in ctx and el.text:
            facts.append((el.tag.split('}')[-1], cref, el.get('unitRef'), el.text.strip()))
    return ctx, units, facts


def reports_from_20f(cik):
    """20-F の XBRL から通期（FY）の行を返す。[{per_type:'FY', per_end, sales, op, np, eps, total_assets,
    equity, shares, div_ann: None, currency}] per_end 昇順。同じ期は新しい提出を優先。取れなければ []"""
    rows = {}   # per_end -> dict
    for url in list_20f_instances(cik):
        try:
            ctx, units, facts = parse_xbrl_instance(_get_bytes(url))
        except Exception:   # noqa: BLE001  1本壊れていても他の提出は使う
            continue
        # 通期の期間（約1年）と、その期末の時点
        fy_ctx = {cid: _d(e) for cid, (s_, e) in ctx.items() if s_ and e and 350 <= (_d(e) - _d(s_)).days <= 380}
        inst_ctx = {cid: _d(e) for cid, (s_, e) in ctx.items() if not s_ and e}
        by = defaultdict(dict)   # (item, per_end) -> value（先勝ち=候補順）
        ccy = None
        for item, cands in TAGS_20F.items():
            for prio, local in enumerate(cands):
                for tag, cref, uref, text in facts:
                    if tag != local:
                        continue
                    end = fy_ctx.get(cref) if item in ('sales', 'op', 'np', 'eps') else inst_ctx.get(cref)
                    if end is None:
                        continue
                    try:
                        val = float(text)
                    except ValueError:
                        continue
                    cur = by[item].get(end)
                    if cur is None or prio < cur[0]:
                        by[item][end] = (prio, val)
                    u = units.get(uref, '')
                    if item in ('sales', 'op', 'np') and len(u) == 3 and u != 'SHA':
                        ccy = ccy or u
        for end in sorted(by['np']):
            if end in rows:          # 新しい提出（先に処理）を優先
                continue
            v = lambda item: (by[item].get(end) or (None, None))[1]   # noqa: E731
            rows[end] = {
                'per_type': 'FY', 'per_end': end,
                'sales': v('sales'), 'op': v('op'), 'np': v('np'), 'eps': v('eps'),
                'total_assets': v('total_assets'), 'equity': v('equity'), 'shares': v('shares'),
                'div_ann': None,     # 20-F の1株配当タグは企業ごとに定義が揺れる（PayPay は子会社分が混ざる）ので使わない
                'currency': ccy or 'USD',
            }
    return [rows[k] for k in sorted(rows)]
