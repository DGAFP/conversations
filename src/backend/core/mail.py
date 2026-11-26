"""Django email backend backed by the Scaleway Transactional Email (TEM) HTTP API."""

import logging
import re
from email.utils import parseaddr
from urllib.parse import urlparse

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TEM_API_URL = "https://api.scaleway.com"
DEFAULT_TEM_REGION = "fr-par"
DEFAULT_TEM_TIMEOUT = 10.0
REGION_RE = re.compile(r"^[a-z0-9-]+$")
FAILED_STATUSES = frozenset({"failed", "canceled"})


class ScalewayTEMError(Exception):
    """Raised when Scaleway TEM rejects or cannot deliver an email."""


def _parse_address(raw_address):
    """Parse an RFC 5322 address string into a Scaleway TEM address dict."""
    display_name, address = parseaddr(raw_address or "")
    address = address.strip()
    if not address or "@" not in address or any(ch in address for ch in "\r\n"):
        raise ScalewayTEMError("Invalid email address for Scaleway TEM.")
    entry = {"email": address}
    display_name = display_name.strip()
    if display_name and not any(ch in display_name for ch in "\r\n"):
        entry["name"] = display_name
    return entry


def _extract_bodies(message):
    """Extract plain text and optional HTML bodies from a Django EmailMessage."""
    text_body = ""
    html_body = ""
    if getattr(message, "content_subtype", "plain") == "html":
        html_body = message.body or ""
    else:
        text_body = message.body or ""

    for content, mimetype in getattr(message, "alternatives", ()):
        if mimetype == "text/html" and not html_body:
            html_body = content
        elif mimetype == "text/plain" and not text_body:
            text_body = content

    return text_body, html_body


def _build_additional_headers(message):
    """Convert Reply-To and safe extra headers into Scaleway TEM header entries."""
    headers = []
    has_reply_to = False
    for key, value in (getattr(message, "extra_headers", None) or {}).items():
        key_str = str(key).strip()
        val_str = str(value).strip()
        if not key_str or any(ch in key_str + val_str for ch in "\r\n"):
            continue
        if key_str.lower() == "reply-to":
            has_reply_to = True
        headers.append({"key": key_str, "value": val_str})

    reply_to = getattr(message, "reply_to", None) or []
    if reply_to and not has_reply_to:
        cleaned = [addr.strip() for addr in reply_to if addr and not any(c in addr for c in "\r\n")]
        if cleaned:
            headers.append({"key": "Reply-To", "value": ", ".join(cleaned)})
    return headers


def _build_payload(message, project_id):
    """Build the JSON request payload for Scaleway TEM create_email."""
    from_address = _parse_address(message.from_email or settings.EMAIL_FROM)
    to_addresses = [_parse_address(addr) for addr in (message.to or ())]
    if not to_addresses:
        raise ScalewayTEMError("Scaleway TEM email requires at least one recipient.")

    text_body, html_body = _extract_bodies(message)
    payload = {
        "from": from_address,
        "to": to_addresses,
        "subject": message.subject or "",
        "text": text_body,
        "project_id": project_id,
    }
    if html_body:
        payload["html"] = html_body
    if message.cc:
        payload["cc"] = [_parse_address(addr) for addr in message.cc]
    if message.bcc:
        payload["bcc"] = [_parse_address(addr) for addr in message.bcc]
    additional_headers = _build_additional_headers(message)
    if additional_headers:
        payload["additional_headers"] = additional_headers
    return payload


def _resolve_option(kwargs, key, setting_name, default):
    """Return an explicit kwarg when provided (even if empty), else Django setting or default."""
    value = kwargs.get(key)
    if value is None:
        value = getattr(settings, setting_name, None)
    if value is None:
        return default
    return value


class ScalewayTEMEmailBackend(BaseEmailBackend):
    """Send transactional emails via Scaleway TEM REST API using httpx.

    Uses httpx directly instead of scaleway-sdk-python so request timeouts
    (including Django's get_connection(timeout=...)) are always enforced and
    email bodies (such as one-time activation codes) are never logged.
    """

    def __init__(self, fail_silently=False, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.api_url = _resolve_option(
            kwargs, "api_url", "SCW_TEM_API_URL", DEFAULT_TEM_API_URL
        ).rstrip("/")
        self.region = _resolve_option(
            kwargs, "region", "SCW_TEM_REGION", DEFAULT_TEM_REGION
        ).strip()
        self.project_id = _resolve_option(kwargs, "project_id", "SCW_TEM_PROJECT_ID", "").strip()
        self.secret_key = _resolve_option(kwargs, "secret_key", "SCW_TEM_SECRET_KEY", "").strip()
        configured_timeout = (
            timeout
            if timeout is not None
            else getattr(settings, "SCW_TEM_TIMEOUT", DEFAULT_TEM_TIMEOUT)
        )
        self.timeout = float(configured_timeout)
        self._client = None

    def _endpoint(self):
        parsed = urlparse(self.api_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ScalewayTEMError("SCW_TEM_API_URL must be a valid HTTPS URL.")
        if not REGION_RE.fullmatch(self.region):
            raise ScalewayTEMError("SCW_TEM_REGION contains invalid characters.")
        if not self.project_id or not self.secret_key:
            raise ScalewayTEMError("Scaleway TEM credentials or project ID are not configured.")
        if self.timeout <= 0:
            raise ScalewayTEMError("Scaleway TEM timeout must be strictly positive.")
        return f"{self.api_url}/transactional-email/v1alpha1/regions/{self.region}/emails"

    def open(self):
        """Open a reusable httpx client for the connection lifetime."""
        if self._client is not None:
            return False
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=False)
        return True

    def close(self):
        """Close the underlying httpx client if open."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def _send_one(self, client, endpoint, message):
        payload = _build_payload(message, self.project_id)
        try:
            response = client.post(
                endpoint,
                json=payload,
                headers={
                    "X-Auth-Token": self.secret_key,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("Scaleway TEM request failed (%s)", type(exc).__name__)
            raise ScalewayTEMError("Scaleway TEM HTTP request failed.") from exc

        if response.status_code >= 400:
            logger.warning("Scaleway TEM rejected email with HTTP %s", response.status_code)
            raise ScalewayTEMError(f"Scaleway TEM returned HTTP {response.status_code}.")

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("Scaleway TEM returned a non-JSON response")
            raise ScalewayTEMError("Scaleway TEM returned a non-JSON response.") from exc

        emails = data.get("emails") if isinstance(data, dict) else None
        if not isinstance(emails, list) or not emails:
            logger.warning("Scaleway TEM response did not include accepted emails")
            raise ScalewayTEMError("Scaleway TEM response did not include accepted emails.")

        for item in emails:
            status = item.get("status") if isinstance(item, dict) else None
            if status in FAILED_STATUSES:
                logger.warning("Scaleway TEM reported immediate email status '%s'", status)
                raise ScalewayTEMError(f"Scaleway TEM reported status '{status}'.")

    def send_messages(self, email_messages):
        """Send one or more Django EmailMessage objects via Scaleway TEM."""
        if not email_messages:
            return 0

        try:
            endpoint = self._endpoint()
        except ScalewayTEMError:
            if self.fail_silently:
                return 0
            raise

        new_conn_created = self.open()
        sent_count = 0
        try:
            for message in email_messages:
                try:
                    self._send_one(self._client, endpoint, message)
                except ScalewayTEMError:
                    if not self.fail_silently:
                        raise
                else:
                    sent_count += 1
        finally:
            if new_conn_created:
                self.close()
        return sent_count
