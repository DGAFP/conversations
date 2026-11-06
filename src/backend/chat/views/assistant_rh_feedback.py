"""Session-protected feedback bridge using server-owned message/run associations."""

import logging
import uuid
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import get_object_or_404

import httpx
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.assistant_rh import AssistantRHSessionPermission, visible_conversations

from chat.assistant_rh_delegation import sign_assertion
from chat.models import AssistantRHMessageRun

logger = logging.getLogger(__name__)
POSITIVE_REASONS = ("Clair", "Utile", "Pertinent", "Complet", "Précis")
NEGATIVE_REASONS = ("Confus", "Éléments faux", "Non pertinent", "Incomplet", "Sources manquantes")


class FeedbackUnavailable(APIException):
    """Do not expose upstream responses, identifiers or credentials."""

    status_code = 503
    default_detail = "Le service d’évaluation est momentanément indisponible. Réessayez."
    default_code = "feedback_unavailable"


class FeedbackSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The same fields and content rules as Assistant RH POST /v1/feedback."""

    stars = serializers.IntegerField(min_value=1, max_value=5)
    reasons_positive = serializers.ListField(
        child=serializers.ChoiceField(choices=POSITIVE_REASONS), default=list, max_length=32
    )
    reasons_negative = serializers.ListField(
        child=serializers.ChoiceField(choices=NEGATIVE_REASONS), default=list, max_length=32
    )
    comment = serializers.CharField(allow_blank=True, default="", max_length=4000)

    def validate(self, attrs):
        """Keep identities server-owned and reject incompatible or empty feedback."""
        if set(self.initial_data) - set(self.fields):
            raise ValidationError("Champs d’évaluation non autorisés.")
        positive, negative = attrs["reasons_positive"], attrs["reasons_negative"]
        if (
            (attrs["stars"] <= 2 and positive)
            or (attrs["stars"] == 5 and negative)
            or not (positive or negative or attrs["comment"])
        ):
            raise ValidationError(
                "Sélectionnez un motif compatible avec la note ou un commentaire."
            )
        return attrs


class AssistantRHFeedbackView(APIView):
    """Resolve the authorized assistant message on every read and write."""

    permission_classes = [AssistantRHSessionPermission]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def get_run(self, request, conversation_id, message_id):
        """A browser message ID is never used as an upstream completion ID."""
        if not settings.ASSISTANT_RH_ENABLED:
            raise NotFound()
        conversation = get_object_or_404(visible_conversations(request.user), pk=conversation_id)
        if not any(
            message.id == message_id and message.role == "assistant"
            for message in conversation.messages
        ):
            raise NotFound()
        return get_object_or_404(
            AssistantRHMessageRun, conversation=conversation, message_id=message_id
        )

    def upstream(self, request, run, method, body=None):
        """Sign each attempt after rechecking the live session and ministry grants."""
        try:
            assertion = sign_assertion(
                request.user.pk, request.session.session_key, run.conversation.ministry
            )
            url = settings.ASSISTANT_RH_API_URL.rstrip("/") + "/feedback"
            if method == "GET":
                url += "/" + quote(run.completion_id, safe="")
            request_id = uuid.uuid4().hex
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=httpx.Timeout(15, connect=5)
            ) as client:
                response = client.request(
                    method,
                    url,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + assertion,
                        "X-Client-Request-Id": request_id,
                    },
                )
            logger.info(
                "assistant_rh_feedback_request request_id=%s status=%s",
                request_id,
                response.status_code,
            )
            return response
        except httpx.HTTPError, ImproperlyConfigured, OSError:
            raise FeedbackUnavailable() from None

    def get(self, request, conversation_id, message_id):
        """No saved feedback returns 204; failures are never treated as success."""
        run = self.get_run(request, conversation_id, message_id)
        response = self.upstream(request, run, "GET")
        if response.status_code == 404:
            return Response(status=204)
        if response.status_code == 403:
            raise NotFound()
        if response.status_code != 200:
            raise FeedbackUnavailable()
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("completion_id") != run.completion_id:
                raise FeedbackUnavailable()
            serializer = FeedbackSerializer(
                data={
                    key: data[key]
                    for key in ("stars", "reasons_positive", "reasons_negative", "comment")
                }
            )
            if not serializer.is_valid():
                raise FeedbackUnavailable()
        except ValueError, KeyError, TypeError:
            raise FeedbackUnavailable() from None
        return Response(serializer.validated_data)

    def post(self, request, conversation_id, message_id):
        """The API remains the durable store and derives helpful and ownership itself."""
        run = self.get_run(request, conversation_id, message_id)
        serializer = FeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = self.upstream(
            request, run, "POST", {"completion_id": run.completion_id, **serializer.validated_data}
        )
        if response.status_code in (403, 404):
            raise NotFound()
        if response.status_code == 422:
            raise ValidationError("Évaluation invalide. Vérifiez les motifs et le commentaire.")
        if response.status_code != 204:
            raise FeedbackUnavailable()
        return Response(status=204)
