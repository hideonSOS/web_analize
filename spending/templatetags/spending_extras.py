"""支出画面用のテンプレートフィルタ"""
from django import template

register = template.Library()


@register.filter
def div12(value):
    """年額 → 月額。節約候補の「年◯円＝月◯円」の併記に使う"""
    try:
        return int(value) // 12
    except (TypeError, ValueError):
        return 0
