from django.urls import path

from . import views

app_name = 'diary'

urlpatterns = [
    path('', views.index, name='index'),
    path('stock-options.json', views.stock_options, name='stock_options'),
    path('create/', views.create, name='create'),
    path('contra/', views.contra, name='contra'),
    path('practice/', views.practice, name='practice'),
    path('note/<int:pk>/image/', views.note_image, name='note_image'),
    path('<int:pk>/track/', views.track, name='track'),
    path('order/<int:pk>/fill/', views.order_fill, name='order_fill'),
    path('order/<int:pk>/unfill/', views.order_unfill, name='order_unfill'),
    path('order/<int:pk>/delete/', views.order_delete, name='order_delete'),
    path('order/refresh/', views.order_refresh, name='order_refresh'),
    path('<int:pk>/review/', views.review, name='review'),
    path('review/<int:pk>/delete/', views.review_delete, name='review_delete'),
    path('<int:pk>/delete/', views.delete, name='delete'),
]
