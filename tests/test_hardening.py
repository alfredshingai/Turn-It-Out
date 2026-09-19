"""Tests for hardening features in the anonymous model: rate limits,
proxy-aware client IPs, and stale scan recovery on documents."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, server


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeHandler:
    def __init__(self, client_ip: str, xff: str = ""):
        self.client_address = (client_ip, 12345)
        self.headers = FakeHeaders({"X-Forwarded-For": xff} if xff else {})


class TestClientIp(unittest.TestCase):
    def test_public_peer_ignores_xff(self):
        h = FakeHandler("93.184.216.34", xff="1.2.3.4")
        self.assertEqual(server._client_ip(h), "93.184.216.34")

    def test_private_proxy_honors_xff(self):
        h = FakeHandler("192.168.1.5", xff="93.184.216.34, 10.0.0.1")
        self.assertEqual(server._client_ip(h), "93.184.216.34")

    def test_localhost_counts_as_private(self):
        h = FakeHandler("127.0.0.1", xff="198.51.100.3")
        self.assertEqual(server._client_ip(h), "198.51.100.3")


class TestRateLimiter(unittest.TestCase):
    def test_allows_under_limit_blocks_over(self):
        key = ("test-ip", "POST", "/api/scan")
        server._rate_buckets.pop(key, None)
        limit, window = 3, 60
        for _ in range(limit):
            self.assertTrue(server._rate_limit(key, limit, window))
        self.assertFalse(server._rate_limit(key, limit, window))

    def test_old_entries_expire(self):
        key = ("test-ip2", "POST", "/api/scan")
        server._rate_buckets.pop(key, None)
        limit, window = 1, 60
        self.assertTrue(server._rate_limit(key, limit, window))
        self.assertFalse(server._rate_limit(key, limit, window))
        server._rate_buckets[key][0] -= window + 1
        self.assertTrue(server._rate_limit(key, limit, window))


class TestScanRecovery(unittest.TestCase):
    def setUp(self):
        self._old_path = db.DB_PATH
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(tmp)
        db.DB_PATH = tmp
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._old_path

    def test_stuck_scans_marked_failed(self):
        for status in ("processing", "queued", "done"):
            db.execute(
                """INSERT INTO documents
                   (token, text, char_count, word_count, status, created_at)
                   VALUES (?, 'x', 1, 1, ?, 0)""",
                (db.new_token(), status),
            )
        n = db.recover_stale_scans()
        rows = db.query("SELECT status FROM documents WHERE status = 'error'")
        self.assertEqual(len(rows), n)
        self.assertEqual(n, 2)  # only processing + queued

    def test_tokens_are_unguessable_and_unique(self):
        tokens = {db.new_token() for _ in range(1000)}
        self.assertEqual(len(tokens), 1000)
        self.assertTrue(all(len(t) >= 20 for t in tokens))


if __name__ == "__main__":
    unittest.main()
