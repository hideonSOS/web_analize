# 投資判断(3項目)を自由記述1項目に集約し、財務・還元セクションを廃止する。
# 投資判断の既存内容は削除前に結合して残す。
# financial_policy は雛形から外す方針のため結合せず破棄する（ユーザー指示）。

from django.db import migrations, models

INVEST_SOURCES = [
    ('hypothesis', '【投資仮説】'),
    ('disconfirm', '【仮説が崩れる条件】'),
    ('next_check', '【次回決算で確認すること】'),
]


def merge_invest(apps, schema_editor):
    StockKarte = apps.get_model('karte', 'StockKarte')
    for k in StockKarte.objects.all():
        parts = []
        for field, label in INVEST_SOURCES:
            value = (getattr(k, field, '') or '').strip()
            if value:
                # どの項目に書いた内容か分かるよう見出しを残す
                parts.append(f'{label}\n{value}')
        if parts:
            k.invest_note = '\n\n'.join(parts)
            k.save(update_fields=['invest_note'])


def split_back(apps, schema_editor):
    """逆方向。結合済みの本文は分解できないため投資仮説へ戻す"""
    StockKarte = apps.get_model('karte', 'StockKarte')
    for k in StockKarte.objects.all():
        if k.invest_note:
            k.hypothesis = k.invest_note
            k.save(update_fields=['hypothesis'])


class Migration(migrations.Migration):

    dependencies = [
        ('karte', '0006_merge_business_competitive'),
    ]

    operations = [
        # 1) 先に新項目を追加
        migrations.AddField(
            model_name='stockkarte',
            name='invest_note',
            field=models.TextField(blank=True),
        ),
        # 2) 投資判断の既存データを結合してから
        migrations.RunPython(merge_invest, split_back),
        # 3) 旧項目を削除する
        migrations.RemoveField(model_name='stockkarte', name='hypothesis'),
        migrations.RemoveField(model_name='stockkarte', name='disconfirm'),
        migrations.RemoveField(model_name='stockkarte', name='next_check'),
        # 財務・還元は雛形から廃止
        migrations.RemoveField(model_name='stockkarte', name='financial_policy'),
    ]
