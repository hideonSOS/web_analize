"""merchant_rules.csv の内容を MerchantRule へ反映する。

なぜ要るか: 分類ルールは CSV から**初回シードされるだけ**（seed_rules_if_empty）で、
以後は DB が正になる。つまり CSV を直しても、既に取り込み済みの環境には何も起きない。
ルールの誤りは今後も見つかるので、その修正を本番へ届ける経路として用意する。

⚠️ ルールを直しただけでは既存の台帳は分類し直されない。反映するには取り込みを
やり直すこと（画面の「取り込み」または import_from_files）。
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from spending.models import MerchantRule

FIELDS = ['merchant', 'category', 'subcategory', 'kind', 'necessity', 'note']


class Command(BaseCommand):
    help = 'merchant_rules.csv の内容を MerchantRule へ反映する（pattern をキーに upsert）'

    def add_arguments(self, parser):
        parser.add_argument('--prune', action='store_true',
                            help='CSV に無いルールを DB から削除する')
        parser.add_argument('--dry-run', action='store_true', help='変更内容を表示するだけ')

    def handle(self, *args, **opts):
        import pandas as pd
        csv = Path(settings.BASE_DIR) / 'card_insight' / 'merchant_rules.csv'
        if not csv.exists():
            self.stderr.write(f'{csv} が見つかりません')
            return

        df = pd.read_csv(csv, encoding='utf-8-sig').fillna('')
        existing = {r.pattern: r for r in MerchantRule.objects.all()}
        seen, added, changed = set(), 0, 0

        for i, row in df.iterrows():
            pattern = str(row.get('pattern') or '').strip()
            if not pattern:
                continue
            seen.add(pattern)
            values = {
                'priority': int(row.get('priority') or 100),
                'merchant': str(row.get('merchant') or ''),
                'category': str(row.get('category') or ''),
                'subcategory': str(row.get('subcategory') or ''),
                'kind': str(row.get('kind') or '変動'),
                'necessity': str(row.get('necessity') or '要確認'),
                'note': str(row.get('note') or ''),
            }
            obj = existing.get(pattern)
            if obj is None:
                if not opts['dry_run']:
                    MerchantRule.objects.create(pattern=pattern, **values)
                added += 1
                self.stdout.write(f'  追加 {pattern} → {values["category"]}/{values["subcategory"]}')
                continue
            diff = [f for f in FIELDS if getattr(obj, f) != values[f]]
            if not diff:
                continue
            for f in diff:
                self.stdout.write(f'  変更 {pattern} {f}: {getattr(obj, f)!r} → {values[f]!r}')
                setattr(obj, f, values[f])
            if not opts['dry_run']:
                obj.save(update_fields=diff)
            changed += 1

        orphans = sorted(set(existing) - seen)
        if orphans and opts['prune']:
            if not opts['dry_run']:
                MerchantRule.objects.filter(pattern__in=orphans).delete()
            self.stdout.write(f'  削除 {len(orphans)}件: {", ".join(orphans)}')
        elif orphans:
            self.stdout.write(f'  CSV に無いルール {len(orphans)}件（--prune で削除）: {", ".join(orphans)}')

        head = '（dry-run。書き込んでいません）' if opts['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'ルール {MerchantRule.objects.count()}件 / 追加{added} 変更{changed} {head}'))
        if (added or changed) and not opts['dry_run']:
            self.stdout.write('⚠️ 既存の台帳は分類し直されていません。取り込みをやり直してください。')
