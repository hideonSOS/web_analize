"""カルテ用のテンプレートフィルタ（デュポン5分解の表・2026-09-27）"""
from django import template

register = template.Library()


@register.filter
def getitem(d, key):
    """dict[key]（テンプレートで変数のキーを引く）"""
    try:
        return d.get(key) if d is not None else None
    except AttributeError:
        return None


@register.filter
def dp_fmt(v, fmt):
    """5分解の各項の表示: ratio=0.72 / pct=12.3% / times=0.85回 / x=2.10倍"""
    if v is None:
        return ''
    if fmt == 'pct':
        return f'{v:.1f}%'
    if fmt == 'times':
        return f'{v:.2f}回'
    if fmt == 'x':
        return f'{v:.2f}倍'
    return f'{v:.2f}'
