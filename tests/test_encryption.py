"""Tests for AES-256-GCM encryption at rest."""

from __future__ import annotations

import os

import pytest

from src.encryption import (
    decrypt_directory,
    decrypt_file,
    derive_key,
    encrypt_directory,
    encrypt_file,
    load_key_file,
)


class TestEncryption:
    def test_encrypt_decrypt_file_passphrase(self, tmp_dir):
        test_file = tmp_dir / "test.txt"
        test_file.write_text("hello encryption")
        passphrase = "test_passphrase_123"

        enc_path = encrypt_file(test_file, passphrase=passphrase)
        assert enc_path.suffix == ".enc"
        assert enc_path.exists()
        assert not test_file.exists()

        dec_path = decrypt_file(enc_path, passphrase=passphrase)
        assert dec_path.read_text() == "hello encryption"
        assert not enc_path.exists()

    def test_encrypt_decrypt_file_keyfile(self, tmp_dir):
        test_file = tmp_dir / "test.txt"
        test_file.write_bytes(b"keyfile test data")
        key_file = tmp_dir / "keyfile.bin"
        key_file.write_bytes(os.urandom(32))

        enc_path = encrypt_file(test_file, key_file=str(key_file))
        assert enc_path.exists()

        dec_path = decrypt_file(enc_path, key_file=str(key_file))
        assert dec_path.read_bytes() == b"keyfile test data"

    def test_encrypt_directory_skips_manifests(self, tmp_dir, logger):
        (tmp_dir / "data.txt").write_text("data")
        (tmp_dir / "backup_manifest_20260101_120000.json").write_text("{}")

        count = encrypt_directory(tmp_dir, passphrase="pass", logger=logger)
        assert count == 1
        assert (tmp_dir / "data.txt.enc").exists()
        assert (tmp_dir / "backup_manifest_20260101_120000.json").exists()

    def test_encrypt_directory_skips_enc_files(self, tmp_dir, logger):
        (tmp_dir / "already.enc").write_bytes(b"encrypted")
        (tmp_dir / "new.txt").write_text("new")

        count = encrypt_directory(tmp_dir, passphrase="pass", logger=logger)
        assert count == 1

    def test_decrypt_directory(self, tmp_dir, logger):
        (tmp_dir / "a.txt").write_text("aaa")
        (tmp_dir / "b.txt").write_text("bbb")
        encrypt_directory(tmp_dir, passphrase="pass", logger=logger)

        assert (tmp_dir / "a.txt.enc").exists()
        assert (tmp_dir / "b.txt.enc").exists()

        decrypt_directory(tmp_dir, passphrase="pass", logger=logger)
        assert (tmp_dir / "a.txt").read_text() == "aaa"
        assert (tmp_dir / "b.txt").read_text() == "bbb"

    def test_derive_key_deterministic(self):
        salt = b"\x00" * 16
        key1 = derive_key("passphrase", salt)
        key2 = derive_key("passphrase", salt)
        assert key1 == key2
        assert len(key1) == 32

    def test_load_key_file_wrong_size(self, tmp_dir):
        kf = tmp_dir / "bad.key"
        kf.write_bytes(b"\x00" * 16)
        with pytest.raises(ValueError, match="32 bytes"):
            load_key_file(str(kf))

    def test_wrong_passphrase_fails(self, tmp_dir):
        test_file = tmp_dir / "test.txt"
        test_file.write_text("secret data")
        enc_path = encrypt_file(test_file, passphrase="correct")

        from cryptography.exceptions import InvalidTag

        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="wrong")
