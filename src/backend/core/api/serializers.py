"""Client serializers for the conversations core app."""

from django.conf import settings

from rest_framework import serializers

from core import models


class UserSerializer(serializers.ModelSerializer):
    """Serialize users."""

    def update(self, instance, validated_data):
        if settings.ASSISTANT_RH_ENABLED:
            # A concurrent admin revocation must survive a profile-preference update.
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save(update_fields=set(validated_data) | {"updated_at"})
            return instance
        return super().update(instance, validated_data)

    class Meta:
        model = models.User
        fields = [
            "id",
            "allow_conversation_analytics",
            "allow_datagouv_connector",
            "allow_smart_web_search",
            "email",
            "full_name",
            "short_name",
            "language",
            "sub",
        ]
        read_only_fields = ["id", "email", "full_name", "short_name", "sub"]
