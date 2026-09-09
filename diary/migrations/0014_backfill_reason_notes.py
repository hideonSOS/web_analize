# 既存の取引の「なぜ買ったか」（entry_note の各行）を TradeNote（情報・建て日）として取り込む。
# 購入時の理由と購入後のコメントを同じ時系列で見る方針（2026-09-10）に合わせるため
from datetime import datetime, time

from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Trade = apps.get_model('diary', 'Trade')
    TradeNote = apps.get_model('diary', 'TradeNote')
    for t in Trade.objects.all():
        lines = [x.strip() for x in (t.entry_note or '').splitlines() if x.strip()]
        if not lines or TradeNote.objects.filter(trade=t, kind='info').exists():
            continue
        when = timezone.make_aware(datetime.combine(t.entry_date, time(9, 0)))
        for text in lines:
            n = TradeNote.objects.create(trade=t, text=text, kind='info', at_entry=True)
            TradeNote.objects.filter(pk=n.pk).update(created_at=when)


class Migration(migrations.Migration):
    dependencies = [('diary', '0013_note_kind_info')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
