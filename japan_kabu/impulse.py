"""独自セクター別インパルス（時系列ヒートマップ）の定義

「市場で今どのテーマに資金が入っているか」を数日間のモメンタムで見るための
独自セクター定義。J-Quantsの業種区分ではなく、自分の関心テーマで銘柄を束ねる。

プロトタイプ段階のため銘柄は各セクター数銘柄に限定している。
セクターや銘柄を増やすときはこのファイルを編集し、
`python manage.py update_impulse_prices` で日次終値を取り直すだけでよい
（ビューは定義を動的に読むのでコード変更は不要）。

codes は JP=表示コード4桁 / US=ティッカー。

**codes が空のセクターは「ダミー行」**として、実データの代わりに日付とセクター名から
決まる擬似乱数で色を出す（レイアウト確認用のプレースホルダ）。画面には「ダミー」
バッジが出る。codes を入れた時点で自動的に実データ計算へ切り替わる。
"""

IMPULSE_SECTORS = {
    'JP': [
        {'name': 'AI・半導体', 'codes': ['8035', '6857', '6146']},   # 東エレク/アドバンテスト/ディスコ
        {'name': '情報通信', 'codes': ['9432', '9433', '9434']},     # NTT/KDDI/ソフトバンク
        {'name': 'メガバンク', 'codes': ['8306', '8316', '8411']},   # 三菱UFJ/三井住友/みずほ
    ],
    'US': [
        # ↓ 構成銘柄はユーザーが後日定義する（現状の3行は暫定のまま。差し替え待ち）
        {'name': 'AI・半導体', 'codes': ['NVDA', 'AVGO', 'AMD']},
        {'name': 'ビッグテック', 'codes': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']},
        {'name': '金融', 'codes': ['JPM', 'BAC', 'GS']},
        # IonQ / Rigetti / IBM / Quantum Computing Inc.
        {'name': '量子系', 'codes': ['IONQ', 'RGTI', 'IBM', 'QUBT']},
        # ↓ 銘柄未定のダミー行。codes に入れた時点で実データ計算に切り替わる
        # SpaceX(SPCX・2026-06-12上場) / Sidus Space / Momentus / Satellogic
        # ※SPCXは上場が新しく履歴が短い（σ推定は MIN_HISTORY 以上あれば可）
        {'name': '宇宙系', 'codes': ['SPCX', 'SIDU', 'MNTS', 'SATL']},
        # Pfizer / Eli Lilly
        {'name': '医療系', 'codes': ['PFE', 'LLY']},
        # 暗号資産は現物(BTC-USD等)ではなく**関連株**で代替している
        # Strategy(旧MicroStrategy・BTC保有) / Coinbase(取引所)
        {'name': '暗号資産', 'codes': ['MSTR', 'COIN']},
        # 金銀は現物・先物ではなく**ETF**で代替（GC=F 等の非株式ティッカーは
        # Stockマスタに無く、_targets() がマスタ経由で解決しているため使えない）
        # SPDR Gold Shares / iShares Silver Trust
        {'name': '金銀', 'codes': ['GLD', 'SLV']},
    ],
}

DAYS_SHOWN = 20        # 表示する営業日数（参考画像と同じ約1か月）

# ---- 色の判定しきい値 ------------------------------------------------------
# 判定は「セクター自身のボラティリティの何倍動いたか」で行う（±SIGMA_BAND σ）。
# 全セクター共通の固定%にすると、σが3倍違う行（量子系3.94 vs 金融1.27）で
# 色の意味が変わってしまい、ヒートマップの主目的である**行間の比較**が壊れる
# （実測: 固定±0.3%だと量子系は20日中19日が緑か赤、金融は7日が青）。
SIGMA_BAND = 0.5       # ±この倍のσ以内は「中立（青）」。超えたら上昇（緑）/下落（赤）
MIN_BAND = 0.1         # 判定幅の下限（%）。無風のセクターでノイズを拾わないため
MIN_HISTORY = 10       # σ推定に必要な最低日数。これ未満は NEUTRAL_BAND にフォールバック
NEUTRAL_BAND = 0.3     # 履歴不足時のフォールバック判定幅（%）


def impulse_universe(country):
    """その国の全対象コードのリスト（重複除去・定義順維持）。ダミー行は空なので寄与しない"""
    seen = []
    for sec in IMPULSE_SECTORS.get(country, []):
        for c in sec.get('codes') or []:
            if c not in seen:
                seen.append(c)
    return seen


def dummy_change(sector_name, day):
    """ダミー行のセル値（%）。セクター名と日付から決まる決定的な擬似乱数

    ランダムだと再読み込みのたびに色が変わって「実データではない」ことが
    伝わりにくいので、同じ日・同じセクターなら常に同じ値になるようにする。
    分布は概ね -2.5%〜+2.5%。
    """
    import hashlib

    h = hashlib.md5(f'{sector_name}:{day.isoformat()}'.encode()).digest()
    raw = int.from_bytes(h[:4], 'big') / 0xFFFFFFFF   # 0.0〜1.0
    return round((raw - 0.5) * 5.0, 2)
