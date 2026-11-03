"""Price-probe safety tests; no network calls."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prices


class Response:
    def __init__(self, status=200, body=b"{}", content_type="application/json"):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type}


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class ProbeTests(unittest.TestCase):
    def test_html_error_cannot_be_a_quote_and_bytes_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = b"<html>error</html>"
            client = Client([Response(body=raw, content_type="text/html")])
            result = prices.collect_prices(directory, [{"official_game_id": "g1"}], client)
            self.assertEqual(result["quotes"], [])
            self.assertEqual(result["excluded"][0]["reason"], "official_non_json_response")
            rec = result["requests"][0]
            self.assertEqual(rec["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(Path(rec["body_file"]).read_bytes(), raw)
            self.assertFalse(client.calls[0][1]["allow_redirects"])

    def test_unverified_json_never_accepts_fabricated_products(self):
        body = b'{"market":"full_game_3way","odds":[1.7,3.9,3.25]}'
        with tempfile.TemporaryDirectory() as directory:
            result = prices.collect_prices(directory, [{}], Client([Response(body=body)]))
            self.assertEqual(result["quotes"], [])
            self.assertEqual(result["excluded"][0]["reason"], "official_unverified_envelope")

    def test_rate_limit_retries_once_with_backoff(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(prices.time, "sleep") as sleep:
            client = Client([Response(429), Response(429)])
            result = prices.collect_prices(directory, [], client)
            self.assertEqual(len(result["requests"]), 2)
            self.assertEqual(result["excluded"][0]["reason"], "official_http_429")
            sleep.assert_called_once_with(15)

    def test_corrupt_throttle_stops_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "price-last-request.json").write_text('{}')
            client = Client([])
            result = prices.collect_prices(directory, [], client)
            self.assertEqual(result["excluded"][0]["reason"], "invalid_throttle_state")
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
