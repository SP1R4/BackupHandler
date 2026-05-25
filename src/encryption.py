"""
encryption.py - AES-256-GCM Encryption at Rest

Provides file-level encryption and decryption for backup data using
AES-256-GCM authenticated encryption.

Key sources (KDF identifiers):
    0x00 raw key file   - 32 random bytes read directly from disk.
    0x01 PBKDF2-HMAC    - SHA256, 600,000 iterations (OWASP minimum).
    0x02 Argon2id       - t=3, m=64MiB, p=1 (optional, requires argon2-cffi).

Encrypted file format v1 (binary):
    [4B magic "BHE1"][1B kdf_id][16B salt][12B nonce][ciphertext + 16B GCM tag]

The first six bytes (magic + version + kdf_id) are bound into the AEAD
associated_data, so flipping the KDF byte or the version invalidates
the authentication tag — preventing downgrade attacks.

Legacy format (pre-versioning):
    [16B salt][12B nonce][ciphertext + 16B GCM tag]

Legacy files are detected by the absence of the magic prefix and decrypted
via the old code path for backward compatibility.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from tqdm import tqdm

# ─── Format constants ───────────────────────────────────────────────────────
MAGIC = b"BHE1"
MAGIC_LEN = len(MAGIC)
KDF_KEYFILE = 0x00
KDF_PBKDF2 = 0x01
KDF_ARGON2ID = 0x02

# ─── Cryptographic constants ────────────────────────────────────────────────
PBKDF2_ITERATIONS = 600_000
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST_KIB = 64 * 1024
ARGON2_PARALLELISM = 1
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32

_HEADER_PREFIX_LEN = MAGIC_LEN + 1
_HEADER_LEN_V1 = _HEADER_PREFIX_LEN + SALT_SIZE + NONCE_SIZE


def _derive_pbkdf2(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _derive_argon2id(passphrase: str, salt: bytes) -> bytes:
    try:
        from argon2.low_level import Type, hash_secret_raw
    except ImportError as e:
        raise RuntimeError(
            "Argon2id requested but argon2-cffi is not installed. "
            "Install with: pip install 'backup-handler[argon2]'"
        ) from e
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST_KIB,
        parallelism=ARGON2_PARALLELISM,
        hash_len=KEY_SIZE,
        type=Type.ID,
    )


def derive_key(passphrase: str, salt: bytes, kdf_id: int = KDF_PBKDF2) -> bytes:
    """Derive a 32-byte AES key from a passphrase using the given KDF."""
    if kdf_id == KDF_PBKDF2:
        return _derive_pbkdf2(passphrase, salt)
    if kdf_id == KDF_ARGON2ID:
        return _derive_argon2id(passphrase, salt)
    raise ValueError(f"Unsupported KDF id for passphrase derivation: {kdf_id:#x}")


def load_key_file(path: str | os.PathLike) -> bytes:
    """Read a raw 32-byte key from a file."""
    key_path = Path(path)
    if not key_path.exists():
        raise FileNotFoundError(f"Key file not found: {path}")
    key = key_path.read_bytes()
    if len(key) != KEY_SIZE:
        raise ValueError(f"Key file must be exactly {KEY_SIZE} bytes, got {len(key)}")
    return key


def _kdf_name(kdf_id: int) -> str:
    return {KDF_KEYFILE: "key_file", KDF_PBKDF2: "pbkdf2", KDF_ARGON2ID: "argon2id"}.get(
        kdf_id, f"unknown({kdf_id:#x})"
    )


def _parse_kdf_choice(kdf: str | None) -> int:
    if kdf is None or kdf == "pbkdf2":
        return KDF_PBKDF2
    if kdf == "argon2id":
        return KDF_ARGON2ID
    raise ValueError(f"Unknown KDF choice: {kdf!r}. Use 'pbkdf2' or 'argon2id'.")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write data atomically: open tmp, write, fsync, rename onto path."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def encrypt_file(
    path: str | os.PathLike,
    passphrase: str | None = None,
    key_file: str | None = None,
    kdf: str | None = None,
) -> Path:
    """
    Encrypt a single file with AES-256-GCM in the v1 versioned format.

    Writes ``<original>.enc`` atomically and deletes the plaintext only after
    the encrypted file is durable on disk.
    """
    path = Path(path)
    plaintext = path.read_bytes()

    if key_file:
        kdf_id = KDF_KEYFILE
        key = load_key_file(key_file)
        salt = os.urandom(SALT_SIZE)
    elif passphrase:
        kdf_id = _parse_kdf_choice(kdf)
        salt = os.urandom(SALT_SIZE)
        key = derive_key(passphrase, salt, kdf_id=kdf_id)
    else:
        raise ValueError("Either passphrase or key_file must be provided for encryption")

    nonce = os.urandom(NONCE_SIZE)
    header_prefix = MAGIC + bytes([kdf_id])

    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, header_prefix)

    enc_path = path.with_name(path.name + ".enc")
    _atomic_write(enc_path, header_prefix + salt + nonce + ciphertext)
    path.unlink()
    return enc_path


def _decrypt_legacy(data: bytes, passphrase: str | None, key_file: str | None) -> bytes:
    """Decrypt a pre-versioning .enc payload: [salt][nonce][ct+tag]."""
    salt = data[:SALT_SIZE]
    nonce = data[SALT_SIZE : SALT_SIZE + NONCE_SIZE]
    ciphertext = data[SALT_SIZE + NONCE_SIZE :]
    if key_file:
        key = load_key_file(key_file)
    elif passphrase:
        key = _derive_pbkdf2(passphrase, salt)
    else:
        raise ValueError("Either passphrase or key_file must be provided for decryption")
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def _decrypt_v1(data: bytes, passphrase: str | None, key_file: str | None) -> bytes:
    """Decrypt a v1 .enc payload: [magic][kdf_id][salt][nonce][ct+tag]."""
    kdf_id = data[MAGIC_LEN]
    header_prefix = data[:_HEADER_PREFIX_LEN]
    salt = data[_HEADER_PREFIX_LEN : _HEADER_PREFIX_LEN + SALT_SIZE]
    nonce = data[_HEADER_PREFIX_LEN + SALT_SIZE : _HEADER_LEN_V1]
    ciphertext = data[_HEADER_LEN_V1:]

    if kdf_id == KDF_KEYFILE:
        if not key_file:
            raise ValueError(
                "File was encrypted with a key file but no key_file was provided for decryption"
            )
        key = load_key_file(key_file)
    elif kdf_id in (KDF_PBKDF2, KDF_ARGON2ID):
        if not passphrase:
            raise ValueError(
                f"File was encrypted with {_kdf_name(kdf_id)} but no passphrase was provided"
            )
        key = derive_key(passphrase, salt, kdf_id=kdf_id)
    else:
        raise ValueError(f"Unsupported KDF id in header: {kdf_id:#x}")

    return AESGCM(key).decrypt(nonce, ciphertext, header_prefix)


def decrypt_file(
    enc_path: str | os.PathLike,
    passphrase: str | None = None,
    key_file: str | None = None,
) -> Path:
    """Decrypt a ``.enc`` file. Detects v1 vs legacy via the magic prefix."""
    enc_path = Path(enc_path)
    data = enc_path.read_bytes()

    if data[:MAGIC_LEN] == MAGIC:
        plaintext = _decrypt_v1(data, passphrase, key_file)
    else:
        plaintext = _decrypt_legacy(data, passphrase, key_file)

    if enc_path.name.endswith(".enc"):
        out_path = enc_path.with_name(enc_path.name[:-4])
    else:
        out_path = enc_path.with_suffix("")

    _atomic_write(out_path, plaintext)
    enc_path.unlink()
    return out_path


def encrypt_directory(
    directory: str | os.PathLike,
    passphrase: str | None = None,
    key_file: str | None = None,
    logger=None,
    workers: int = 1,
    kdf: str | None = None,
) -> int:
    """
    Encrypt all eligible files in a directory tree.

    Skips files already ending in ``.enc`` and backup manifest JSON files
    (needed for status/restore lookups without decryption keys).
    """
    directory = Path(directory)
    files = [
        f
        for f in directory.rglob("*")
        if f.is_file()
        and f.suffix != ".enc"
        and not (f.name.startswith("backup_manifest_") and f.suffix == ".json")
    ]

    if not files:
        return 0

    encrypted = 0
    workers = max(1, workers)

    def _encrypt_one(file: Path) -> Path:
        encrypt_file(file, passphrase=passphrase, key_file=key_file, kdf=kdf)
        return file

    if workers == 1:
        for file in tqdm(files, desc="Encrypting files", unit="files"):
            try:
                _encrypt_one(file)
                encrypted += 1
                if logger:
                    logger.debug(f"Encrypted: {file}")
            except Exception as e:
                if logger:
                    logger.error(f"Failed to encrypt {file}: {e}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_encrypt_one, f): f for f in files}
            for future in tqdm(
                as_completed(futures), total=len(futures), desc="Encrypting files", unit="files"
            ):
                file = futures[future]
                try:
                    future.result()
                    encrypted += 1
                    if logger:
                        logger.debug(f"Encrypted: {file}")
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to encrypt {file}: {e}")

    if logger:
        logger.info(f"Encrypted {encrypted} files in {directory}")
    return encrypted


def decrypt_directory(
    directory: str | os.PathLike,
    passphrase: str | None = None,
    key_file: str | None = None,
    logger=None,
    workers: int = 1,
) -> int:
    """Decrypt all ``.enc`` files in a directory tree."""
    directory = Path(directory)
    files = [f for f in directory.rglob("*.enc") if f.is_file()]

    if not files:
        return 0

    decrypted = 0
    workers = max(1, workers)

    def _decrypt_one(file: Path) -> Path:
        decrypt_file(file, passphrase=passphrase, key_file=key_file)
        return file

    if workers == 1:
        for file in tqdm(files, desc="Decrypting files", unit="files"):
            try:
                _decrypt_one(file)
                decrypted += 1
                if logger:
                    logger.debug(f"Decrypted: {file}")
            except Exception as e:
                if logger:
                    logger.error(f"Failed to decrypt {file}: {e}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_decrypt_one, f): f for f in files}
            for future in tqdm(
                as_completed(futures), total=len(futures), desc="Decrypting files", unit="files"
            ):
                file = futures[future]
                try:
                    future.result()
                    decrypted += 1
                    if logger:
                        logger.debug(f"Decrypted: {file}")
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to decrypt {file}: {e}")

    if logger:
        logger.info(f"Decrypted {decrypted} files in {directory}")
    return decrypted


def get_encryption_key(
    passphrase: str | None = None,
    key_file: str | None = None,
    salt: bytes | None = None,
    kdf: str | None = None,
) -> tuple[bytes, bytes | None]:
    """
    Compatibility shim: derive a key independently of encrypt_file().

    Returns ``(key, salt)`` where salt is None when key_file is used.
    """
    if key_file:
        return load_key_file(key_file), None
    if passphrase:
        if salt is None:
            salt = os.urandom(SALT_SIZE)
        kdf_id = _parse_kdf_choice(kdf)
        return derive_key(passphrase, salt, kdf_id=kdf_id), salt
    raise ValueError("Either passphrase or key_file must be provided for encryption")
