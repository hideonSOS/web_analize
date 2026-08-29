from django.urls import path

from . import views

app_name = 'portfolio'

urlpatterns = [
    path('', views.index, name='index'),
    path('stocks/', views.stocks, name='stocks'),
    path('register/', views.register, name='register'),
]
