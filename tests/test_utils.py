"""Tests for small utilities in src.utils."""

from __future__ import annotations

import string

from src.utils import generate_otp, is_valid_email, should_exclude


class TestGenerateOTP:
    def test_default_length_is_16(self):
        otp = generate_otp()
        assert len(otp) == 16

    def test_custom_length(self):
        assert len(generate_otp(32)) == 32

    def test_uses_alphanumeric_alphabet(self):
        otp = generate_otp(64)
        alphabet = set(string.ascii_letters + string.digits)
        assert set(otp).issubset(alphabet)

    def test_not_predictable(self):
        samples = {generate_otp(24) for _ in range(50)}
        # CSPRNG output: collisions at this length are astronomically unlikely.
        assert len(samples) == 50


class TestIsValidEmail:
    def test_valid(self):
        assert is_valid_email("user@example.com") is True
        assert is_valid_email("user.name+tag@sub.example.co.uk") is True

    def test_invalid(self):
        assert is_valid_email("not-an-email") is False
        assert is_valid_email("@example.com") is False
        assert is_valid_email("user@") is False
        assert is_valid_email("") is False


class TestShouldExclude:
    def test_empty_patterns(self):
        assert should_exclude("file.log", []) is False
        assert should_exclude("file.log", None) is False

    def test_matches_filename(self):
        assert should_exclude("app.log", ["*.log"]) is True

    def test_matches_path(self):
        assert should_exclude("__pycache__/foo.pyc", ["__pycache__/*"]) is True

    def test_no_match(self):
        assert should_exclude("data.txt", ["*.log", "*.tmp"]) is False
