"""Optional bank-only connection routing; URL, Host and TLS identity stay intact."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import urllib.request


def _origin(url: str) -> tuple[str, int]:
    if not isinstance(url, str):
        raise ValueError("bank transport requires a valid HTTPS origin")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = 443 if parsed.port is None else parsed.port
    except (ValueError, AttributeError):
        raise ValueError("bank transport requires a valid HTTPS origin") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or not 1 <= port <= 65535):
        raise ValueError("bank transport requires a valid HTTPS origin")
    return parsed.hostname.lower(), port


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class BankConnectTransport:
    """Connect one HTTPS origin to an operator-supplied IP without changing it.

    No address is a source default. This callable is compatible with the hosted
    backend's opener injection. It does not install a global opener, change DNS,
    retry, follow redirects or use proxy environment settings.
    """
    def __init__(self, bank_origin: str, connect_address: str):
        self.origin = _origin(bank_origin)
        try:
            if not isinstance(connect_address, str) or "%" in connect_address:
                raise ValueError
            self.connect_address = str(ipaddress.ip_address(connect_address))
        except ValueError:
            raise ValueError("bank connect address must be a literal IP address") from None
        context = ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
            raise ValueError("bank transport requires certificate and hostname validation")
        expected = self.origin
        address = self.connect_address

        class Connection(http.client.HTTPSConnection):
            def __init__(self, host, **kwargs):
                super().__init__(host, **kwargs)
                if (self.host.lower(), self.port) != expected:
                    raise ValueError("bank transport cannot connect to another origin")
                original_connect = self._create_connection

                def connect(target, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
                    if target != (self.host, self.port) or self._tunnel_host is not None:
                        raise ValueError("bank transport cannot tunnel or replace the TLS identity")
                    return original_connect((address, self.port), timeout, source_address)

                self._create_connection = connect

        class Handler(urllib.request.HTTPSHandler):
            def https_open(self, request):
                if _origin(request.full_url) != expected:
                    raise ValueError("bank transport cannot send to another origin")
                return self.do_open(Connection, request, context=context)

        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(), Handler(context=context))

    def __call__(self, request, *, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        if isinstance(request, str):
            if _origin(request) != self.origin:
                raise ValueError("bank transport cannot send to another origin")
            request = urllib.request.Request(request)
        if not isinstance(request, urllib.request.Request):
            raise ValueError("bank transport requires a URL or HTTP request")
        url = request.full_url
        if _origin(url) != self.origin:
            raise ValueError("bank transport cannot send to another origin")
        # Dispatch uses mutable Request fields, not full_url alone. Snapshot the
        # request into a fresh URL-derived object before urllib selects a handler.
        safe = urllib.request.Request(url, data=request.data, method=request.get_method())
        if (request.type != safe.type or request.host != safe.host
                or request.selector != safe.selector or request._tunnel_host is not None):
            raise ValueError("bank transport cannot replace HTTP dispatch or proxy state")
        regular, unredirected = dict(request.headers), dict(request.unredirected_hdrs)
        # do_open gives unredirected headers precedence; header_items alone can
        # hide a conflicting Host. Check both snapshots before transmitting.
        for name, value in (*regular.items(), *unredirected.items()):
            if name.lower() == "host":
                if not isinstance(value, str):
                    raise ValueError("bank transport requires a literal Host authority")
                parsed = urllib.parse.urlsplit("https://" + value)
                if (parsed.netloc != value or parsed.path or parsed.query or parsed.fragment
                        or _origin("https://" + value) != self.origin):
                    raise ValueError("bank transport cannot replace the HTTP Host")
        safe.headers, safe.unredirected_hdrs = regular, unredirected
        return self._opener.open(safe, timeout=timeout)

    def configuration(self) -> dict:
        return {"mode": "explicit_connect_address", "hostname": self.origin[0],
                "port": self.origin[1], "connect_address": self.connect_address,
                "certificate_verification": True, "hostname_verification": True,
                "redirects": False, "proxies": False, "retries": 0}
