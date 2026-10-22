from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsProviderRole, IsUserRole
from billing import services as billing_services

from . import services
from .serializers import AssignSerializer, SessionSerializer

USER_ONLY_ACTIONS = {"create", "assign", "start"}
PROVIDER_ONLY_ACTIONS = {"accept"}


class SessionViewSet(viewsets.ViewSet):
    def get_permissions(self):
        permissions = [IsAuthenticated()]
        if self.action in USER_ONLY_ACTIONS:
            permissions.append(IsUserRole())
        elif self.action in PROVIDER_ONLY_ACTIONS:
            permissions.append(IsProviderRole())
        return permissions

    def create(self, request):
        session = services.create_session(request.user)
        return Response(SessionSerializer(session).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        session = services.get_session_for_actor(pk, request.user)
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = services.assign_provider(pk, request.user, serializer.validated_data["provider_id"])
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return Response(SessionSerializer(services.accept_session(pk, request.user)).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return Response(SessionSerializer(services.start_session(pk, request.user)).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        return Response(SessionSerializer(services.end_session(pk, request.user)).data)

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        # only the session's own user or provider may trigger billing
        session = services.get_session_for_actor(pk, request.user)
        result = billing_services.bill_session(session.pk)
        return Response({
            "session": SessionSerializer(result.session).data,
            "minutes_charged": result.minutes_charged,
            "amount_charged": str(result.amount_charged),
        })
