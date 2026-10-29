"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path, re_path

from lasuite.oidc_login.urls import urlpatterns as oidc_urls
from rest_framework.routers import DefaultRouter

from core.api import viewsets
from core.authentication import local
from core.file_upload.enums import FileToLLMMode

from chat.views import (
    AssistantHealthView,
    ChatConversationAttachmentViewSet,
    ChatCooldownView,
    ChatProjectAttachmentViewSet,
    ChatProjectViewSet,
    ChatViewSet,
    FileStreamView,
    LLMConfigurationView,
    ModelHealthView,
)
from chat.views.assistant_rh_documents import document as assistant_rh_document

# - Main endpoints
router = DefaultRouter()
router.register("users", viewsets.UserViewSet, basename="users")
router.register("chats", ChatViewSet, basename="chats")
router.register("projects", ChatProjectViewSet, basename="projects")

conversation_router = DefaultRouter()
conversation_router.register(
    "attachments", ChatConversationAttachmentViewSet, basename="conversation-attachments"
)

project_router = DefaultRouter()
project_router.register("attachments", ChatProjectAttachmentViewSet, basename="project-attachments")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                path(
                    "chats/<uuid:conversation_id>/messages/<str:message_id>/"
                    "sources/<int:source_index>/",
                    assistant_rh_document,
                    name="assistant-rh-document",
                ),
                *router.urls,
                path("authenticate/", local.login),
                path(
                    "local/authenticate/",
                    local.technical_login,
                    name="assistant-rh-technical-login",
                ),
                path("logout/", local.logout),
                *oidc_urls,
                path(
                    "llm-configuration/", LLMConfigurationView.as_view(), name="llm-configuration"
                ),
                path("model-health/", ModelHealthView.as_view(), name="model-health"),
                path("assistant-health/", AssistantHealthView.as_view(), name="assistant-health"),
                path("chat-cooldown/", ChatCooldownView.as_view(), name="chat-cooldown"),
                path(
                    "chats/<uuid:conversation_pk>/",
                    include(conversation_router.urls),
                ),
                path(
                    "projects/<uuid:project_pk>/",
                    include(project_router.urls),
                ),
            ]
            + (
                # Only allow file stream URL when configured for backend temporary URL mode
                [
                    re_path(
                        r"^file-stream/(?P<temporary_key>[a-zA-Z0-9_-]+)/$",
                        FileStreamView.as_view(),
                        name="file-stream",
                    ),
                ]
                if (
                    settings.FILE_TO_LLM_MODE == FileToLLMMode.BACKEND_TEMPORARY_URL
                    or settings.ENVIRONMENT == "test"
                )
                else []
            )
        ),
    ),
    path(f"api/{settings.API_VERSION}/config/", viewsets.ConfigView.as_view()),
]
