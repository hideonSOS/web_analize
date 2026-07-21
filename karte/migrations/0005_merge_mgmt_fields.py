# 経営陣の考課を3項目から自由記述1項目に集約する。
# 既存の記入内容を失わないよう、削除の前に mgmt_note へ結合する。

from django.db import migrations, models


def merge_to_note(apps, schema_editor):
    """mgmt_track_record / mgmt_capital / mgmt_stance を mgmt_note へ結合"""
    StockKarte = apps.get_model('karte', 'StockKarte')
    labels = [
        ('mgmt_track_record', '【実績・公約の達成度】'),
        ('mgmt_capital', '【資本配分】'),
        ('mgmt_stance', '【姿勢・開示】'),
    ]
    for k in StockKarte.objects.all():
        parts = []
        for field, label in labels:
            value = (getattr(k, field, '') or '').strip()
            if value:
                # どの項目に書いた内容か分かるよう見出しを残す
                parts.append(f'{label}\n{value}')
        if parts:
            k.mgmt_note = '\n\n'.join(parts)
            k.save(update_fields=['mgmt_note'])


def split_back(apps, schema_editor):
    """逆方向。結合済みの本文は分解できないため先頭の項目に戻す"""
    StockKarte = apps.get_model('karte', 'StockKarte')
    for k in StockKarte.objects.all():
        if k.mgmt_note:
            k.mgmt_track_record = k.mgmt_note
            k.save(update_fields=['mgmt_track_record'])


class Migration(migrations.Migration):

    dependencies = [
        ('karte', '0004_remove_executive_name_remove_executive_title'),
    ]

    operations = [
        # 1) 先に新項目を追加
        migrations.AddField(
            model_name='stockkarte',
            name='mgmt_note',
            field=models.TextField(blank=True),
        ),
        # 2) 既存データを結合してから
        migrations.RunPython(merge_to_note, split_back),
        # 3) 旧項目を削除する
        migrations.RemoveField(model_name='stockkarte', name='mgmt_capital'),
        migrations.RemoveField(model_name='stockkarte', name='mgmt_stance'),
        migrations.RemoveField(model_name='stockkarte', name='mgmt_track_record'),
    ]
