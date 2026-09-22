# -*- coding: utf-8 -*-
"""株タン手法 ルールv1.1 の判定ロジック（バックテストと同一定義）

出典: 「株タン公開記事に基づく買い手法：AI参照・再現用仕様 v1.0（2026-09-22）」
検証: Desktop\\study\\files\\kabutan_backtest.py / kabutan_v11_test.py
      JPX400全400銘柄・約2年（2024-09〜2026-06）で
      v1.1 = 489トレード・勝率56.0%・コスト後+2.13%/回・PF2.42

═══ PROPOSED v1.1 確定値（本人のルールではなく検証用にAIが固定した仮定） ═══
  MA: SMA・終値。週足環境確認は日足で代替（終値>100日MA かつ 60日MA上向き）
  上昇開始: 20日MA > 60日MA へのクロス。押し目 = 終値が20日MA±2%以内に接近
  押し目の区切り: 接近が6日以上途切れたら次の押し目。1回目の押し目のみ対象
  5日MA転換: 当日傾き≧0 かつ 直近3日内に負の傾き
  エントリー: 全条件AND ＋ 当日陽線。翌日寄付の成行を想定
  地合いフィルター: JPX400等ウェイト合成指数 > その20日MA（TOPIX APIは現プラン403）
  低ボラ除外: 60日日次リターン標準偏差 ≥ 1.2%
  損切り: シグナル日の直近10日安値（終値確認）／利確目安: 20日MA乖離+10%
  出来高条件は不採用（バックテストで逆効果と判明）
═══════════════════════════════════════════════════════════════

⚠️ この判定を変更するときは必ずバックテスト側で再検証してから、
   RULE_VERSION を上げること（旧版の ScreenResult と混ざらないように）。
"""
import pandas as pd

RULE_VERSION = 'v1.1'

# ── PROPOSED パラメータ（バックテストと同値。片方だけ変えないこと） ──
TOUCH_PCT     = 0.02    # 20日MA付近の許容距離 ±2%
PULLBACK_GAP  = 6       # 接近が6日以上途切れたら別の押し目
MA60_SLOPE_D  = 5       # 60日MA傾きの判定日数
VOL_MIN       = 0.012   # 60日 日次リターン標準偏差の下限
TP_DEVIATION  = 0.10    # 利確目安: 20日MA乖離+10%
SL_LOW_WINDOW = 10      # 損切り: 直近10日安値
MIN_ROWS      = 130     # 判定に必要な最小日数（MA100+余裕）

# 条件コード → 画面表示用ラベル
CONDITION_LABELS = {
    'env_up':   '環境: 終値>100日MA・60日MA上向き',
    'regime':   '上昇局面: 20日MA>60日MA',
    'pullback1': '1回目の押し目（20日MA±2%接近）',
    'ma5_turn': '5日MAが下向き→横ばい/上向きに転換',
    'bull':     '当日陽線',
    'mkt':      '地合い: JPX400合成指数>20日MA',
    'vol':      'ボラ十分（60日σ≥1.2%）',
}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """OHLC DataFrame（Date昇順・列 Date/Open/High/Low/Close）に指標列を付ける"""
    df = df.copy()
    for w in (5, 20, 60, 100):
        df[f'MA{w}'] = df['Close'].rolling(w).mean()
    df['ma5_slope'] = df['MA5'].diff()
    df['ma60_up']   = df['MA60'] > df['MA60'].shift(MA60_SLOPE_D)
    df['env_up']    = (df['Close'] > df['MA100']) & df['ma60_up']
    df['regime']    = df['MA20'] > df['MA60']
    df['touch']     = (df['Close'] / df['MA20'] - 1).abs() <= TOUCH_PCT
    df['bull']      = df['Close'] > df['Open']
    df['ma5_turn']  = (df['ma5_slope'] >= 0) & (
        df['ma5_slope'].shift(1).rolling(3).min() < 0)
    df['vol60']     = df['Close'].pct_change().rolling(60).std()

    # 押し目回数（20日MA>60日MA の上昇局面開始でリセット、接近の塊を1回と数える）
    pullback_no, count, last_touch_i, prev = [], 0, None, False
    for i in range(len(df)):
        regime = bool(df['regime'].iloc[i]) if pd.notna(df['regime'].iloc[i]) else False
        if regime and not prev:
            count, last_touch_i = 0, None
        if regime and df['touch'].iloc[i]:
            if last_touch_i is None or i - last_touch_i >= PULLBACK_GAP:
                count += 1
            last_touch_i = i
        pullback_no.append(count if regime else 0)
        prev = regime
    df['pullback_no'] = pullback_no
    return df


def build_market_ok(close_wide: pd.DataFrame) -> dict:
    """JPX400等ウェイト合成指数 > 20日MA の日別辞書を作る。

    close_wide: index=Date, columns=銘柄, 値=調整後終値（JPX400構成銘柄）
    """
    norm = close_wide / close_wide.apply(
        lambda s: s.dropna().iloc[0] if s.dropna().size else float('nan'))
    px = norm.mean(axis=1).sort_index()
    ok = px > px.rolling(20).mean()
    return {d: bool(v) for d, v in ok.items()}


def judge_latest(df: pd.DataFrame, market_ok: dict) -> dict:
    """最新バー（終値確定後）を判定する。翌営業日の寄付成行での買いを想定。

    戻り値: judgment(BUY/WAIT/NODATA), met, unmet, close, stop_price, tp_price, date
    """
    if df is None or len(df) < MIN_ROWS:
        return dict(judgment='NODATA', met=[], unmet=[],
                    close=None, stop_price=None, tp_price=None, date=None)

    df = prepare(df)
    row = df.iloc[-1]
    checks = {
        'env_up':    bool(row['env_up']),
        'regime':    bool(row['regime']),
        'pullback1': bool(row['regime'] and row['pullback_no'] == 1 and row['touch']),
        'ma5_turn':  bool(row['ma5_turn']),
        'bull':      bool(row['bull']),
        'mkt':       bool(market_ok.get(row['Date'], False)),
        'vol':       bool(pd.notna(row['vol60']) and row['vol60'] >= VOL_MIN),
    }
    met   = [k for k, v in checks.items() if v]
    unmet = [k for k, v in checks.items() if not v]
    stop  = float(df['Low'].iloc[-SL_LOW_WINDOW:].min())
    tp    = float(row['MA20'] * (1 + TP_DEVIATION)) if pd.notna(row['MA20']) else None
    return dict(
        judgment='BUY' if not unmet else 'WAIT',
        met=met, unmet=unmet,
        close=float(row['Close']), stop_price=stop, tp_price=tp,
        date=row['Date'],
    )
