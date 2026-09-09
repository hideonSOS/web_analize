# 短期の設定行を get_or_create(pk=1) で作っていたため PostgreSQL の id シーケンスが進んでおらず、
# 練習用の2行目（kind='practice'）を作ろうとすると id=1 の重複で落ちた（2026-09-10 実測）。
# シーケンスを現在の最大 id に合わせ直す。SQLite など他 DB では何もしない
from django.core.management.color import no_style
from django.db import connection, migrations


def reset_sequence(apps, schema_editor):
    if connection.vendor != 'postgresql':
        return
    ContraSetting = apps.get_model('diary', 'ContraSetting')
    for sql in connection.ops.sequence_reset_sql(no_style(), [ContraSetting]):
        schema_editor.execute(sql)


class Migration(migrations.Migration):
    dependencies = [('diary', '0009_practice')]
    operations = [migrations.RunPython(reset_sequence, migrations.RunPython.noop)]
