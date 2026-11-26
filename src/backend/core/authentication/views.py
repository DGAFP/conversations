"""Authentication views for the Conversations core app."""

from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from csp.constants import SELF
from csp.decorators import csp_replace
from lasuite.oidc_login.views import (
    OIDCAuthenticationCallbackView as LaSuiteOIDCAuthenticationCallbackView,
)

from core.authentication import invitations
from core.authentication.backends import OIDCAuthenticationBackend, OIDCRoleAccessDenied

# Frontend path shown to users whose account is not allowed to access the app.
# Keep the trailing slash: the frontend is a Next.js static export with
# trailingSlash=true, so the canonical URL is /unauthorized/. Redirecting to the
# bare path makes the frontend nginx issue a directory redirect that leaks its
# internal listen port (e.g. :8080) and breaks behind the TLS ingress.
ACCESS_DENIED_PATH = "/unauthorized/"


class OIDCAuthenticationCallbackView(LaSuiteOIDCAuthenticationCallbackView):
    """Callback view that turns a role-based access denial into a friendly redirect.

    When the backend rejects a user for lacking a required role, the resulting
    exception bubbles up through the auth flow (it would otherwise surface as a
    raw 400). We intercept it here and redirect to the frontend access-denied
    page, instead of the default failure URL which is the app root and would
    bounce the user straight back into the login loop.
    """

    def get(self, request):
        """Handle the OIDC callback, redirecting denied users to the access-denied page."""
        try:
            return super().get(request)
        except invitations.InvitationRequired as exc:
            for key in ("oidc_id_token", "oidc_access_token"):
                request.session.pop(key, None)
            if not request.user.is_authenticated and invitations.start(request, exc.claims):
                return redirect("assistant-rh-proconnect-confirm")
            return HttpResponseRedirect(f"{self.failure_url.rstrip('/')}{ACCESS_DENIED_PATH}")
        except OIDCRoleAccessDenied:
            return HttpResponseRedirect(f"{self.failure_url.rstrip('/')}{ACCESS_DENIED_PATH}")


@sensitive_post_parameters("code")
@never_cache
@csrf_protect
@csp_replace({"form-action": [SELF]})
@require_http_methods(["GET", "POST"])
def confirm_invitation(request):
    """Complete the pending invitation and let OIDC start a standard session."""
    if not invitations.enabled():
        raise Http404

    pending = request.session.get(invitations.SESSION_KEY)
    if not pending or request.user.is_authenticated:
        return redirect("assistant-rh-proconnect-login")

    context = {}
    if request.method == "POST":
        code = request.POST.get("code", "").strip().lower()
        if invitations.confirm(pending, code, OIDCAuthenticationBackend().create_oidc_user):
            request.session.pop(invitations.SESSION_KEY, None)
            # Start a GET navigation from the document so the form policy stays
            # same-origin even though the next OIDC redirect leaves the site.
            context["confirmed"] = True
        else:
            context["invalid_code"] = True
    return render(request, "assistant_rh/proconnect_confirmation.html", context)
