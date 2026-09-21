# 既存の振り返り（DiaryEntry.review_*）を DiaryReview の1件目として取り込む（2026-09-22）
from django.db import migrations


def forwards(apps, schema_editor):
    DiaryEntry = apps.get_model('diary', 'DiaryEntry')
    DiaryReview = apps.get_model('diary', 'DiaryReview')
    for e in DiaryEntry.objects.exclude(review_note='', review_result=''):
        if not DiaryReview.objects.filter(entry=e).exists():
            rv = DiaryReview.objects.create(entry=e, result=e.review_result, note=e.review_note)
            if e.reviewed_at:
                DiaryReview.objects.filter(pk=rv.pk).update(created_at=e.reviewed_at)


class Migration(migrations.Migration):
    dependencies = [('diary', '0017_diaryreview')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
