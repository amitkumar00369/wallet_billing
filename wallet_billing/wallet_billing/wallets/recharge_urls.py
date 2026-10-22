from django.urls import path

from . import views

urlpatterns = [
    path("private/initiate/", views.RechargeCreateView.as_view(), name="recharge-initiate"),   
    path("callback/", views.RechargeCallbackView.as_view(), name="recharge-callback"),         
]
