"""Authentication Backends for the Conversations core app."""

import logging
import re
import time

from django.conf import settings
from django.core.exceptions import SuspiciousOperation

from lasuite.oidc_login.backends import (
    OIDCAuthenticationBackend as LaSuiteOIDCAuthenticationBackend,
)

from core.brevo import add_user_to_brevo_list
from core.models import AccessBypassEmail, DuplicateEmailError

logger = logging.getLogger(__name__)


# NB: deliberately NOT a SuspiciousOperation/PermissionDenied subclass. Both
# mozilla-django-oidc (authenticate() catches SuspiciousOperation) and Django's
# auth.authenticate() (catches PermissionDenied) would swallow those into a
# `None` result, which redirects to the generic failure URL with no message.
# A plain exception propagates up to OIDCAuthenticationCallbackView, which turns
# it into a redirect to the access-denied page.
class OIDCRoleAccessDenied(Exception):
    """Raised when an authenticated user lacks a role required to access the app."""


class OIDCAuthenticationBackend(LaSuiteOIDCAuthenticationBackend):
    """Custom OpenID Connect (OIDC) Authentication Backend.

    This class overrides the default OIDC Authentication Backend to accommodate differences
    in the User and Identity models, and handles signed and/or encrypted UserInfo response.
    """

    def verify_token(self, token, **kwargs):
        """Keep upstream signature/nonce verification and enforce the RH token audience."""
        payload = super().verify_token(token, **kwargs)
        if not (settings.ASSISTANT_RH_ENABLED and settings.ASSISTANT_RH_OIDC_ENABLED):
            return payload
        now = time.time()
        audience = payload.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        valid_audience = (
            isinstance(audiences, list)
            and bool(audiences)
            and all(isinstance(item, str) for item in audiences)
            and self.OIDC_RP_CLIENT_ID in audiences
        )
        # Python bool is an int subclass; require actual JSON integers for dates.
        # pylint: disable=unidiomatic-typecheck
        valid_dates = (
            type(payload.get("exp")) is int
            and type(payload.get("iat")) is int
            and payload["exp"] > now - 30
            and payload["iat"] <= now + 30
            and payload["exp"] > payload["iat"]
            and (
                "nbf" not in payload or (type(payload["nbf"]) is int and payload["nbf"] <= now + 30)
            )
        )
        # pylint: enable=unidiomatic-typecheck
        authorized_party = payload.get("azp")
        valid_party = authorized_party in (None, self.OIDC_RP_CLIENT_ID) and (
            not valid_audience or len(audiences) == 1 or authorized_party == self.OIDC_RP_CLIENT_ID
        )
        if not (
            payload.get("iss") == settings.OIDC_OP_URL
            and isinstance(payload.get("sub"), str)
            and bool(payload["sub"])
            and valid_audience
            and valid_dates
            and valid_party
        ):
            raise SuspiciousOperation("Invalid OIDC token claims.")
        return payload

    def get_userinfo(self, access_token, id_token, payload):
        """UserInfo must identify the same subject as the verified ID token."""
        userinfo = super().get_userinfo(access_token, id_token, payload)
        if settings.ASSISTANT_RH_ENABLED and settings.ASSISTANT_RH_OIDC_ENABLED:
            if userinfo.get("sub") != payload.get("sub"):
                raise SuspiciousOperation("OIDC subject mismatch.")
        return userinfo

    def get_extra_claims(self, user_info):
        """
        Return extra claims from user_info.

        Args:
          user_info (dict): The user information dictionary.

        Returns:
          dict: A dictionary of extra claims.
        """
        # Only store a well-formed SIRET (14 digits); fall back to an empty
        # string for anything else so a malformed claim never blocks login
        # (the field is optional and non-nullable).
        siret = (user_info.get("siret") or "").strip()
        return {
            "full_name": self.compute_full_name(user_info),
            "short_name": user_info.get(settings.OIDC_USERINFO_SHORTNAME_FIELD),
            "organization_siret": siret if re.fullmatch(r"\d{14}", siret) else "",
        }

    def verify_claims(self, claims):
        """Allow authentication only for users holding a required role.

        Extends the base essential-claim verification: when
        ``OIDC_ALLOWED_ROLES`` is configured, the user must expose at least one
        of those roles in the ``roles`` claim (a flat list, the shape used by
        both Keycloak in dev and ProConnect in production). This gates both
        login and account creation, since it runs before either branch in
        ``get_or_create_user``. An empty ``OIDC_ALLOWED_ROLES`` disables the
        restriction (default behavior).

        As a fallback, a user who lacks the required role is still allowed in if
        their email is on the :class:`~core.models.AccessBypassEmail` allow-list
        (active and not expired).
        """
        if not super().verify_claims(claims):
            return False

        if settings.ASSISTANT_RH_ENABLED:
            return True

        allowed_roles = settings.OIDC_ALLOWED_ROLES
        if not allowed_roles:
            return True

        user_roles = claims.get("roles") or []
        if isinstance(user_roles, str):
            user_roles = user_roles.split()

        if set(allowed_roles) & set(user_roles):
            return True

        # Fallback: allow users explicitly added to the access bypass list,
        # even though they lack the required role.
        email = claims.get("email")
        if AccessBypassEmail.is_email_allowed(email):
            return True

        logger.warning(
            "OIDC access denied for sub %s: roles %s do not include any of %s",
            claims.get("sub"),
            user_roles,
            allowed_roles,
        )
        raise OIDCRoleAccessDenied("User does not have a role required to access the application.")

    def get_existing_user(self, sub, email):
        """Fetch existing user by sub or email."""

        if settings.ASSISTANT_RH_ENABLED:
            return self.UserModel.objects.filter(sub=sub).first() if sub else None

        try:
            return self.UserModel.objects.get_user_by_sub_or_email(sub, email)
        except DuplicateEmailError as err:
            raise SuspiciousOperation(err.message) from err

    def create_user(self, claims):
        """
        Create a new user with the given claims.

        This is the only place where we create users, so we set the default value
        for allow_conversation_analytics here.
        This allows to enable analytics by default during the alpha version and get
        more information about the usage of the application.
        """
        return super().create_user(
            claims
            | {
                "allow_conversation_analytics": settings.DEFAULT_ALLOW_CONVERSATION_ANALYTICS,
                "allow_smart_web_search": settings.DEFAULT_ALLOW_SMART_WEB_SEARCH,
            }
        )

    def post_get_or_create_user(self, user, claims, is_new_user):
        """Add the user to the Brevo follow-up list.

        Only on signup: adding a contact already in the list is a no-op for Brevo, so
        doing it on every login only costs an HTTP round-trip in the login path.

        A failure is logged and never retried (as in every other Brevo call site).
        The follow-up list is a marketing aid: recovering a missed contact is a
        re-import, not something worth persisting per-user state for.
        """
        if settings.ASSISTANT_RH_ENABLED:
            if user is None or not user.is_active or not user.assistant_rh_admitted:
                raise OIDCRoleAccessDenied("Admission DGAFP required.")
            return

        if is_new_user and settings.BREVO_FOLLOWUP_LIST_ID:
            add_user_to_brevo_list([user.email], settings.BREVO_FOLLOWUP_LIST_ID)
