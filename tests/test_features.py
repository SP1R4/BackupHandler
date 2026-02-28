"""
Tests for backup_handler v2.2.0 features:
  1. Backup Verification / Integrity Checks
  2. Email Notifications (SMTP)
  3. Remote Restore (SSH/S3 path parsing)
  4. Backup Deduplication
  + Config env var resolution, encryption (from v2.1.0)
"""

import os
import json
import shutil
import logging
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure project root is on sys.path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import resolve_env_vars, _resolve_all_env_vars, normalize_none
from src.encryption import (encrypt_file, decrypt_file, encrypt_directory,
                            decrypt_directory, derive_key, load_key_file)
from src.verify import verify_backup_integrity, print_verify_report
from src.email_notify import send_smtp_email
from src.restore import _is_ssh_path, _is_s3_path, _parse_ssh_path, _parse_s3_path
from src.dedup import deduplicate_directory, deduplicate_backup_dirs, _file_hash


@pytest.fixture
def logger():
    log = logging.getLogger('test_backup_handler')
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        log.addHandler(logging.StreamHandler())
    return log


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# Feature: Config Secrets from Environment Variables
# ──────────────────────────────────────────────────────────────────────

class TestEnvVarResolution:
    def test_resolve_single_var(self):
        os.environ['TEST_BH_VAR'] = 'secret123'
        assert resolve_env_vars('${TEST_BH_VAR}') == 'secret123'
        del os.environ['TEST_BH_VAR']

    def test_resolve_multiple_vars(self):
        os.environ['TEST_BH_A'] = 'hello'
        os.environ['TEST_BH_B'] = 'world'
        result = resolve_env_vars('${TEST_BH_A}-${TEST_BH_B}')
        assert result == 'hello-world'
        del os.environ['TEST_BH_A']
        del os.environ['TEST_BH_B']

    def test_unset_var_raises(self):
        with pytest.raises(ValueError, match="UNSET_VAR_12345"):
            resolve_env_vars('${UNSET_VAR_12345}')

    def test_no_vars_passthrough(self):
        assert resolve_env_vars('plain_value') == 'plain_value'

    def test_empty_string(self):
        assert resolve_env_vars('') == ''

    def test_resolve_all_env_vars(self, logger):
        import configparser
        config = configparser.ConfigParser()
        config.read_string("""
[DEFAULT]
source_dir = /tmp/test

[SSH]
password = ${TEST_BH_PASS}
""")
        os.environ['TEST_BH_PASS'] = 'my_secret'
        _resolve_all_env_vars(config, logger)
        assert config.get('SSH', 'password') == 'my_secret'
        del os.environ['TEST_BH_PASS']


# ──────────────────────────────────────────────────────────────────────
# Feature: Encryption at Rest
# ──────────────────────────────────────────────────────────────────────

class TestEncryption:
    def test_encrypt_decrypt_file_passphrase(self, tmp_dir):
        test_file = tmp_dir / 'test.txt'
        test_file.write_text('hello encryption')
        passphrase = 'test_passphrase_123'

        enc_path = encrypt_file(test_file, passphrase=passphrase)
        assert enc_path.suffix == '.enc'
        assert enc_path.exists()
        assert not test_file.exists()

        dec_path = decrypt_file(enc_path, passphrase=passphrase)
        assert dec_path.read_text() == 'hello encryption'
        assert not enc_path.exists()

    def test_encrypt_decrypt_file_keyfile(self, tmp_dir):
        test_file = tmp_dir / 'test.txt'
        test_file.write_bytes(b'keyfile test data')
        key_file = tmp_dir / 'keyfile.bin'
        key_file.write_bytes(os.urandom(32))

        enc_path = encrypt_file(test_file, key_file=str(key_file))
        assert enc_path.exists()

        dec_path = decrypt_file(enc_path, key_file=str(key_file))
        assert dec_path.read_bytes() == b'keyfile test data'

    def test_encrypt_directory_skips_manifests(self, tmp_dir, logger):
        (tmp_dir / 'data.txt').write_text('data')
        (tmp_dir / 'backup_manifest_20260101_120000.json').write_text('{}')

        count = encrypt_directory(tmp_dir, passphrase='pass', logger=logger)
        assert count == 1
        assert (tmp_dir / 'data.txt.enc').exists()
        assert (tmp_dir / 'backup_manifest_20260101_120000.json').exists()

    def test_encrypt_directory_skips_enc_files(self, tmp_dir, logger):
        (tmp_dir / 'already.enc').write_bytes(b'encrypted')
        (tmp_dir / 'new.txt').write_text('new')

        count = encrypt_directory(tmp_dir, passphrase='pass', logger=logger)
        assert count == 1  # Only new.txt encrypted

    def test_decrypt_directory(self, tmp_dir, logger):
        (tmp_dir / 'a.txt').write_text('aaa')
        (tmp_dir / 'b.txt').write_text('bbb')
        encrypt_directory(tmp_dir, passphrase='pass', logger=logger)

        assert (tmp_dir / 'a.txt.enc').exists()
        assert (tmp_dir / 'b.txt.enc').exists()

        decrypt_directory(tmp_dir, passphrase='pass', logger=logger)
        assert (tmp_dir / 'a.txt').read_text() == 'aaa'
        assert (tmp_dir / 'b.txt').read_text() == 'bbb'

    def test_derive_key_deterministic(self):
        salt = b'\x00' * 16
        key1 = derive_key('passphrase', salt)
        key2 = derive_key('passphrase', salt)
        assert key1 == key2
        assert len(key1) == 32

    def test_load_key_file_wrong_size(self, tmp_dir):
        kf = tmp_dir / 'bad.key'
        kf.write_bytes(b'\x00' * 16)
        with pytest.raises(ValueError, match="32 bytes"):
            load_key_file(str(kf))

    def test_wrong_passphrase_fails(self, tmp_dir):
        test_file = tmp_dir / 'test.txt'
        test_file.write_text('secret data')
        enc_path = encrypt_file(test_file, passphrase='correct')

        with pytest.raises(Exception):
            decrypt_file(enc_path, passphrase='wrong')


