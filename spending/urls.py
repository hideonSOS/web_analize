from django.urls import path

from . import views

app_name = 'spending'

urlpatterns = [
    path('', views.index, name='index'),
    path('month/', views.month, name='month'),
    path('transactions/', views.transactions, name='transactions'),
]
