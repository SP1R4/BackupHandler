"""Tests for the Qsafe post-quantum encryption backend."""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess

import pytest

from backup_handler import qsafe_backend
from backup_handler.config import validate_config
from backup_handler.encryption import decrypt_directory, decrypt_file, encrypt_directory, encrypt_file
from backup_handler.manifest import manifest_signature_status

QSAFE_CLI = shutil.which("qsafe")
TEST_PASSPHRASE = "qsafe-test-passphrase"


class TestParseRecipients:
    def test_none(self):
        assert qsafe_backend.parse_recipients(None) == []

    def test_empty_string(self):
        assert qsafe_backend.parse_recipients("") == []

    def test_single(self):
        assert qsafe_backend.parse_recipients("/keys/ops.pub") == ["/keys/ops.pub"]

    def test_comma_separated_with_whitespace(self):
        raw = "/keys/ops.pub, /keys/escrow.pub ,,"
        assert qsafe_backend.parse_recipients(raw) == ["/keys/ops.pub", "/keys/escrow.pub"]

    def test_list_passthrough(self):
        assert qsafe_backend.parse_recipients(["a.pub", " b.pub "]) == ["a.pub", "b.pub"]


class TestMagicDetection:
    def test_qsafe_magic(self):
        assert qsafe_backend.is_qsafe_data(b"QSAFE006" + b"\x00" * 16)

    def test_aes_magic_is_not_qsafe(self):
        assert not qsafe_backend.is_qsafe_data(b"BHE1\x01" + b"\x00" * 16)

    def test_short_data(self):
        assert not qsafe_backend.is_qsafe_data(b"QS")


class TestBackendValidation:
    def test_encrypt_file_unknown_backend(self, tmp_dir):
        f = tmp_dir / "x.txt"
        f.write_text("data")
        with pytest.raises(ValueError, match="Unknown encryption backend"):
            encrypt_file(f, backend="rot13")

    def test_encrypt_directory_qsafe_requires_recipients(self, tmp_dir, logger):
        (tmp_dir / "x.txt").write_text("data")
        with pytest.raises(ValueError, match="recipient"):
            encrypt_directory(tmp_dir, backend="qsafe", logger=logger)

    def test_encrypt_missing_recipient_key(self, tmp_dir):
        f = tmp_dir / "x.txt"
        f.write_text("data")
        with pytest.raises(FileNotFoundError):
            qsafe_backend.encrypt_file_to(f, tmp_dir / "x.enc", [str(tmp_dir / "missing.pub")])

    def test_decrypt_qsafe_file_without_secret_key(self, tmp_dir):
        enc = tmp_dir / "x.txt.enc"
        enc.write_bytes(b"QSAFE006" + os.urandom(64))
        with pytest.raises(ValueError, match="qsafe_secret_key"):
            decrypt_file(enc, passphrase="irrelevant")
        assert enc.exists()  # nothing consumed on failure

    def test_decrypt_missing_secret_key_file(self, tmp_dir):
        enc = tmp_dir / "x.txt.enc"
        enc.write_bytes(b"QSAFE006" + os.urandom(64))
        with pytest.raises(FileNotFoundError):
            decrypt_file(enc, passphrase="x", qsafe_secret_key=str(tmp_dir / "missing.key"))

    def test_encrypt_directory_skips_manifest_signatures(self, tmp_dir, logger):
        (tmp_dir / "data.txt").write_text("data")
        (tmp_dir / "backup_manifest_20260101_120000.json").write_text("{}")
        (tmp_dir / "backup_manifest_20260101_120000.json.sig").write_bytes(os.urandom(32))

        count = encrypt_directory(tmp_dir, passphrase="pass", logger=logger)
        assert count == 1
        assert (tmp_dir / "data.txt.enc").exists()
        assert (tmp_dir / "backup_manifest_20260101_120000.json").exists()
        assert (tmp_dir / "backup_manifest_20260101_120000.json.sig").exists()

    def test_manifest_signature_status_missing(self, tmp_dir):
        manifest = tmp_dir / "backup_manifest_20260101_120000.json"
        manifest.write_text("{}")
        pub = tmp_dir / "sign.pub"
        pub.write_bytes(b"irrelevant")
        assert manifest_signature_status(manifest, str(pub)) == "missing"


