from django.urls import path

from . import views

app_name = 'kabutan'

urlpatterns = [
    path('', views.index, name='index'),
]
