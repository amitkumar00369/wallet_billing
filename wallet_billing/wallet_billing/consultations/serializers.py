from rest_framework import serializers

from accounts.models import User

from .models import ConsultationSession


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultationSession
        fields = [
            "id", "user", "provider", "status", "per_minute_rate",
            "started_at", "ended_at", "billed_minutes", "duration_seconds",
            "ended_by", "end_reason", "created_at", "updated_at",
        ]
        read_only_fields = fields


class AssignSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField()

    def validate_provider_id(self, value):
        provider = User.objects.filter(pk=value, role=User.Role.PROVIDER).first()
        if provider is None:
            raise serializers.ValidationError("No provider exists with this id.")
        return provider
