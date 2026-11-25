from django.urls import path

from fuel_route import views

urlpatterns = [
    path('api/route/', views.route_plan, name='route_plan'),
]
