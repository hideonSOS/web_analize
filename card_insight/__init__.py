"""card_insight: Zaim × 楽天e-navi のカード支出を突合・分類・分析する前処理モジュール(たたき台)。

パイプライン:
    zaim_loader.load_zaim      -> Zaim CSV を正規化した DataFrame
    zaim_loader.extract_card   -> 楽天カード払いの行だけ抽出
    enavi_loader.load_enavi    -> 楽天e-navi の明細 CSV(複数月可)を正規化
    normalize.apply_rules      -> 加盟店名の正規化と分類ルール適用
    reconcile.reconcile        -> Zaim と e-navi の突合(日付±N日・金額一致)
    analyze.*                  -> 月次推移 / サブスク検出 / 歪み検出 / 節約候補 / 入金力試算
    report.write_report        -> Excel レポート出力

既存の資産管理アプリへは、`run.py` の `build_dataset()` が返す dict(各 DataFrame)を
そのままモデル層に渡す想定。詳細は 申し送り.md を参照。
"""

__version__ = "0.1.0"