class TestQsafeReadiness:
    def _values(self, **overrides):
        values = {
            "encryption_enabled": True,
            "encryption_backend": "qsafe",
            "encryption_qsafe_recipients": None,
            "encryption_qsafe_sign_key": None,
        }
        values.update(overrides)
        return values

    def test_not_configured_is_ready(self):
        from backup_handler.orchestrator import _check_qsafe_readiness

        assert _check_qsafe_readiness({"encryption_enabled": False}) is None

    def test_missing_recipients(self):
        from backup_handler.orchestrator import _check_qsafe_readiness

        error = _check_qsafe_readiness(self._values())
        assert error and "qsafe_recipients" in error

    def test_missing_recipient_key_file(self, tmp_dir):
        from backup_handler.orchestrator import _check_qsafe_readiness

        error = _check_qsafe_readiness(self._values(encryption_qsafe_recipients=str(tmp_dir / "nope.pub")))
        assert error and "not found" in error

    def test_missing_sign_key_file(self, tmp_dir):
        from backup_handler.orchestrator import _check_qsafe_readiness

        error = _check_qsafe_readiness(
            {
                "encryption_enabled": False,
                "encryption_qsafe_sign_key": str(tmp_dir / "nope.key"),
            }
        )
        assert error and "signing key not found" in error

    def test_unavailable_engine(self, monkeypatch, tmp_dir):
        from backup_handler.orchestrator import _check_qsafe_readiness

        monkeypatch.setattr(qsafe_backend, "is_available", lambda: False)
        error = _check_qsafe_readiness(self._values())
        assert error and "available" in error

    def test_ready(self, tmp_dir):
        from backup_handler.orchestrator import _check_qsafe_readiness

        pub = tmp_dir / "ops.pub"
        pub.write_bytes(b"key material")
        if not qsafe_backend.is_available():
            pytest.skip("no qsafe engine on this machine")
        assert _check_qsafe_readiness(self._values(encryption_qsafe_recipients=str(pub))) is None


class TestConfigValidation:
    def _config(self, encryption: dict) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config["DEFAULT"] = {"source_dir": "/data/src", "mode": "full"}
        config["BACKUPS"] = {"backup_dirs": "/data/dst"}
        config["ENCRYPTION"] = {"enabled": "True", **encryption}
        return config

    def test_qsafe_without_recipients_rejected(self, logger):
        config = self._config({"backend": "qsafe"})
        with pytest.raises(SystemExit):
            validate_config(logger, config)

    def test_qsafe_with_recipients_accepted(self, logger):
        config = self._config({"backend": "qsafe", "qsafe_recipients": "/keys/ops.pub"})
        validate_config(logger, config)

    def test_unknown_backend_rejected(self, logger):
        config = self._config({"backend": "rot13", "passphrase": "x"})
        with pytest.raises(SystemExit):
            validate_config(logger, config)

    def test_aes_still_requires_credentials(self, logger):
        config = self._config({"backend": "aes"})
        with pytest.raises(SystemExit):
            validate_config(logger, config)


