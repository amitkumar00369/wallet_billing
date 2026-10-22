from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/wallet/", include("wallets.wallet_urls")),
    path("api/v1/recharge/", include("wallets.recharge_urls")),
    path("api/v1/sessions/", include("consultations.urls")),
]
