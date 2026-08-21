from django.urls import path

from . import views

app_name = 'japan_kabu'

urlpatterns = [
    path('', views.index, name='index'),
    path('volume/', views.volume_ranking, name='volume'),
    path('heatmap/', views.heatmap, name='heatmap'),
    path('impulse/', views.impulse, name='impulse'),
    path('stock/<str:code>/', views.stock_detail, name='stock_detail'),
]