@pytest.mark.skipif(QSAFE_CLI is None, reason="qsafe CLI not installed")
class TestQsafeRoundtrip:
    @pytest.fixture
    def keypair(self, tmp_dir):
        """Generate a real Qsafe keypair once per test."""
        sk = tmp_dir / "keys" / "backup.key"
        pk = tmp_dir / "keys" / "backup.pub"
        sk.parent.mkdir()
        env = os.environ.copy()
        env["QSAFE_PASSPHRASE"] = TEST_PASSPHRASE
        subprocess.run(
            [QSAFE_CLI, "keygen", "--key-file", str(sk), "--pub-file", str(pk), "--scrypt-cost", "14"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )
        return sk, pk

    @pytest.fixture
    def sign_keypair(self, tmp_dir):
        """Generate a real ML-DSA-87 signing keypair once per test."""
        sk = tmp_dir / "keys" / "sign.key"
        pk = tmp_dir / "keys" / "sign.pub"
        sk.parent.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["QSAFE_PASSPHRASE"] = TEST_PASSPHRASE
        subprocess.run(
            [
                QSAFE_CLI,
                "sign-keygen",
                "--key-file",
                str(sk),
                "--pub-file",
                str(pk),
                "--scrypt-cost",
                "14",
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )
        return sk, pk

    def test_encrypt_decrypt_file(self, tmp_dir, keypair):
        sk, pk = keypair
        f = tmp_dir / "test.txt"
        f.write_text("post-quantum backup data")

        enc_path = encrypt_file(f, backend="qsafe", qsafe_recipients=[str(pk)])
        assert enc_path.suffix == ".enc"
        assert enc_path.exists()
        assert not f.exists()
        assert qsafe_backend.is_qsafe_data(enc_path.read_bytes()[:8])

        dec_path = decrypt_file(enc_path, passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk))
        assert dec_path.read_text() == "post-quantum backup data"
        assert not enc_path.exists()

    def test_directory_roundtrip_skips_manifests(self, tmp_dir, keypair, logger):
        sk, pk = keypair
        data_dir = tmp_dir / "backup"
        data_dir.mkdir()
        (data_dir / "a.txt").write_text("alpha")
        (data_dir / "sub").mkdir()
        (data_dir / "sub" / "b.bin").write_bytes(os.urandom(1024))
        original = (data_dir / "sub" / "b.bin").read_bytes()
        (data_dir / "backup_manifest_20260101_120000.json").write_text("{}")

        count = encrypt_directory(data_dir, backend="qsafe", qsafe_recipients=[str(pk)], logger=logger)
        assert count == 2
        assert (data_dir / "a.txt.enc").exists()
        assert (data_dir / "backup_manifest_20260101_120000.json").exists()

        count = decrypt_directory(
            data_dir, passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk), logger=logger
        )
        assert count == 2
        assert (data_dir / "a.txt").read_text() == "alpha"
        assert (data_dir / "sub" / "b.bin").read_bytes() == original

    def test_multi_recipient_any_key_decrypts(self, tmp_dir, keypair):
        _sk_ops, pk_ops = keypair
        sk_escrow = tmp_dir / "keys" / "escrow.key"
        pk_escrow = tmp_dir / "keys" / "escrow.pub"
        env = os.environ.copy()
        env["QSAFE_PASSPHRASE"] = TEST_PASSPHRASE
        subprocess.run(
            [
                QSAFE_CLI,
                "keygen",
                "--key-file",
                str(sk_escrow),
                "--pub-file",
                str(pk_escrow),
                "--scrypt-cost",
                "14",
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )

        f = tmp_dir / "shared.txt"
        f.write_text("escrowed")
        enc_path = encrypt_file(f, backend="qsafe", qsafe_recipients=[str(pk_ops), str(pk_escrow)])

        # The escrow key alone must decrypt
        dec_path = decrypt_file(enc_path, passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk_escrow))
        assert dec_path.read_text() == "escrowed"

    def test_mixed_aes_and_qsafe_tree(self, tmp_dir, keypair, logger):
        """A tree with both AES and Qsafe .enc files decrypts in one pass."""
        sk, pk = keypair
        data_dir = tmp_dir / "mixed"
        data_dir.mkdir()
        (data_dir / "old.txt").write_text("aes era")
        (data_dir / "new.txt").write_text("qsafe era")

        encrypt_file(data_dir / "old.txt", passphrase=TEST_PASSPHRASE)
        encrypt_file(data_dir / "new.txt", backend="qsafe", qsafe_recipients=[str(pk)])

        count = decrypt_directory(
            data_dir, passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk), logger=logger
        )
        assert count == 2
        assert (data_dir / "old.txt").read_text() == "aes era"
        assert (data_dir / "new.txt").read_text() == "qsafe era"

    def test_verify_in_place_no_plaintext(self, tmp_dir, keypair, logger):
        from backup_handler.verify import verify_backup_integrity

        sk, pk = keypair
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir()
        payload = backup_dir / "file.txt"
        payload.write_text("payload")
        size = payload.stat().st_size
        manifest = backup_dir / "backup_manifest_20260101_120000.json"
        manifest.write_text(
            f'{{"mode": "full", "copied": [{{"path": "/src/file.txt", "size": {size}}}],'
            ' "skipped": [], "failed": []}'
        )
        encrypt_file(payload, backend="qsafe", qsafe_recipients=[str(pk)])

        results = verify_backup_integrity(
            logger, [str(backup_dir)], encryption_passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk)
        )
        assert results["verified"] == 1
        assert results["corrupted"] == 0
        details = results["directories"][str(backup_dir)]["details"]
        assert any("authenticated in place" in d for d in details)
        # In-place verification must not leave plaintext anywhere in the backup
        assert not (backup_dir / "file.txt").exists()

        # Tamper with the ciphertext: authentication must fail
        enc = backup_dir / "file.txt.enc"
        blob = bytearray(enc.read_bytes())
        blob[-1] ^= 0xFF
        enc.write_bytes(bytes(blob))
        results = verify_backup_integrity(
            logger, [str(backup_dir)], encryption_passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk)
        )
        assert results["corrupted"] == 1
        details = results["directories"][str(backup_dir)]["details"]
        assert any("AUTHENTICATION FAILED" in d for d in details)

    def test_verify_encrypted_file_backend(self, tmp_dir, keypair):
        sk, pk = keypair
        f = tmp_dir / "x.txt"
        f.write_text("check me")
        enc_path = encrypt_file(f, backend="qsafe", qsafe_recipients=[str(pk)])

        assert qsafe_backend.verify_encrypted_file(enc_path, str(sk), TEST_PASSPHRASE)

        blob = bytearray(enc_path.read_bytes())
        blob[-1] ^= 0xFF
        enc_path.write_bytes(bytes(blob))
        assert not qsafe_backend.verify_encrypted_file(enc_path, str(sk), TEST_PASSPHRASE)

    def test_sign_verify_roundtrip(self, tmp_dir, sign_keypair):
        sk, pk = sign_keypair
        manifest = tmp_dir / "backup_manifest_20260101_120000.json"
        manifest.write_text('{"copied": []}')
        sig = tmp_dir / "backup_manifest_20260101_120000.json.sig"

        qsafe_backend.sign_file(manifest, sig, str(sk), TEST_PASSPHRASE)
        assert sig.exists()
        assert qsafe_backend.verify_signature_file(manifest, sig, str(pk))
        assert manifest_signature_status(manifest, str(pk)) == "valid"

        manifest.write_text('{"copied": [{"path": "/injected", "size": 0}]}')
        assert not qsafe_backend.verify_signature_file(manifest, sig, str(pk))
        assert manifest_signature_status(manifest, str(pk)) == "invalid"

    def test_restore_aborts_on_invalid_manifest_signature(self, tmp_dir, sign_keypair, logger):
        from backup_handler.restore import restore_backup

        sk, pk = sign_keypair
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir()
        data = backup_dir / "file.txt"
        data.write_text("payload")
        manifest = backup_dir / "backup_manifest_20260101_120000.json"
        manifest.write_text(
            '{"mode": "full", "copied": [{"path": "/original/src/file.txt", "size": 7}],'
            ' "skipped": [], "failed": []}'
        )
        qsafe_backend.sign_file(manifest, str(manifest) + ".sig", str(sk), TEST_PASSPHRASE)

        restore_dir = tmp_dir / "restore"

        # Valid signature: point-in-time restore proceeds
        ok = restore_backup(
            logger, str(backup_dir), str(restore_dir), timestamp="20260101_120000", qsafe_sign_pub=str(pk)
        )
        assert ok
        assert (restore_dir / "file.txt").read_text() == "payload"

        # Tampered manifest: restore aborts
        manifest.write_text(
            '{"mode": "full", "copied": [{"path": "evil.txt", "size": 4}], "skipped": [], "failed": []}'
        )
        ok = restore_backup(
            logger,
            str(backup_dir),
            str(tmp_dir / "restore2"),
            timestamp="20260101_120000",
            qsafe_sign_pub=str(pk),
        )
        assert not ok

    def test_verify_flags_tampered_manifest(self, tmp_dir, sign_keypair, logger):
        from backup_handler.verify import verify_backup_integrity

        sk, pk = sign_keypair
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir()
        (backup_dir / "file.txt").write_text("payload")
        manifest = backup_dir / "backup_manifest_20260101_120000.json"
        manifest.write_text(
            '{"mode": "full", "copied": [{"path": "file.txt", "size": 7}], "skipped": [], "failed": []}'
        )
        qsafe_backend.sign_file(manifest, str(manifest) + ".sig", str(sk), TEST_PASSPHRASE)

        results = verify_backup_integrity(logger, [str(backup_dir)], qsafe_sign_pub=str(pk))
        assert results["corrupted"] == 0
        assert results["verified"] == 1

        manifest.write_text(
            '{"mode": "full", "copied": [{"path": "evil.txt", "size": 4}], "skipped": [], "failed": []}'
        )
        results = verify_backup_integrity(logger, [str(backup_dir)], qsafe_sign_pub=str(pk))
        assert results["corrupted"] == 1
        assert results["verified"] == 0

    def test_tampered_ciphertext_rejected(self, tmp_dir, keypair):
        sk, pk = keypair
        f = tmp_dir / "victim.txt"
        f.write_text("integrity matters")
        enc_path = encrypt_file(f, backend="qsafe", qsafe_recipients=[str(pk)])

        blob = bytearray(enc_path.read_bytes())
        blob[-1] ^= 0xFF  # flip a bit in the GCM tag region
        enc_path.write_bytes(bytes(blob))

        with pytest.raises(RuntimeError):
            decrypt_file(enc_path, passphrase=TEST_PASSPHRASE, qsafe_secret_key=str(sk))
        assert not (tmp_dir / "victim.txt").exists()
