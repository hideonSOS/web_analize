# 保有中の取引にも建値ストップ（+7%）を適用する（2026-09-24。決済済みは触らない）
from django.db import migrations


def forwards(apps, schema_editor):
    Trade = apps.get_model('diary', 'Trade')
    for t in Trade.objects.filter(exit_date__isnull=True, be_trigger_pct__isnull=True):
        if 7.0 < (t.target_pct or 0):
            t.be_trigger_pct = 7.0
            t.save(update_fields=['be_trigger_pct'])


class Migration(migrations.Migration):
    dependencies = [('diary', '0019_breakeven_stop')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
