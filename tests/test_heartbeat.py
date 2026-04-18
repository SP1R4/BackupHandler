"""Tests for the heartbeat (dead-man's-switch) module."""

from __future__ import annotations

from unittest import mock

from src.heartbeat import _validate_url, send_heartbeat


class TestValidateURL:
    def test_https_ok(self):
        assert _validate_url("https://hc-ping.com/abc") is None

    def test_http_ok(self):
        assert _validate_url("http://watchdog.internal/ping") is None

    def test_rejects_file_scheme(self):
        assert _validate_url("file:///etc/passwd") is not None

    def test_rejects_empty(self):
        assert _validate_url("") is not None

    def test_rejects_non_string(self):
        assert _validate_url(None) is not None  # type: ignore[arg-type]

    def test_rejects_missing_host(self):
        assert _validate_url("https://") is not None


class TestSendHeartbeat:
    def test_rejects_invalid_url_without_network(self, logger):
        with mock.patch("requests.get") as get:
            ok = send_heartbeat(logger, "file:///etc/passwd")
        assert ok is False
        get.assert_not_called()

    def test_success(self, logger):
        with mock.patch("requests.get") as get:
            get.return_value = mock.MagicMock(ok=True, status_code=200)
            ok = send_heartbeat(logger, "https://hc-ping.com/abc")
        assert ok is True

    def test_non_2xx_is_non_fatal(self, logger):
        with mock.patch("requests.get") as get:
            get.return_value = mock.MagicMock(ok=False, status_code=500)
            ok = send_heartbeat(logger, "https://hc-ping.com/abc")
        assert ok is False

    def test_network_error_is_non_fatal(self, logger):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("refused")):
            ok = send_heartbeat(logger, "https://hc-ping.com/abc")
        assert ok is False
