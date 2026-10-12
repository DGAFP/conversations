"""LLM configuration view."""

from django.conf import settings

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.assistant_rh import AssistantRHSessionPermission, configured_models, require_admission

from chat import serializers


class LLMConfigurationView(APIView):
    """View for listing available LLM models."""

    permission_classes = [
        AssistantRHSessionPermission,
    ]

    def get(self, request):
        """Handle GET requests to list available LLM models.

        Assistant RH exposes only the current user's configured corpus grants.

        Returns:
            Response: A response containing the list of available LLM models.
        """
        if settings.ASSISTANT_RH_ENABLED:
            require_admission(request.user)
        models = (
            configured_models(request.user).values()
            if settings.ASSISTANT_RH_ENABLED
            else settings.LLM_CONFIGURATIONS.values()
        )
        serializer = serializers.LLMConfigurationSerializer(
            {
                "models": models,
            },
        )
        response = Response(serializer.data, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response
