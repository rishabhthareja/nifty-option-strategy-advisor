"""Configure TLS before any HTTPS clients (Gemini, LangSmith, httpx, etc.)."""

from __future__ import annotations

import os
import ssl


def _mozilla_ca_bundle() -> str | None:
    try:
        import certifi

        bundled = os.path.join(os.path.dirname(certifi.__file__), "cacert.pem")
        if os.path.isfile(bundled):
            return bundled
        where = certifi.where()
        return where if os.path.isfile(where) else None
    except ImportError:
        return None


def configure_ssl() -> None:
    """
    Fix Windows SSL: python-certifi-win32 merges system CAs that fail OpenSSL 3.x
    ('Basic Constraints of CA cert not marked critical').
    """
    skip = os.getenv("SSL_SKIP_VERIFY", "").lower() in ("1", "true", "yes")
    bundle = _mozilla_ca_bundle()

    if skip:
        ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]

        def _insecure_default_context(*_args, **_kwargs):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        ssl.create_default_context = _insecure_default_context  # type: ignore[assignment]
        return

    if not bundle:
        return

    os.environ["SSL_CERT_FILE"] = bundle
    os.environ["REQUESTS_CA_BUNDLE"] = bundle
    os.environ["CURL_CA_BUNDLE"] = bundle

    _orig_create_default_context = ssl.create_default_context

    def _create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        *,
        cafile=None,
        capath=None,
        cadata=None,
    ):
        if cafile is None and capath is None and cadata is None:
            cafile = bundle
        return _orig_create_default_context(
            purpose, cafile=cafile, capath=capath, cadata=cadata
        )

    ssl.create_default_context = _create_default_context  # type: ignore[assignment]

    def _verified_https_context():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(bundle)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    ssl._create_default_https_context = _verified_https_context  # type: ignore[assignment]
