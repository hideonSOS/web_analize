from django.urls import path

from . import views

app_name = 'website'

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.login, name='login'),
    path('login/verify/', views.login_verify, name='login_verify'),
    path('security/', views.security, name='security'),
    # パスキー（WebAuthn）
    path('passkey/register/options/', views.passkey_register_options, name='passkey_register_options'),
    path('passkey/register/verify/', views.passkey_register_verify, name='passkey_register_verify'),
    path('passkey/auth/options/', views.passkey_auth_options, name='passkey_auth_options'),
    path('passkey/auth/verify/', views.passkey_auth_verify, name='passkey_auth_verify'),
    path('passkey/<int:pk>/delete/', views.passkey_delete, name='passkey_delete'),
    path('logout/', views.logout, name='logout'),
    path('feature/<int:num>/', views.feature, name='feature'),
]
