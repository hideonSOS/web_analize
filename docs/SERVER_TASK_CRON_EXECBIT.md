# サーバー側AIへの作業指示: 実行ビット修正の取り込みと検証

作成: 2026-08-30 開発機側AI（Claude Code）
宛先: サーバー側AI
背景: `docs/HANDOVER_CRON_EXECBIT.md`（あなたからの引き継ぎ書）への返信。
指摘のとおり開発機側で `git update-index --chmod=+x` を実施し、
コミット `eb16e30` として origin/main に push 済み。scripts/*.sh は
リポジトリ上で 100755 になっている。以下を上から順に実行すること。

## 1. 現状確認

```bash
cd /srv/web_analize
git status
git log --oneline origin/main..HEAD
```

- push できなかったローカルコミット（あなたが作った chmod コミット）が
  残っているはず。その前提で次へ。ローカルコミットが無ければ単に
  `git pull` して手順3へ。

## 2. 取り込み

```bash
git pull --rebase
```

- 開発機側のコミットと内容が同じなら、rebase が重複を自動で落とす。
- コンフリクトした場合: 対象は scripts/*.sh のモードか docs/ のはず。
  **origin 側を正として解決**してよい（開発機側コミットに全内容が含まれている）。
  解決が面倒なら、`git stash list` と `git status` で**サーバー固有の
  未コミット変更が無いことを確認したうえで**:

```bash
git rebase --abort
git reset --hard origin/main
```

⚠️ config.json / media/ / logs/ は git 管理外なので reset では消えない。
ただし追跡ファイルへのサーバー独自の変更があれば消えるため、
reset 前に `git diff HEAD` が空であることを必ず確認すること。

## 3. 検証（3段階）

```bash
# (1) 実行ビットが付いたか
ls -l scripts/*.sh
# 期待: -rwxr-xr-x が2本（daily_update.sh / us_ranking_update.sh）

# (2) 直接パス実行が通るか（cron と同じ起動方法）
./scripts/daily_update.sh
echo "exit=$?"
# 期待: exit 126 にならない。0 または（データ都合の失敗時）1。
# 実行後 logs/update_$(date +%Y%m%d).log に今日のログが増えていること

# (3) cron の次回実行を待たずに設定を目視確認
crontab -l
```

## 4. あわせて確認: ポートフォリオ機能の反映漏れ

`docs/DEPLOY_PORTFOLIO.md` の手順（migrate → collectstatic →
seed_fund_products → update_product_prices → restart）が未実施なら実施する。
実施済みかは次で判定できる:

```bash
./venv/bin/python manage.py shell -c "from portfolio.models import Product; print(Product.objects.count())"
# 5 以上なら実施済み。0 なら未実施（DEPLOY_PORTFOLIO.md を実行）
```

## 5. 完了報告（ユーザーに伝えること）

以下を報告すること:
- `git log --oneline -1`（eb16e30 以降になっているか）
- `ls -l scripts/*.sh` の結果
- `./scripts/daily_update.sh` の終了コードとログ末尾数行
- ポートフォリオ手順の実施有無と、投信プルダウンに5商品が出るか

## 禁止事項

- このリポジトリへの push は行わない（認証が無い環境である前提。
  サーバー側で恒久修正が必要になった場合は、今回同様
  docs/ に引き継ぎ書を書いてユーザー経由で開発機側AIに依頼すること）
- crontab の時刻・行の変更は今回のスコープ外（既存設定のまま）
