import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from higgsfield.client import HiggsfieldClient, HiggsfieldError, load_credentials, output_url  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps(payload).encode()))  # type: ignore[arg-type]


class Opener:
    """Answers requests from a list of (method, path suffix, response or exception)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        method, suffix, answer = self.script.pop(0)
        assert req.get_method() == method, (req.get_method(), method)
        assert req.full_url.endswith(suffix), (req.full_url, suffix)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer if isinstance(answer, bytes) else json.dumps(answer).encode())


def client(script):
    opener = Opener(script)
    return HiggsfieldClient("id:secret", base_url="https://api.test", opener=opener, sleep=lambda s: None), opener


class Credentials(unittest.TestCase):
    def test_order(self):
        missing = Path("/nonexistent/key.txt")
        self.assertEqual(load_credentials({"HF_KEY": " a:b "}, missing), "a:b")
        self.assertEqual(load_credentials({"HF_API_KEY": "a", "HF_API_SECRET": "b"}, missing), "a:b")
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "k.txt"
            key.write_text("c:d\n")
            self.assertEqual(load_credentials({}, key), "c:d")

    def test_missing_points_to_console(self):
        with self.assertRaisesRegex(HiggsfieldError, "console.higgsfield.ai"):
            load_credentials({}, Path("/nonexistent/key.txt"))


class Calls(unittest.TestCase):
    def test_estimate_sends_key(self):
        c, opener = client([("POST", "/estimate/m/x", {"usd": "1.25"})])
        self.assertEqual(c.estimate("m/x", {"a": 1}), 1.25)
        self.assertEqual(opener.calls[0].get_header("Authorization"), "Key id:secret")

    def test_upload_does_not_send_key_to_storage(self):
        slot = {"public_url": "https://cdn/p.png", "upload_url": "https://s3/put", "upload_headers": {"x-amz-tagging": "t"}}
        c, opener = client([("POST", "/files/generate-upload-url", slot), ("PUT", "/put", b"")])
        self.assertEqual(c.upload(b"png", "image/png"), "https://cdn/p.png")
        put = opener.calls[1]
        self.assertIsNone(put.get_header("Authorization"))
        self.assertEqual(put.get_header("X-amz-tagging"), "t")

    def test_submit_retries_busy_with_same_idempotency_key(self):
        busy = http_error(400, {"detail": "Maximum number of concurrent requests reached"})
        c, opener = client([("POST", "/m", busy), ("POST", "/m", http_error(429, {})), ("POST", "/m", {"request_id": "r1"})])
        self.assertEqual(c.submit("m", {}, "key-1"), "r1")
        self.assertEqual({r.get_header("Idempotency-key") for r in opener.calls}, {"key-1"})

    def test_submit_does_not_retry_validation_errors(self):
        bad = http_error(422, {"detail": [{"loc": ["body", "duration"], "msg": "too long"}]})
        c, _ = client([("POST", "/m", bad)])
        with self.assertRaisesRegex(HiggsfieldError, "duration: too long"):
            c.submit("m", {}, "k")

    def test_empty_balance_hint(self):
        c, _ = client([("POST", "/m", http_error(403, {"detail": "Not enough credits"}))])
        with self.assertRaisesRegex(HiggsfieldError, "balance"):
            c.submit("m", {}, "k")


class Wait(unittest.TestCase):
    def test_polls_until_completed(self):
        done = {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}
        c, _ = client([
            ("GET", "/requests/r/status", {"status": "queued"}),
            ("GET", "/requests/r/status", http_error(502, {})),
            ("GET", "/requests/r/status", {"status": "in_progress"}),
            ("GET", "/requests/r/status", done),
        ])
        seen = []
        answer = c.wait("r", on_status=lambda s, t: seen.append(s))
        self.assertEqual(seen, ["queued", "unknown", "in_progress", "completed"])
        self.assertEqual(output_url(answer), "https://cdn/v.mp4")

    def test_failure_is_not_charged(self):
        c, _ = client([("GET", "/requests/r/status", {"status": "nsfw"})])
        with self.assertRaisesRegex(HiggsfieldError, "nsfw.*Not charged"):
            c.wait("r")

    def test_interrupt_cancels_queued_request(self):
        c, opener = client([("POST", "/requests/r/cancel", {})])
        with self.assertRaisesRegex(InterruptedError, "canceled"):
            c.wait("r", should_stop=lambda: True)
        self.assertEqual(len(opener.calls), 1)

    def test_interrupt_after_start_warns_about_charge(self):
        c, _ = client([("POST", "/requests/r/cancel", http_error(400, {"detail": "already processing"}))])
        with self.assertRaisesRegex(InterruptedError, "may still be charged"):
            c.wait("r", should_stop=lambda: True)


class Download(unittest.TestCase):
    def test_refuses_plain_http(self):
        c, _ = client([])
        with self.assertRaises(HiggsfieldError):
            c.download("http://evil/v.mp4", Path("/tmp/x"))

    def test_writes_file(self):
        c, _ = client([("GET", "/v.mp4", b"mp4data")])
        with tempfile.TemporaryDirectory() as tmp:
            path = c.download("https://cdn/v.mp4", Path(tmp) / "v.mp4")
            self.assertEqual(path.read_bytes(), b"mp4data")


if __name__ == "__main__":
    unittest.main()
