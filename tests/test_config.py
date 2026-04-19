"""Tests for config loading, env-var resolution, and normalisation."""

from __future__ import annotations

import os

import pytest

from src.config import _resolve_all_env_vars, normalize_none, resolve_env_vars


class TestEnvVarResolution:
    def test_resolve_single_var(self):
        os.environ["TEST_BH_VAR"] = "secret123"
        try:
            assert resolve_env_vars("${TEST_BH_VAR}") == "secret123"
        finally:
            del os.environ["TEST_BH_VAR"]

    def test_resolve_multiple_vars(self):
        os.environ["TEST_BH_A"] = "hello"
        os.environ["TEST_BH_B"] = "world"
        try:
            result = resolve_env_vars("${TEST_BH_A}-${TEST_BH_B}")
            assert result == "hello-world"
        finally:
            del os.environ["TEST_BH_A"]
            del os.environ["TEST_BH_B"]

    def test_unset_var_raises(self):
        with pytest.raises(ValueError, match="UNSET_VAR_12345"):
            resolve_env_vars("${UNSET_VAR_12345}")

    def test_no_vars_passthrough(self):
        assert resolve_env_vars("plain_value") == "plain_value"

    def test_empty_string(self):
        assert resolve_env_vars("") == ""

    def test_resolve_all_env_vars(self, logger):
        import configparser

        config = configparser.ConfigParser()
        config.read_string("""
[DEFAULT]
source_dir = /tmp/test

[SSH]
password = ${TEST_BH_PASS}
""")
        os.environ["TEST_BH_PASS"] = "my_secret"
        try:
            _resolve_all_env_vars(config, logger)
            assert config.get("SSH", "password") == "my_secret"
        finally:
            del os.environ["TEST_BH_PASS"]


class TestNormalizeNone:
    def test_none_value(self):
        assert normalize_none(None) is None

    def test_none_string(self):
        assert normalize_none("None") is None
        assert normalize_none("none") is None

    def test_empty_string(self):
        assert normalize_none("") is None
        assert normalize_none("   ") is None

    def test_valid_value(self):
        assert normalize_none("hello") == "hello"
        assert normalize_none("  hello  ") == "hello"
