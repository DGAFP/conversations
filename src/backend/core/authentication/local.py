"""Temporary local login using Django's password and CSRF protections."""

import hashlib
import math

from django import forms
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.http import Http404, HttpResponse

from lasuite.oidc_login.views import OIDCLogoutView
from mozilla_django_oidc.urls import OIDCAuthenticateClass
from rest_framework.throttling import SimpleRateThrottle


def local_login_enabled():
    """Local login is only available in the explicitly enabled RH mode."""
    return settings.ASSISTANT_RH_ENABLED and settings.ASSISTANT_RH_LOCAL_LOGIN_ENABLED


def public_local_login_enabled():
    """The normal login becomes OIDC while the technical form remains explicit."""
    return local_login_enabled() and not settings.ASSISTANT_RH_OIDC_ENABLED


class LocalAuthenticationForm(AuthenticationForm):
    """An ordinary local account still needs central admission."""

    username = forms.EmailField(
        label="Adresse e-mail",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.assistant_rh_admitted:
            raise forms.ValidationError("Accès en attente de validation DGAFP.", code="admission")
        if settings.ASSISTANT_RH_OIDC_ENABLED:
            allowed = {
                email.strip().casefold()
                for email in settings.ASSISTANT_RH_LOCAL_LOGIN_ALLOWED_EMAILS
            }
            if not user.admin_email or user.admin_email.casefold() not in allowed:
                raise forms.ValidationError(
                    "La connexion locale est réservée aux comptes techniques autorisés.",
                    code="local_login_restricted",
                )


class LocalLoginThrottle(SimpleRateThrottle):
    """Bound password attempts per submitted account using the deployment's shared cache."""

    rate = "10/min"
    scope = "assistant_rh_login"

    def get_cache_key(self, request, view):
        # Behind the ingress every peer is the proxy, and forwarded headers are
        # user-controlled: an IP bucket would let anyone lock every account out.
        account = request.POST.get("username", "").strip().lower()
        ident = hashlib.sha256(account.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LocalLogoutView(LogoutView):
    """GET displays confirmation; only a CSRF-protected POST ends the session."""

    http_method_names = ["get", "post", "options"]
    template_name = "assistant_rh/logout.html"


def login(request):
    """Keep the frontend's existing entry point for either authentication mode."""
    if not public_local_login_enabled():
        return OIDCAuthenticateClass.as_view()(request)
    return technical_login(request)


def technical_login(request):
    """Keep the monitored password flow separate, CSRF protected and allowlisted."""
    if not local_login_enabled():
        raise Http404
    if request.method == "POST":
        throttle = LocalLoginThrottle()
        if not throttle.allow_request(request, None):
            return HttpResponse(
                "Trop de tentatives. Réessayez dans une minute.",
                status=429,
                headers={"Retry-After": str(math.ceil(throttle.wait()))},
            )
    return LoginView.as_view(
        authentication_form=LocalAuthenticationForm,
        template_name="assistant_rh/login.html",
    )(request)


def logout(request):
    """End the actual authentication flow, independently of the current login mode."""
    local_session = (
        settings.ASSISTANT_RH_ENABLED
        and request.session.get(BACKEND_SESSION_KEY) == "django.contrib.auth.backends.ModelBackend"
    )
    view = LocalLogoutView if local_session or public_local_login_enabled() else OIDCLogoutView
    return view.as_view()(request)
