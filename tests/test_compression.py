"""Smoke tests for the pyzipper-backed password-protected ZIP writer."""

from __future__ import annotations

import pytest
import pyzipper

from backup_handler.compression import _write_aes_encrypted_zip


def _populate(root):
    (root / "a.txt").write_text("alpha")
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("bravo")


class TestAESZipWrite:
    def test_writes_aes256_encrypted_zip(self, tmp_dir):
        src = tmp_dir / "src"
        src.mkdir()
        _populate(src)
        files = [str(src / "a.txt"), str(src / "sub" / "b.txt")]
        output = tmp_dir / "out.zip"

        _write_aes_encrypted_zip(files, str(src), str(output), password="s3cret")

        with pyzipper.AESZipFile(output) as zf:
            zf.setpassword(b"s3cret")
            names = sorted(zf.namelist())
            assert names == ["a.txt", "sub/b.txt"]
            assert zf.read("a.txt") == b"alpha"
            assert zf.read("sub/b.txt") == b"bravo"

    def test_wrong_password_fails(self, tmp_dir):
        src = tmp_dir / "src"
        src.mkdir()
        _populate(src)
        output = tmp_dir / "out.zip"
        _write_aes_encrypted_zip([str(src / "a.txt")], str(src), str(output), password="correct")

        with pyzipper.AESZipFile(output) as zf:
            zf.setpassword(b"wrong")
            with pytest.raises(RuntimeError):
                zf.read("a.txt")
