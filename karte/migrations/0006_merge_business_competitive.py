# 事業理解(2項目)と競争環境(3項目)を、それぞれ自由記述1項目に集約する。
# 既存の記入内容を失わないよう、削除の前に結合する。

from django.db import migrations, models

# (集約先, [(元フィールド, 見出し), ...])
MERGES = [
    ('business_note', [
        ('business_model', '【事業内容・稼ぎ方】'),
        ('revenue_structure', '【収益構造】'),
    ]),
    ('competitive_note', [
        ('strengths', '【強み・参入障壁】'),
        ('competition', '【競合・市場シェア】'),
        ('risks', '【リスク・弱み】'),
    ]),
]


def merge_fields(apps, schema_editor):
    StockKarte = apps.get_model('karte', 'StockKarte')
    for k in StockKarte.objects.all():
        changed = []
        for target, sources in MERGES:
            parts = []
            for field, label in sources:
                value = (getattr(k, field, '') or '').strip()
                if value:
                    # どの項目に書いた内容か分かるよう見出しを残す
                    parts.append(f'{label}\n{value}')
            if parts:
                setattr(k, target, '\n\n'.join(parts))
                changed.append(target)
        if changed:
            k.save(update_fields=changed)


def split_back(apps, schema_editor):
    """逆方向。結合済みの本文は分解できないため各グループの先頭項目へ戻す"""
    StockKarte = apps.get_model('karte', 'StockKarte')
    for k in StockKarte.objects.all():
        changed = []
        for target, sources in MERGES:
            value = getattr(k, target, '')
            if value:
                setattr(k, sources[0][0], value)
                changed.append(sources[0][0])
        if changed:
            k.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ('karte', '0005_merge_mgmt_fields'),
    ]

    operations = [
        # 1) 先に新項目を追加
        migrations.AddField(
            model_name='stockkarte',
            name='business_note',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='stockkarte',
            name='competitive_note',
            field=models.TextField(blank=True),
        ),
        # 2) 既存データを結合してから
        migrations.RunPython(merge_fields, split_back),
        # 3) 旧項目を削除する
        migrations.RemoveField(model_name='stockkarte', name='business_model'),
        migrations.RemoveField(model_name='stockkarte', name='revenue_structure'),
        migrations.RemoveField(model_name='stockkarte', name='strengths'),
        migrations.RemoveField(model_name='stockkarte', name='competition'),
        migrations.RemoveField(model_name='stockkarte', name='risks'),
    ]
