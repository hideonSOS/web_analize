from django.urls import path

from . import views

app_name = 'karte'

urlpatterns = [
    path('', views.index, name='index'),
    path('stock-options.json', views.stock_options, name='stock_options'),
    path('create/', views.create, name='create'),
    path('<str:code>/', views.detail, name='detail'),
    path('<str:code>/save/', views.save, name='save'),
    path('<str:code>/delete/', views.delete, name='delete'),
    path('<str:code>/target/add/', views.add_target, name='add_target'),
    path('<str:code>/target/<int:pk>/delete/', views.delete_target, name='delete_target'),
    path('<str:code>/exec/add/', views.add_executive, name='add_executive'),
    path('<str:code>/exec/<int:pk>/delete/', views.delete_executive, name='delete_executive'),
    path('<str:code>/video/add/', views.add_video, name='add_video'),
    path('<str:code>/video/<int:pk>/delete/', views.delete_video, name='delete_video'),
    path('<str:code>/shot/add/', views.add_screenshot, name='add_screenshot'),
    path('<str:code>/shot/<int:pk>/delete/', views.delete_screenshot, name='delete_screenshot'),
    path('<str:code>/kpi/add/', views.add_kpi, name='add_kpi'),
    path('<str:code>/kpi/<int:pk>/delete/', views.delete_kpi, name='delete_kpi'),
]
