"""監視（2026-09-21 新設）。まずはナビから遷移できる雛形だけ。機能はこれから決める（ユーザー指示）"""
from django.shortcuts import render


def index(request):
    return render(request, 'watch/index.html', {})
