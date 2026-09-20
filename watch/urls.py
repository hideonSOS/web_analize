from django.urls import path

from . import views

app_name = 'watch'

urlpatterns = [
    path('', views.index, name='index'),
    path('add/', views.add, name='add'),
    path('reorder/', views.reorder, name='reorder'),
    path('<int:pk>/update/', views.update, name='update'),
    path('<int:pk>/delete/', views.delete, name='delete'),
    path('<int:pk>/prices/', views.fetch_prices, name='fetch_prices'),
]
