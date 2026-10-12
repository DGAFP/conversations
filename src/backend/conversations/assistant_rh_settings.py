"""Opt-in configuration for the Assistant RH fork (server-side secrets only)."""

from configurations import values


class AssistantRHSettings:
    """The API verifies signed per-request delegation, never a shared API key."""

    ASSISTANT_RH_ENABLED = values.BooleanValue(False, environ_prefix=None)
    ASSISTANT_RH_API_URL = values.Value("", environ_prefix=None)
    ASSISTANT_RH_DELEGATION_ISSUER = values.Value("", environ_prefix=None)
    ASSISTANT_RH_API_AUDIENCE = values.Value("assistant-rh-api", environ_prefix=None)
    ASSISTANT_RH_SIGNING_KEY_FILE = values.Value("", environ_prefix=None)
    ASSISTANT_RH_SIGNING_KID = values.Value("", environ_prefix=None)
    ASSISTANT_RH_AUDIT_KEY_FILE = values.Value("", environ_prefix=None)
