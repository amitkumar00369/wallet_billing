import hmac

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import (
    RechargeCallbackSerializer, RechargeCreateSerializer, RechargeSerializer, WalletSerializer,
)


class WalletView(APIView):
    def get(self, request):
        return Response(WalletSerializer(services.get_wallet(request.user)).data)


class RechargeCreateView(APIView):
    def post(self, request):
        serializer = RechargeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recharge = services.create_recharge(request.user, serializer.validated_data["amount"])
        print("rechareg", recharge)
        return Response(RechargeSerializer(recharge).data, status=status.HTTP_201_CREATED)


class RechargeCallbackView(APIView):


    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        supplied = request.headers.get("X-Callback-Secret", "")
        if not hmac.compare_digest(supplied, settings.RECHARGE_CALLBACK_SECRET):
            raise PermissionDenied("Invalid callback secret.")

        serializer = RechargeCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recharge, credited = services.process_recharge_callback(
            serializer.validated_data["reference"], serializer.validated_data["status"]
        )
        print("hdhdggddjhjdhjf", credited,recharge)
        return Response({"reference": recharge.reference, "status": recharge.status, "credited": credited})
