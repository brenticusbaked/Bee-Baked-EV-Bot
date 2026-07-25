import unittest

import httpx

import db_manager


class _FakePostgrest:
    def __init__(self, session):
        self.session = session


class _FakeClient:
    def __init__(self, session):
        self.postgrest = _FakePostgrest(session)


class ForcePostgrestHttp1Tests(unittest.TestCase):
    def test_http2_session_is_replaced_with_http1(self):
        http2_session = httpx.Client(base_url="https://x.supabase.co/rest/v1/", http2=True)
        self.addCleanup(http2_session.close)
        self.assertTrue(http2_session._transport._pool._http2)

        client = _FakeClient(http2_session)
        db_manager._force_postgrest_http1(client)

        new_session = client.postgrest.session
        self.addCleanup(new_session.close)
        self.assertIsInstance(new_session, httpx.Client)
        self.assertFalse(new_session._transport._pool._http2)
        self.assertEqual(str(new_session.base_url), "https://x.supabase.co/rest/v1/")

    def test_non_httpx_session_is_left_untouched(self):
        sentinel = object()
        client = _FakeClient(sentinel)
        db_manager._force_postgrest_http1(client)
        self.assertIs(client.postgrest.session, sentinel)


if __name__ == "__main__":
    unittest.main()
