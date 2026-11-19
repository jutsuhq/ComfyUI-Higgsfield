"""Minimal Higgsfield API client built on the standard library.

It covers what the nodes need: price estimates, uploads, submission with an
Idempotency-Key, status polling, cancellation and output download.
API reference: https://docs.higgsfield.ai
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://api.higgsfield.ai"
USER_AGENT = "ComfyUI-Higgsfield/0.1"
TERMINAL_FAILURES = {"failed", "nsfw", "canceled", "cancelled"}
KEY_FILE = Path(__file__).resolve().parent.parent / "higgsfield_key.txt"


class HiggsfieldError(RuntimeError):
    """An error the person can act on, with the API's own message when there is one."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def load_credentials(env: Mapping[str, str] | None = None, key_file: Path = KEY_FILE) -> str:
    """Return "KEY_ID:KEY_SECRET" from the environment or the key file.

    Order: HF_KEY, then HF_API_KEY + HF_API_SECRET (the names the official SDK
    reads), then higgsfield_key.txt next to this package. The key is never
    taken from a node widget, because widgets are saved into workflow files
    and into the metadata of every image and video ComfyUI writes.
    """
    if env is None:
        env = os.environ
    key = env.get("HF_KEY", "").strip()
    if not key and env.get("HF_API_KEY") and env.get("HF_API_SECRET"):
        key = f"{env['HF_API_KEY'].strip()}:{env['HF_API_SECRET'].strip()}"
    if not key and key_file.is_file():
        key = key_file.read_text(encoding="utf-8").strip()
    if ":" not in key:
        raise HiggsfieldError(
            "Higgsfield API key not found. Create one under API keys in https://console.higgsfield.ai, "
            "then set HF_KEY=KEY_ID:KEY_SECRET before starting ComfyUI, "
            f"or paste KEY_ID:KEY_SECRET into {key_file}."
        )
    return key


class HiggsfieldClient:
    def __init__(
        self,
        credentials: str,
        base_url: str | None = None,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60,
    ):
        self._auth = f"Key {credentials}"
        self.base_url = (base_url or os.environ.get("HF_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._open = opener
        self._sleep = sleep
        self._timeout = timeout

    # --- HTTP ---------------------------------------------------------------

    def _request(self, method: str, path: str, body=None, headers: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", self._auth)
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            req.add_header(name, value)
        try:
            with self._open(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            raise _api_error(err) from None
        return json.loads(raw) if raw else {}

    # --- API calls ----------------------------------------------------------

    def estimate(self, endpoint: str, body: dict) -> float:
        """Price in USD for this exact request. Nothing is charged."""
        answer = self._request("POST", f"/estimate/{endpoint}", body)
        try:
            return float(answer["usd"])
        except (KeyError, TypeError, ValueError):
            raise HiggsfieldError(f"Unexpected estimate answer: {answer}") from None

    def upload(self, data: bytes, content_type: str) -> str:
        """Upload a file and return its public URL. Higgsfield deletes it within 7 days."""
        slot = self._request("POST", "/files/generate-upload-url", {"content_type": content_type})
        req = urllib.request.Request(slot["upload_url"], data=data, method="PUT")
        # The presigned URL must not receive the API key.
        for name, value in (slot.get("upload_headers") or {"Content-Type": content_type}).items():
            req.add_header(name, value)
        try:
            with self._open(req, timeout=300) as resp:
                resp.read()
        except urllib.error.HTTPError as err:
            raise HiggsfieldError(f"Upload failed: HTTP {err.code}") from None
        return slot["public_url"]

    def submit(self, endpoint: str, body: dict, idempotency_key: str) -> str:
        """Start a generation and return its request_id.

        Retries on "slot busy" (429 or the 400 concurrency message) and on 5xx with
        the same Idempotency-Key, so a retry never pays twice.
        """
        delay = 5.0
        for attempt in range(8):
            try:
                answer = self._request("POST", f"/{endpoint}", body, {"Idempotency-Key": idempotency_key})
                return answer["request_id"]
            except HiggsfieldError as err:
                if not err.retryable or attempt == 7:
                    raise
                self._sleep(delay)
                delay = min(delay * 2, 60)
        raise AssertionError("unreachable")

    def status(self, request_id: str) -> dict:
        return self._request("GET", f"/requests/{request_id}/status")

    def cancel(self, request_id: str) -> bool:
        """Cancel a queued request. Returns False once processing has started."""
        try:
            self._request("POST", f"/requests/{request_id}/cancel")
            return True
        except HiggsfieldError:
            return False

    def wait(
        self,
        request_id: str,
        on_status: Callable[[str, float], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        timeout: float = 3600,
    ) -> dict:
        """Poll until the request finishes and return the completed answer."""
        started = time.monotonic()
        delay = 2.0
        while True:
            if should_stop and should_stop():
                cancelled = self.cancel(request_id)
                raise InterruptedError(
                    "Stopped. The queued request was canceled and is not charged."
                    if cancelled
                    else "Stopped watching. The request had already started on Higgsfield and may still be charged."
                )
            try:
                answer = self.status(request_id)
            except HiggsfieldError as err:
                if err.status in (401, 403, 404):
                    raise
                answer = {"status": "unknown"}
            except OSError:
                answer = {"status": "unknown"}
            state = str(answer.get("status", "unknown")).lower()
            elapsed = time.monotonic() - started
            if on_status:
                on_status(state, elapsed)
            if state == "completed":
                return answer
            if state in TERMINAL_FAILURES:
                reason = answer.get("error") or ""
                note = "Not charged." if state != "canceled" else ""
                raise HiggsfieldError(f"Higgsfield request {request_id} ended as {state}. {reason} {note}".strip())
            if elapsed > timeout:
                raise HiggsfieldError(f"Gave up after {int(timeout)} s. Request {request_id} may still finish.")
            self._sleep(delay * random.uniform(0.8, 1.2))
            delay = min(delay * 1.5, 10)

    def download(self, url: str, path: Path) -> Path:
        if urlparse(url).scheme != "https" and not url.startswith(self.base_url):
            raise HiggsfieldError(f"Refusing to download from a non-HTTPS URL: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with self._open(req, timeout=600) as resp, open(path, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        return path


def output_url(answer: dict) -> str:
    for kind in ("video", "image"):
        item = answer.get(kind)
        if isinstance(item, dict) and item.get("url"):
            return item["url"]
    images = answer.get("images")
    if isinstance(images, list) and images and images[0].get("url"):
        return images[0]["url"]
    raise HiggsfieldError(f"The request completed but returned no output URL: {answer}")


def _api_error(err: urllib.error.HTTPError) -> HiggsfieldError:
    try:
        payload = json.loads(err.read() or b"{}")
        detail = payload.get("detail") or payload.get("error") or payload
    except (ValueError, AttributeError):
        detail = ""
    if isinstance(detail, list):  # validation errors: [{loc, msg}, ...]
        detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail)
    text = str(detail)
    hints = {
        401: "Check the API key.",
        403: "The account balance may be empty. Add funds at https://console.higgsfield.ai.",
    }
    message = f"Higgsfield API error {err.code}: {text} {hints.get(err.code, '')}".strip()
    busy = err.code == 429 or (err.code == 400 and "concurrent requests" in text.lower())
    return HiggsfieldError(message, status=err.code, retryable=busy or err.code >= 500)
