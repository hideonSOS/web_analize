from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'japan_kabu'

urlpatterns = [
    # 時価総額・出来高急増のページは 2026-09-30 に削除（ユーザー指示: 使わない）。旧 URL はカレンダーへ
    path('', RedirectView.as_view(pattern_name='market_calendar:index', permanent=False), name='index'),
    path('volume/', RedirectView.as_view(pattern_name='market_calendar:index', permanent=False), name='volume'),
    path('heatmap/', views.heatmap, name='heatmap'),
    path('impulse/', views.impulse, name='impulse'),
    path('drawdown/', views.drawdown, name='drawdown'),
    path('macro/', views.macro, name='macro'),
    path('stock/<str:code>/', views.stock_detail, name='stock_detail'),
]
