"""The opt-in route changes only the TCP destination, never HTTPS identity."""
import io
import ssl
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from benchmarks.bank_transport import BankConnectTransport


class Socket:
    def __init__(self, response):
        self.response = response
        self.sent = b""

    def setsockopt(self, *args):
        pass

    def sendall(self, value):
        self.sent += value

    def makefile(self, *args):
        return io.BytesIO(self.response)

    def close(self):
        pass


class BankTransportTests(unittest.TestCase):
    origin = "https://bank.example.test"
    address = "203.0.113.31"

    def response(self, status=200, extra=b""):
        return Socket(b"HTTP/1.1 " + str(status).encode() + b" Test\r\n"
                      + extra + b"Content-Length: 2\r\n\r\n{}")

    def test_real_http_stack_preserves_method_body_host_sni_and_ca_verification(self):
        route = BankConnectTransport(self.origin, self.address)
        sock = self.response()
        request = urllib.request.Request(self.origin + "/session?test=1", data=b'{"case":"TEST"}',
                                         headers={"Authorization": "Bearer TEST_ONLY", "Content-Type": "application/json"},
                                         method="POST")
        wrapping = []
        def wrap(context, socket, **kwargs):
            wrapping.append((context, socket, kwargs))
            return socket
        with patch("socket.create_connection", return_value=sock) as connect, \
                patch.object(ssl.SSLContext, "wrap_socket", autospec=True, side_effect=wrap):
            with route(request, timeout=7) as response:
                self.assertEqual((response.status, response.read()), (200, b"{}"))
        connect.assert_called_once_with((self.address, 443), 7, None)
        context, socket, kwargs = wrapping[0]
        self.assertEqual(kwargs["server_hostname"], "bank.example.test")
        self.assertIs(socket, sock)
        self.assertIs(context.check_hostname, True)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(sock.sent.startswith(b"POST /session?test=1 HTTP/1.1\r\n"))
        self.assertIn(b"Host: bank.example.test\r\n", sock.sent)
        self.assertIn(b"Authorization: Bearer TEST_ONLY\r\n", sock.sent)
        self.assertTrue(sock.sent.endswith(b'{"case":"TEST"}'))
        self.assertNotIn(self.address.encode(), sock.sent)
        self.assertEqual(request.full_url, self.origin + "/session?test=1")

    def test_other_origins_plain_http_userinfo_and_host_spoofs_fail_before_io(self):
        route = BankConnectTransport(self.origin, self.address)
        with patch.object(route._opener, "open") as opened:
            for url in ("https://other.example.test/x", "http://bank.example.test/x",
                        "https://bank.example.test:444/x", "https://user@bank.example.test/x",
                        "TEST_ONLY_NOT_A_URL"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    route(url, timeout=1)
            request = urllib.request.Request(self.origin, headers={"Host": "other.example.test"})
            with self.assertRaises(ValueError):
                route(request, timeout=1)
        opened.assert_not_called()

    def test_http_redirect_never_replays_request_or_changes_origin(self):
        route = BankConnectTransport(self.origin, self.address)
        sock = self.response(302, b"Location: https://other.example.test/steal\r\n")
        with patch("socket.create_connection", return_value=sock) as connect, \
                patch.object(ssl.SSLContext, "wrap_socket", autospec=True, side_effect=lambda context, socket, **kwargs: socket):
            with self.assertRaises(urllib.error.HTTPError) as error:
                route(urllib.request.Request(self.origin + "/ledger/TEST_ONLY"), timeout=2)
        self.assertEqual(error.exception.code, 302)
        self.assertEqual(connect.call_count, 1)

    def test_mutated_dispatch_or_proxy_fields_cannot_bypass_https_handler(self):
        route = BankConnectTransport(self.origin, self.address)
        for field, value in (("type", "http"), ("host", "other.example.test"),
                             ("selector", self.origin + "/steal"),
                             ("_tunnel_host", "proxy.example.test")):
            with self.subTest(field=field):
                request = urllib.request.Request(self.origin, headers={"Authorization": "Bearer TEST_ONLY"})
                setattr(request, field, value)
                with patch("socket.create_connection") as connect, self.assertRaises(ValueError):
                    route(request, timeout=1)
                connect.assert_not_called()

    def test_unredirected_host_cannot_hide_behind_regular_host(self):
        route = BankConnectTransport(self.origin, self.address)
        request = urllib.request.Request(self.origin, headers={"Host": "bank.example.test"})
        request.add_unredirected_header("Host", "other.example.test")
        self.assertEqual(dict(request.header_items())["Host"], "bank.example.test")
        with patch("socket.create_connection") as connect, self.assertRaises(ValueError):
            route(request, timeout=1)
        connect.assert_not_called()

    def test_host_must_be_authority_only_and_snapshot_preserves_effective_headers(self):
        route = BankConnectTransport(self.origin, self.address)
        for host in ("bank.example.test/altered", "bank.example.test?altered=1",
                     "bank.example.test#altered", "user@bank.example.test"):
            with self.subTest(host=host):
                request = urllib.request.Request(self.origin, headers={"Host": host})
                with patch("socket.create_connection") as connect, self.assertRaises(ValueError):
                    route(request, timeout=1)
                connect.assert_not_called()
        request = urllib.request.Request(self.origin, data=b"{}", method="PATCH",
                                         headers={"Authorization": "Bearer REGULAR_TEST_ONLY"})
        request.add_unredirected_header("Authorization", "Bearer EFFECTIVE_TEST_ONLY")
        sock = self.response()
        with patch("socket.create_connection", return_value=sock), \
                patch.object(ssl.SSLContext, "wrap_socket", autospec=True, side_effect=lambda context, socket, **kwargs: socket):
            with route(request, timeout=2) as result:
                result.read()
        self.assertIn(b"Authorization: Bearer EFFECTIVE_TEST_ONLY\r\n", sock.sent)
        self.assertNotIn(b"REGULAR_TEST_ONLY", sock.sent)
        self.assertTrue(sock.sent.startswith(b"PATCH / HTTP/1.1\r\n"))

    def test_proxy_environment_does_not_replace_explicit_connection(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.example.test:8888", "NO_PROXY": ""}):
            route = BankConnectTransport(self.origin, self.address)
            sock = self.response()
            with patch("socket.create_connection", return_value=sock) as connect, \
                    patch.object(ssl.SSLContext, "wrap_socket", autospec=True, side_effect=lambda context, socket, **kwargs: socket):
                with route(self.origin, timeout=3) as result:
                    result.read()
        connect.assert_called_once_with((self.address, 443), 3, None)
        self.assertNotIn(b"CONNECT ", sock.sent)

    def test_ipv6_and_explicit_original_port_are_supported(self):
        route = BankConnectTransport(self.origin + ":8443", "2001:db8::31")
        sock = self.response()
        with patch("socket.create_connection", return_value=sock) as connect, \
                patch.object(ssl.SSLContext, "wrap_socket", autospec=True, side_effect=lambda context, socket, **kwargs: socket):
            with route(self.origin + ":8443/ledger/TEST_ONLY", timeout=4) as result:
                result.read()
        connect.assert_called_once_with(("2001:db8::31", 8443), 4, None)
        self.assertIn(b"Host: bank.example.test:8443\r\n", sock.sent)

    def test_network_failure_is_propagated_without_retry(self):
        route = BankConnectTransport(self.origin, self.address)
        with patch("socket.create_connection", side_effect=TimeoutError) as connect:
            with self.assertRaises(urllib.error.URLError):
                route(self.origin, timeout=1)
        self.assertEqual(connect.call_count, 1)

    def test_invalid_addresses_origins_and_weakened_context_are_rejected(self):
        for address in (None, True, "bank.example.test", "203.0.113.31:443", "fe80::1%en0", ""):
            with self.subTest(address=address), self.assertRaises(ValueError):
                BankConnectTransport(self.origin, address)
        for origin in ("http://bank.example.test", "https://user:pass@bank.example.test",
                       "https://bank.example.test:0", "https://bank.example.test:99999", "not-a-url"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                BankConnectTransport(origin, self.address)
        context = ssl.create_default_context()
        context.check_hostname = False
        with patch("ssl.create_default_context", return_value=context), self.assertRaises(ValueError):
            BankConnectTransport(self.origin, self.address)

    def test_no_global_dns_or_opener_installation_and_configuration_is_explicit(self):
        with patch("urllib.request.install_opener") as installed, patch("socket.getaddrinfo") as dns:
            route = BankConnectTransport(self.origin, self.address)
        installed.assert_not_called()
        dns.assert_not_called()
        config = route.configuration()
        self.assertEqual((config["hostname"], config["connect_address"], config["port"]),
                         ("bank.example.test", self.address, 443))
        self.assertEqual((config["redirects"], config["proxies"], config["retries"]), (False, False, 0))
        self.assertTrue(config["certificate_verification"] and config["hostname_verification"])


if __name__ == "__main__":
    unittest.main()
