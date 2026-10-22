from django.urls import path

from . import views

urlpatterns = [
    path("private/balance/", views.WalletView.as_view(), name="wallet-balance"),
]
