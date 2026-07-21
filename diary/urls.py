from django.urls import path

from . import views

app_name = 'diary'

urlpatterns = [
    path('', views.index, name='index'),
    path('stock-options.json', views.stock_options, name='stock_options'),
    path('create/', views.create, name='create'),
    path('<int:pk>/review/', views.review, name='review'),
    path('<int:pk>/delete/', views.delete, name='delete'),
]
