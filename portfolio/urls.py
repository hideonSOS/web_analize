from django.urls import path

from . import views

app_name = 'portfolio'

urlpatterns = [
    path('', views.index, name='index'),
    path('stocks/', views.stocks, name='stocks'),
    path('stocks/<str:scope>/', views.stock_focus, name='stock_focus'),
    path('register/', views.register, name='register'),
    path('drill/', views.drill, name='drill'),
]
