"""Tests for the webhook notification module (URL validation + delivery)."""

from __future__ import annotations

from unittest import mock

from src.webhook_notify import _validate_url, send_webhook


class TestValidateURL:
    def test_https_ok(self):
        assert _validate_url("https://hooks.example.com/x") is None

    def test_http_ok(self):
        assert _validate_url("http://internal/x") is None

    def test_rejects_file_scheme(self):
        err = _validate_url("file:///etc/passwd")
        assert err is not None and "scheme" in err

    def test_rejects_ftp_scheme(self):
        err = _validate_url("ftp://example.com/upload")
        assert err is not None and "scheme" in err

    def test_rejects_empty(self):
        assert _validate_url("") is not None

    def test_rejects_non_string(self):
        assert _validate_url(None) is not None  # type: ignore[arg-type]

    def test_rejects_missing_host(self):
        err = _validate_url("https://")
        assert err is not None


class TestSendWebhook:
    def test_rejects_invalid_url_without_calling_network(self, logger):
        with mock.patch("requests.post") as post:
            ok = send_webhook(logger, "file:///etc/passwd", "msg")
        assert ok is False
        post.assert_not_called()

    def test_success(self, logger):
        with mock.patch("requests.post") as post:
            post.return_value = mock.MagicMock(ok=True, status_code=200, text="ok")
            ok = send_webhook(logger, "https://hooks.example.com/x", "msg")
        assert ok is True

    def test_non_2xx(self, logger):
        with mock.patch("requests.post") as post:
            post.return_value = mock.MagicMock(ok=False, status_code=500, text="boom")
            ok = send_webhook(logger, "https://hooks.example.com/x", "msg")
        assert ok is False

    def test_network_error(self, logger):
        import requests

        with mock.patch("requests.post", side_effect=requests.ConnectionError("refused")):
            ok = send_webhook(logger, "https://hooks.example.com/x", "msg")
        assert ok is False
