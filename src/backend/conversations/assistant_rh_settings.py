"""Opt-in configuration for the Assistant RH fork (server-side secrets only)."""

from configurations import values


class AssistantRHSettings:
    """The API verifies signed per-request delegation, never a shared API key."""

    ASSISTANT_RH_ENABLED = values.BooleanValue(False, environ_prefix=None)
    ASSISTANT_RH_LOCAL_LOGIN_ENABLED = values.BooleanValue(False, environ_prefix=None)
    ASSISTANT_RH_API_URL = values.Value("", environ_prefix=None)
    ASSISTANT_RH_DELEGATION_ISSUER = values.Value("", environ_prefix=None)
    ASSISTANT_RH_API_AUDIENCE = values.Value("assistant-rh-api", environ_prefix=None)
    ASSISTANT_RH_SIGNING_KEY_FILE = values.Value("", environ_prefix=None)
    ASSISTANT_RH_SIGNING_KID = values.Value("", environ_prefix=None)
    ASSISTANT_RH_AUDIT_KEY_FILE = values.Value("", environ_prefix=None)

    @classmethod
    def setup(cls):
        """Allow the temporary password mode to start without any OIDC provider."""
        super().setup()  # pylint: disable=no-member
        if cls.ASSISTANT_RH_ENABLED and cls.ASSISTANT_RH_LOCAL_LOGIN_ENABLED:
            cls.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
            cls.REST_FRAMEWORK = {
                **cls.REST_FRAMEWORK,
                "DEFAULT_AUTHENTICATION_CLASSES": (
                    "rest_framework.authentication.SessionAuthentication",
                ),
            }
            cls.FRONTEND_SILENT_LOGIN_ENABLED = False
