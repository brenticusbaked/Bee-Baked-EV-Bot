import unittest
from unittest import mock

import requests

import services.http_client as http_client


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class ResidentialProxyTests(unittest.TestCase):
    def test_returns_none_without_config(self):
        with mock.patch.object(http_client, "_PROXY_IPS", []):
            self.assertIsNone(http_client._residential_proxies())

    def test_builds_rotating_session_proxy(self):
        with mock.patch.object(http_client, "_PROXY_IPS", ["1.2.3.4:8080"]), \
             mock.patch.object(http_client, "_PROXY_USERNAME", "user"), \
             mock.patch.object(http_client, "_PROXY_PASSWORD", "pass"):
            proxies = http_client._residential_proxies()
        self.assertIsNotNone(proxies)
        self.assertEqual(proxies["http"], proxies["https"])
        self.assertIn("user-session-", proxies["http"])
        self.assertIn("@1.2.3.4:8080", proxies["http"])

    def test_url_encodes_credentials_with_special_chars(self):
        with mock.patch.object(http_client, "_PROXY_IPS", ["1.2.3.4:8080"]), \
             mock.patch.object(http_client, "_PROXY_USERNAME", "us@r"), \
             mock.patch.object(http_client, "_PROXY_PASSWORD", "p@ss:w0rd/#"):
            proxies = http_client._residential_proxies()
        # Raw special chars would break the proxy URL and cause a 407; they must
        # be percent-encoded in the userinfo, and only one '@' separates userinfo
        # from host.
        self.assertIsNotNone(proxies)
        self.assertNotIn("p@ss", proxies["http"])
        self.assertIn("p%40ss%3Aw0rd%2F%23", proxies["http"])
        self.assertEqual(proxies["http"].count("@"), 1)
        self.assertTrue(proxies["http"].endswith("@1.2.3.4:8080"))


class MlbRequestTests(unittest.TestCase):
    def test_retries_on_406_then_succeeds(self):
        session = mock.Mock()
        session.request.side_effect = [_FakeResponse(406), _FakeResponse(200)]
        with mock.patch.object(
            http_client, "_residential_proxies",
            side_effect=[{"http": "p1", "https": "p1"}, {"http": "p2", "https": "p2"}],
        ):
            resp = http_client._mlb_request(session, "GET", "https://statsapi.mlb.com/x")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.request.call_count, 2)

    def test_no_proxy_falls_back_to_direct(self):
        session = mock.Mock()
        session.request.return_value = _FakeResponse(200)
        with mock.patch.object(http_client, "_residential_proxies", return_value=None):
            resp = http_client._mlb_request(session, "GET", "https://statsapi.mlb.com/x")
        self.assertEqual(resp.status_code, 200)
        _, kwargs = session.request.call_args
        self.assertNotIn("proxies", kwargs)

    def test_raises_after_exhausting_retries(self):
        session = mock.Mock()
        session.request.return_value = _FakeResponse(406)
        with mock.patch.object(
            http_client, "_residential_proxies", return_value={"http": "p", "https": "p"}
        ):
            with self.assertRaises(requests.HTTPError):
                http_client._mlb_request(session, "GET", "https://statsapi.mlb.com/x")
        self.assertEqual(session.request.call_count, http_client._MLB_PROXY_MAX_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
