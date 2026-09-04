from django.urls import path

from . import views

app_name = 'website'

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.login, name='login'),
    path('login/verify/', views.login_verify, name='login_verify'),
    path('security/', views.security, name='security'),
    path('logout/', views.logout, name='logout'),
    path('feature/<int:num>/', views.feature, name='feature'),
]
