"""Tests for AES-256-GCM encryption at rest."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backup_handler import encryption
from backup_handler.encryption import (
    KDF_PBKDF2,
    MAGIC,
    MAGIC_V2,
    NONCE_SIZE,
    SALT_SIZE,
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

    def test_v2_header_present(self, tmp_dir):
        f = tmp_dir / "x.txt"
        f.write_text("payload")
        enc_path = encrypt_file(f, passphrase="pp")
        data = enc_path.read_bytes()
        assert data[:4] == MAGIC_V2
        assert data[4] == KDF_PBKDF2

    def test_v2_downgrade_attack_fails(self, tmp_dir):
        """Flipping the KDF byte to another passphrase KDF must invalidate the AEAD tag."""
        f = tmp_dir / "x.txt"
        f.write_text("payload")
        enc_path = encrypt_file(f, passphrase="pp")  # pbkdf2 = 0x01
        data = bytearray(enc_path.read_bytes())
        data[4] = 0x02  # claim Argon2id; AAD will mismatch the original
        enc_path.write_bytes(bytes(data))

        from cryptography.exceptions import InvalidTag

        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="pp")

    def test_v2_magic_corruption_fails(self, tmp_dir):
        """Corrupting the magic prefix routes to legacy parsing and fails to decrypt."""
        f = tmp_dir / "x.txt"
        f.write_text("payload")
        enc_path = encrypt_file(f, passphrase="pp")
        data = bytearray(enc_path.read_bytes())
        data[0] = ord("X")  # break magic
        enc_path.write_bytes(bytes(data))

        from cryptography.exceptions import InvalidTag

        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="pp")

    def test_v1_format_still_decrypts(self, tmp_dir):
        """v1 files (whole-file AES-GCM, no longer written) must still decrypt."""
        plaintext = b"v1 era payload"
        passphrase = "v1_pp"
        salt = os.urandom(SALT_SIZE)
        nonce = os.urandom(NONCE_SIZE)
        key = derive_key(passphrase, salt, kdf_id=KDF_PBKDF2)
        header_prefix = MAGIC + bytes([KDF_PBKDF2])
        ct = AESGCM(key).encrypt(nonce, plaintext, header_prefix)

        enc_path = tmp_dir / "v1file.txt.enc"
        enc_path.write_bytes(header_prefix + salt + nonce + ct)

        dec_path = decrypt_file(enc_path, passphrase=passphrase)
        assert dec_path.read_bytes() == plaintext


class TestStreamingV2:
    def test_multi_chunk_roundtrip(self, tmp_dir, monkeypatch):
        """Files spanning many chunks roundtrip byte-identically."""
        monkeypatch.setattr(encryption, "AES_CHUNK_SIZE", 1024)
        payload = os.urandom(5 * 1024 + 137)  # 6 chunks, last one partial
        f = tmp_dir / "big.bin"
        f.write_bytes(payload)

        enc_path = encrypt_file(f, passphrase="pp")
        # header + 6 chunks each carrying a 16B tag
        assert enc_path.stat().st_size == 33 + len(payload) + 6 * 16

        dec_path = decrypt_file(enc_path, passphrase="pp")
        assert dec_path.read_bytes() == payload

    def test_chunk_boundary_roundtrip(self, tmp_dir, monkeypatch):
        """A payload of exactly N chunks (no partial tail) roundtrips."""
        monkeypatch.setattr(encryption, "AES_CHUNK_SIZE", 1024)
        payload = os.urandom(3 * 1024)
        f = tmp_dir / "exact.bin"
        f.write_bytes(payload)
        enc_path = encrypt_file(f, passphrase="pp")
        dec_path = decrypt_file(enc_path, passphrase="pp")
        assert dec_path.read_bytes() == payload

    def test_empty_file_roundtrip(self, tmp_dir):
        f = tmp_dir / "empty.txt"
        f.write_bytes(b"")
        enc_path = encrypt_file(f, passphrase="pp")
        dec_path = decrypt_file(enc_path, passphrase="pp")
        assert dec_path.read_bytes() == b""

    def test_truncation_detected(self, tmp_dir, monkeypatch):
        """Stripping the final chunk must fail — the last remaining chunk
        lacks the final-flag bit in its nonce."""
        from cryptography.exceptions import InvalidTag

        monkeypatch.setattr(encryption, "AES_CHUNK_SIZE", 1024)
        f = tmp_dir / "t.bin"
        f.write_bytes(os.urandom(3 * 1024))
        enc_path = encrypt_file(f, passphrase="pp")

        data = enc_path.read_bytes()
        enc_path.write_bytes(data[: -(1024 + 16)])  # drop the last sealed chunk
        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="pp")

    def test_chunk_reorder_detected(self, tmp_dir, monkeypatch):
        """Swapping two sealed chunks must fail — the counter is in the nonce."""
        from cryptography.exceptions import InvalidTag

        monkeypatch.setattr(encryption, "AES_CHUNK_SIZE", 1024)
        f = tmp_dir / "r.bin"
        f.write_bytes(os.urandom(3 * 1024))
        enc_path = encrypt_file(f, passphrase="pp")

        data = bytearray(enc_path.read_bytes())
        header, cs = 33, 1024 + 16
        chunk0 = bytes(data[header : header + cs])
        chunk1 = bytes(data[header + cs : header + 2 * cs])
        data[header : header + cs] = chunk1
        data[header + cs : header + 2 * cs] = chunk0
        enc_path.write_bytes(bytes(data))
        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="pp")

    def test_bitflip_detected(self, tmp_dir):
        from cryptography.exceptions import InvalidTag

        f = tmp_dir / "b.txt"
        f.write_text("payload")
        enc_path = encrypt_file(f, passphrase="pp")
        data = bytearray(enc_path.read_bytes())
        data[-1] ^= 0xFF
        enc_path.write_bytes(bytes(data))
        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="pp")

    def test_failed_decrypt_leaves_no_output(self, tmp_dir):
        f = tmp_dir / "x.txt"
        f.write_text("payload")
        enc_path = encrypt_file(f, passphrase="pp")

        from cryptography.exceptions import InvalidTag

        with pytest.raises(InvalidTag):
            decrypt_file(enc_path, passphrase="wrong")
        assert not (tmp_dir / "x.txt").exists()
        assert list(tmp_dir.glob("*.tmp")) == []

    def test_legacy_format_decrypts(self, tmp_dir):
        """Pre-versioning .enc files (no magic, salt+nonce+ct only) must still decrypt."""
        plaintext = b"legacy payload"
        passphrase = "legacy_pp"
        salt = os.urandom(SALT_SIZE)
        nonce = os.urandom(NONCE_SIZE)
        key = derive_key(passphrase, salt, kdf_id=KDF_PBKDF2)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)  # legacy: AAD=None

        enc_path = tmp_dir / "legacy.txt.enc"
        enc_path.write_bytes(salt + nonce + ct)

        dec_path = decrypt_file(enc_path, passphrase=passphrase)
        assert dec_path.read_bytes() == plaintext

    def test_atomic_write_no_tmp_left_on_success(self, tmp_dir):
        f = tmp_dir / "x.txt"
        f.write_text("payload")
        enc_path = encrypt_file(f, passphrase="pp")
        leftover = list(tmp_dir.glob("*.tmp"))
        assert leftover == []
        assert enc_path.exists()