# ──────────────────────────────────────────────────────────────────────
# Feature 1: Backup Verification / Integrity Checks
# ──────────────────────────────────────────────────────────────────────

class TestVerification:
    def _create_backup_with_manifest(self, backup_dir):
        """Create a fake backup with a manifest."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Create files
        (backup_dir / 'file1.txt').write_text('content1')
        (backup_dir / 'file2.txt').write_text('content2')

        # Create manifest
        manifest = {
            'timestamp': '20260101_120000',
            'mode': 'full',
            'duration_seconds': 1.0,
            'files_copied': 2,
            'files_skipped': 0,
            'files_failed': 0,
            'total_bytes': 16,
            'copied': [
                {'path': str(backup_dir / 'file1.txt'), 'size': 8},
                {'path': str(backup_dir / 'file2.txt'), 'size': 8},
            ],
            'skipped': [],
            'failed': [],
        }
        manifest_path = backup_dir / 'backup_manifest_20260101_120000.json'
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    def test_verify_all_ok(self, logger, tmp_dir):
        backup_dir = tmp_dir / 'backup'
        self._create_backup_with_manifest(backup_dir)

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results['total'] == 2
        assert results['verified'] == 2
        assert results['missing'] == 0
        assert results['corrupted'] == 0

    def test_verify_missing_file(self, logger, tmp_dir):
        backup_dir = tmp_dir / 'backup'
        self._create_backup_with_manifest(backup_dir)
        # Delete one file
        (backup_dir / 'file1.txt').unlink()

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results['missing'] == 1
        assert results['verified'] == 1

    def test_verify_size_mismatch(self, logger, tmp_dir):
        backup_dir = tmp_dir / 'backup'
        self._create_backup_with_manifest(backup_dir)
        # Corrupt file by changing content (different size)
        (backup_dir / 'file1.txt').write_text('corrupted data that is different size')

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results['corrupted'] == 1

    def test_verify_no_manifest_fallback(self, logger, tmp_dir):
        backup_dir = tmp_dir / 'backup'
        backup_dir.mkdir(parents=True)
        (backup_dir / 'file.txt').write_text('data')

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results['total'] == 1
        assert results['verified'] == 1

    def test_verify_nonexistent_dir(self, logger, tmp_dir):
        results = verify_backup_integrity(logger, [str(tmp_dir / 'nonexistent')])
        assert results['total'] == 0

    def test_print_report(self, logger, tmp_dir, capsys):
        backup_dir = tmp_dir / 'backup'
        self._create_backup_with_manifest(backup_dir)
        results = verify_backup_integrity(logger, [str(backup_dir)])
        all_ok = print_verify_report(results)
        assert all_ok is True
        captured = capsys.readouterr()
        assert 'ALL BACKUPS VERIFIED OK' in captured.out


# ──────────────────────────────────────────────────────────────────────
# Feature 2: Email Notifications (SMTP)
# ──────────────────────────────────────────────────────────────────────

class TestSMTPEmail:
    @mock.patch('src.email_notify.smtplib.SMTP')
    def test_send_email_success(self, mock_smtp_class, logger):
        mock_server = mock.MagicMock()
        mock_smtp_class.return_value = mock_server

        result = send_smtp_email(
            logger,
            smtp_host='smtp.example.com',
            smtp_port=587,
            smtp_user='user@example.com',
            smtp_password='pass',
            from_addr='user@example.com',
            to_addrs=['recipient@example.com'],
            subject='Test',
            body='Test body',
        )

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with('user@example.com', 'pass')
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    @mock.patch('src.email_notify.smtplib.SMTP')
    def test_send_email_no_tls(self, mock_smtp_class, logger):
        mock_server = mock.MagicMock()
        mock_smtp_class.return_value = mock_server

        result = send_smtp_email(
            logger,
            smtp_host='smtp.example.com',
            smtp_port=25,
            smtp_user='user',
            smtp_password='pass',
            from_addr='user@example.com',
            to_addrs=['recipient@example.com'],
            subject='Test',
            body='Test body',
            use_tls=False,
        )

        assert result is True
        mock_server.starttls.assert_not_called()

    def test_send_email_no_recipients(self, logger):
        result = send_smtp_email(
            logger,
            smtp_host='smtp.example.com',
            smtp_port=587,
            smtp_user='user',
            smtp_password='pass',
            from_addr='user@example.com',
            to_addrs=[],
            subject='Test',
            body='Test body',
        )
        assert result is False

    @mock.patch('src.email_notify.smtplib.SMTP')
    def test_send_email_auth_failure_no_retry(self, mock_smtp_class, logger):
        import smtplib
        mock_server = mock.MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b'Auth failed')
        mock_smtp_class.return_value = mock_server

        result = send_smtp_email(
            logger,
            smtp_host='smtp.example.com',
            smtp_port=587,
            smtp_user='user',
            smtp_password='wrongpass',
            from_addr='user@example.com',
            to_addrs=['recipient@example.com'],
            subject='Test',
            body='Test body',
        )
        assert result is False
        # Auth failure should not retry
        assert mock_smtp_class.call_count == 1

    @mock.patch('src.email_notify.smtplib.SMTP')
    def test_send_email_retries_on_connection_error(self, mock_smtp_class, logger):
        mock_smtp_class.side_effect = [
            ConnectionError("refused"),
            ConnectionError("refused"),
            mock.MagicMock(),
        ]

        result = send_smtp_email(
            logger,
            smtp_host='smtp.example.com',
            smtp_port=587,
            smtp_user='user',
            smtp_password='pass',
            from_addr='user@example.com',
            to_addrs=['recipient@example.com'],
            subject='Test',
            body='Test body',
        )
        assert result is True
        assert mock_smtp_class.call_count == 3


# ──────────────────────────────────────────────────────────────────────
# Feature 3: Remote Restore (SSH/S3 path parsing)
# ──────────────────────────────────────────────────────────────────────

class TestRemoteRestore:
    def test_is_ssh_path(self):
        assert _is_ssh_path('user@host:/backup/dir') is True
        assert _is_ssh_path('ssh://user@host/backup') is True
        assert _is_ssh_path('/local/path') is False
        assert _is_ssh_path('s3://bucket/prefix') is False

    def test_is_s3_path(self):
        assert _is_s3_path('s3://my-bucket/prefix') is True
        assert _is_s3_path('s3://bucket') is True
        assert _is_s3_path('/local/path') is False
        assert _is_s3_path('user@host:/path') is False

    def test_parse_ssh_path_user_host(self):
        user, host, path = _parse_ssh_path('admin@backup-server:/backups/daily')
        assert user == 'admin'
        assert host == 'backup-server'
        assert path == '/backups/daily'

    def test_parse_ssh_path_no_user(self):
        user, host, path = _parse_ssh_path('backup-server:/backups/daily')
        assert user is None
        assert host == 'backup-server'
        assert path == '/backups/daily'

    def test_parse_ssh_url(self):
        user, host, path = _parse_ssh_path('ssh://admin@backup-server/backups/daily')
        assert user == 'admin'
        assert host == 'backup-server'
        assert path == '/backups/daily'

    def test_parse_s3_path(self):
        bucket, prefix = _parse_s3_path('s3://my-bucket/backups/2026')
        assert bucket == 'my-bucket'
        assert prefix == 'backups/2026'

    def test_parse_s3_path_no_prefix(self):
        bucket, prefix = _parse_s3_path('s3://my-bucket')
        assert bucket == 'my-bucket'
        assert prefix == ''

    def test_parse_s3_path_single_prefix(self):
        bucket, prefix = _parse_s3_path('s3://bucket/prefix')
        assert bucket == 'bucket'
        assert prefix == 'prefix'


# ──────────────────────────────────────────────────────────────────────
# Feature 4: Backup Deduplication
# ──────────────────────────────────────────────────────────────────────

class TestDeduplication:
    def test_file_hash_consistent(self, tmp_dir):
        f = tmp_dir / 'test.txt'
        f.write_text('hello world')
        h1 = _file_hash(f)
        h2 = _file_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_dedup_identical_files(self, logger, tmp_dir):
        content = b'identical content for dedup test'
        (tmp_dir / 'file1.txt').write_bytes(content)
        (tmp_dir / 'file2.txt').write_bytes(content)
        (tmp_dir / 'file3.txt').write_bytes(content)

        result = deduplicate_directory(logger, tmp_dir)
        assert result['files_checked'] == 3
        assert result['duplicates_found'] == 2
        assert result['bytes_saved'] == len(content) * 2

        # Verify files are now hardlinks (same inode)
        inode1 = (tmp_dir / 'file1.txt').stat().st_ino
        inode2 = (tmp_dir / 'file2.txt').stat().st_ino
        inode3 = (tmp_dir / 'file3.txt').stat().st_ino
        assert inode1 == inode2 == inode3

    def test_dedup_unique_files(self, logger, tmp_dir):
        (tmp_dir / 'file1.txt').write_text('unique1')
        (tmp_dir / 'file2.txt').write_text('unique2')

        result = deduplicate_directory(logger, tmp_dir)
        assert result['duplicates_found'] == 0
        assert result['bytes_saved'] == 0

    def test_dedup_skips_manifests(self, logger, tmp_dir):
        content = b'same content'
        (tmp_dir / 'file1.txt').write_bytes(content)
        (tmp_dir / 'backup_manifest_20260101_120000.json').write_bytes(content)

        result = deduplicate_directory(logger, tmp_dir)
        # Manifest should be skipped, so no dedup pair
        assert result['duplicates_found'] == 0

    def test_dedup_skips_enc_files(self, logger, tmp_dir):
        content = b'same content'
        (tmp_dir / 'file1.txt').write_bytes(content)
        (tmp_dir / 'file1.txt.enc').write_bytes(content)

        result = deduplicate_directory(logger, tmp_dir)
        assert result['duplicates_found'] == 0

    def test_dedup_empty_directory(self, logger, tmp_dir):
        result = deduplicate_directory(logger, tmp_dir)
        assert result['files_checked'] == 0
        assert result['duplicates_found'] == 0

    def test_dedup_nonexistent_directory(self, logger, tmp_dir):
        result = deduplicate_directory(logger, tmp_dir / 'nonexistent')
        assert result['files_checked'] == 0

    def test_deduplicate_backup_dirs(self, logger, tmp_dir):
        dir1 = tmp_dir / 'backup1'
        dir2 = tmp_dir / 'backup2'
        dir1.mkdir()
        dir2.mkdir()

        content = b'same file content'
        (dir1 / 'file.txt').write_bytes(content)
        (dir2 / 'file.txt').write_bytes(content)

        result = deduplicate_backup_dirs(logger, [str(dir1), str(dir2)])
        # Cross-directory dedup should find 1 duplicate
        assert result['duplicates_found'] >= 1

    def test_dedup_preserves_content(self, logger, tmp_dir):
        content = b'important data to preserve'
        (tmp_dir / 'original.txt').write_bytes(content)
        (tmp_dir / 'copy.txt').write_bytes(content)

        deduplicate_directory(logger, tmp_dir)

        # Both files should still have the same content
        assert (tmp_dir / 'original.txt').read_bytes() == content
        assert (tmp_dir / 'copy.txt').read_bytes() == content


# ──────────────────────────────────────────────────────────────────────
# Config normalize_none utility
# ──────────────────────────────────────────────────────────────────────

class TestNormalizeNone:
    def test_none_value(self):
        assert normalize_none(None) is None

    def test_none_string(self):
        assert normalize_none('None') is None
        assert normalize_none('none') is None

    def test_empty_string(self):
        assert normalize_none('') is None
        assert normalize_none('   ') is None

    def test_valid_value(self):
        assert normalize_none('hello') == 'hello'
        assert normalize_none('  hello  ') == 'hello'
