"""
qsafe_backend.py - Qsafe Post-Quantum Encryption Backend

Encrypts backup files to Qsafe recipient public keys using a hybrid
X25519 + ML-KEM-1024 key establishment with AES-256-GCM payload
encryption (NIST FIPS 203).

Unlike the symmetric AES backend, Qsafe uses a public-key workflow:
the backup host needs only recipient *public* keys to encrypt, so a
compromised backup server can create backups but never read them. The
passphrase-wrapped secret key is required only for restore and verify,
and can live off-host. Multiple recipients are supported — any one
secret key decrypts.

Engine resolution order (cached after first probe):
    1. The ``qsafe`` Python bindings (ctypes over libqsafe), if importable.
    2. The ``qsafe`` CLI on PATH. The secret-key passphrase is passed via
       the QSAFE_PASSPHRASE environment variable, never on the command line.

Qsafe encrypted files begin with an 8-byte version header ("QSAFE00x"),
which lets ``encryption.decrypt_file`` dispatch by magic bytes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

# All Qsafe format versions share this prefix ("QSAFE005"/"QSAFE006"/...).
QSAFE_MAGIC = b"QSAFE"

_bindings: ModuleType | None = None
_bindings_probed = False


def is_qsafe_data(data: bytes) -> bool:
    """Return True if the given bytes look like a Qsafe encrypted file."""
    return data[: len(QSAFE_MAGIC)] == QSAFE_MAGIC


def _load_bindings() -> ModuleType | None:
    """Import the qsafe Python bindings once; None if unavailable."""
    global _bindings, _bindings_probed
    if not _bindings_probed:
        _bindings_probed = True
        try:
            import qsafe  # ctypes bindings over libqsafe

            _bindings = qsafe
        except (ImportError, OSError):
            _bindings = None
    return _bindings


def _cli_path() -> str | None:
    return shutil.which("qsafe")


def is_available() -> bool:
    """True if either the qsafe bindings or the qsafe CLI can be used."""
    return _load_bindings() is not None or _cli_path() is not None


def engine_description() -> str:
    """Human-readable description of the engine that will be used."""
    if _load_bindings() is not None:
        return "qsafe python bindings (libqsafe)"
    cli = _cli_path()
    if cli:
        return f"qsafe CLI ({cli})"
    return "unavailable"


def parse_recipients(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """
    Parse the ``qsafe_recipients`` config value into a list of key paths.

    Accepts a comma-separated string, a list, or None. Paths are stripped
    and empty entries dropped.
    """
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def _run_cli(argv: list[str], passphrase: str | None = None) -> None:
    env = os.environ.copy()
    if passphrase:
        env["QSAFE_PASSPHRASE"] = passphrase
    result = subprocess.run(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,  # fail instead of hanging on a prompt
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"qsafe CLI failed ({' '.join(argv[:2])}): {detail or f'exit {result.returncode}'}"
        )


def encrypt_file_to(
    in_path: str | os.PathLike[str], out_path: str | os.PathLike[str], recipients: list[str]
) -> None:
    """
    Encrypt ``in_path`` to ``out_path`` for the given recipient public keys.

    The plaintext input is left untouched; callers decide when to delete it.
    """
    if not recipients:
        raise ValueError("Qsafe encryption requires at least one recipient public key")
    for recipient in recipients:
        if not Path(recipient).exists():
            raise FileNotFoundError(f"Qsafe recipient public key not found: {recipient}")

    bindings = _load_bindings()
    if bindings is not None:
        try:
            bindings.encrypt(str(in_path), str(out_path), [str(r) for r in recipients])
        except Exception as e:
            raise RuntimeError(f"qsafe encrypt failed: {e}") from e
        return

    cli = _cli_path()
    if cli is None:
        raise RuntimeError(
            "Qsafe backend selected but neither the qsafe Python bindings nor "
            "the qsafe CLI are available. Install Qsafe or set backend = aes."
        )
    argv = [cli, "encrypt", "--force"]
    for recipient in recipients:
        argv += ["-r", str(recipient)]
    argv += [str(in_path), str(out_path)]
    _run_cli(argv)


def decrypt_file_to(
    in_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    secret_key: str | os.PathLike[str],
    passphrase: str | None,
) -> None:
    """
    Decrypt ``in_path`` to ``out_path`` using the passphrase-wrapped secret key.

    The ciphertext input is left untouched; callers decide when to delete it.
    """
    if not secret_key:
        raise ValueError("Qsafe decryption requires a secret key (qsafe_secret_key)")
    if not Path(secret_key).exists():
        raise FileNotFoundError(f"Qsafe secret key not found: {secret_key}")
    # The CLI falls back to $QSAFE_PASSPHRASE on its own; the bindings need it explicit.
    effective_passphrase = passphrase or os.environ.get("QSAFE_PASSPHRASE")

    bindings = _load_bindings()
    if bindings is not None:
        if not effective_passphrase:
            raise ValueError(
                "Qsafe decryption requires the secret-key passphrase "
                "([ENCRYPTION] passphrase or QSAFE_PASSPHRASE)"
            )
        try:
            bindings.decrypt(str(in_path), str(out_path), str(secret_key), effective_passphrase)
        except Exception as e:
            raise RuntimeError(f"qsafe decrypt failed: {e}") from e
        return

    cli = _cli_path()
    if cli is None:
        raise RuntimeError(
            "Qsafe backend selected but neither the qsafe Python bindings nor "
            "the qsafe CLI are available. Install Qsafe or set backend = aes."
        )
    if not effective_passphrase:
        raise ValueError(
            "Qsafe decryption requires the secret-key passphrase "
            "([ENCRYPTION] passphrase or QSAFE_PASSPHRASE)"
        )
    argv = [cli, "decrypt", "--force", "--key-file", str(secret_key), str(in_path), str(out_path)]
    _run_cli(argv, passphrase=effective_passphrase)


def sign_file(
    in_path: str | os.PathLike[str],
    sig_path: str | os.PathLike[str],
    sign_secret_key: str | os.PathLike[str],
    passphrase: str | None,
) -> None:
    """Create a detached ML-DSA-87 signature for ``in_path`` at ``sig_path``."""
    if not Path(sign_secret_key).exists():
        raise FileNotFoundError(f"Qsafe signing secret key not found: {sign_secret_key}")
    effective_passphrase = passphrase or os.environ.get("QSAFE_PASSPHRASE")
    if not effective_passphrase:
        raise ValueError(
            "Qsafe signing requires the signing-key passphrase "
            "([ENCRYPTION] qsafe_sign_passphrase or QSAFE_PASSPHRASE)"
        )

    bindings = _load_bindings()
    if bindings is not None:
        try:
            bindings.sign(str(in_path), str(sig_path), str(sign_secret_key), effective_passphrase)
        except Exception as e:
            raise RuntimeError(f"qsafe sign failed: {e}") from e
        return

    cli = _cli_path()
    if cli is None:
        raise RuntimeError(
            "Qsafe manifest signing configured but neither the qsafe Python bindings "
            "nor the qsafe CLI are available. Install Qsafe or unset qsafe_sign_key."
        )
    argv = [cli, "sign", "--force", "--key-file", str(sign_secret_key), str(in_path), str(sig_path)]
    _run_cli(argv, passphrase=effective_passphrase)


def verify_signature_file(
    in_path: str | os.PathLike[str],
    sig_path: str | os.PathLike[str],
    sign_public_key: str | os.PathLike[str],
) -> bool:
    """Verify a detached signature. Returns True if valid, False if not."""
    if not Path(sign_public_key).exists():
        raise FileNotFoundError(f"Qsafe signing public key not found: {sign_public_key}")

    bindings = _load_bindings()
    if bindings is not None:
        try:
            bindings.verify_signature(str(in_path), str(sig_path), str(sign_public_key))
            return True
        except Exception:
            return False

    cli = _cli_path()
    if cli is None:
        raise RuntimeError(
            "Qsafe signature verification configured but neither the qsafe Python "
            "bindings nor the qsafe CLI are available. Install Qsafe or unset qsafe_sign_pub."
        )
    try:
        _run_cli([cli, "verify-sig", "--pub-file", str(sign_public_key), str(in_path), str(sig_path)])
        return True
    except RuntimeError:
        return False
