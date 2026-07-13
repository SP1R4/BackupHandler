"""
encryption.py - Encryption at Rest (AES-256-GCM and Qsafe backends)

Provides file-level encryption and decryption for backup data.

Two backends share the ``.enc`` extension and are distinguished by magic
bytes on decrypt:

aes (default) — symmetric AES-256-GCM. Key sources (KDF identifiers):
    0x00 raw key file   - 32 random bytes read directly from disk.
    0x01 PBKDF2-HMAC    - SHA256, 600,000 iterations (OWASP minimum).
    0x02 Argon2id       - t=3, m=64MiB, p=1 (optional, requires argon2-cffi).

    Encrypted file format v2 (binary, streaming — written by default):
        [4B magic "BHE2"][1B kdf_id][16B salt][8B nonce_prefix][4B chunk_size BE]
        followed by one AES-GCM sealed chunk per chunk_size bytes of plaintext.

    Each chunk's nonce is nonce_prefix || (chunk_index | final_flag) where
    final_flag (top bit) marks the last chunk — so truncating, extending, or
    reordering chunks invalidates a tag. The full header is bound into every
    chunk's associated_data, preventing KDF-downgrade and parameter tampering.
    Both encryption and decryption stream in constant memory.

    Format v1 (read-compatible, no longer written):
        [4B magic "BHE1"][1B kdf_id][16B salt][12B nonce][ciphertext + 16B GCM tag]
    with magic + kdf_id bound as associated_data.

    Legacy format (pre-versioning, read-compatible):
        [16B salt][12B nonce][ciphertext + 16B GCM tag]

    v1 and legacy files are detected by magic prefix (or its absence) and
    decrypted via their original code paths for backward compatibility.

qsafe — hybrid post-quantum public-key encryption (X25519 + ML-KEM-1024 +
    AES-256-GCM) via the Qsafe project. Files start with a "QSAFE00x"
    header. Encryption needs only recipient public keys; decryption needs
    the passphrase-wrapped secret key. See ``qsafe_backend.py``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from tqdm import tqdm

from . import qsafe_backend

# ─── Format constants ───────────────────────────────────────────────────────
MAGIC = b"BHE1"
MAGIC_V2 = b"BHE2"
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

# ─── v2 streaming constants ─────────────────────────────────────────────────
AES_CHUNK_SIZE = 1024 * 1024  # plaintext bytes per sealed chunk
_NONCE_PREFIX_SIZE = 8
_GCM_TAG_SIZE = 16
_HEADER_LEN_V2 = _HEADER_PREFIX_LEN + SALT_SIZE + _NONCE_PREFIX_SIZE + 4
_FINAL_CHUNK_FLAG = 0x80000000
_MAX_CHUNK_SIZE = 64 * 1024 * 1024  # sanity bound when parsing headers


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
    backend: str = "aes",
    qsafe_recipients: list[str] | None = None,
) -> Path:
    """
    Encrypt a single file, writing ``<original>.enc`` and deleting the
    plaintext only after the encrypted file is durable on disk.

    backend 'aes' (default) streams AES-256-GCM chunks in the v2 format
    (constant memory); backend 'qsafe' encrypts to the given recipient
    public keys.
    """
    path = Path(path)

    if backend == "qsafe":
        return _encrypt_file_qsafe(path, qsafe_recipients or [])
    if backend != "aes":
        raise ValueError(f"Unknown encryption backend: {backend!r}. Use 'aes' or 'qsafe'.")

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

    return _encrypt_file_aes_v2(path, key, kdf_id, salt)


def _chunk_nonce(prefix: bytes, index: int, final: bool) -> bytes:
    """Per-chunk GCM nonce: 8B random prefix + 4B counter with a final-chunk flag bit."""
    if index >= _FINAL_CHUNK_FLAG:
        raise ValueError("File too large: v2 chunk counter overflow")
    word = index | (_FINAL_CHUNK_FLAG if final else 0)
    return prefix + word.to_bytes(4, "big")


def _encrypt_file_aes_v2(path: Path, key: bytes, kdf_id: int, salt: bytes) -> Path:
    """
    Stream-encrypt one file in the v2 chunked format with constant memory.

    Writes to a tmp file, fsyncs, renames onto ``<name>.enc``, then deletes
    the plaintext — same durability contract as the old whole-file path.
    """
    enc_path = path.with_name(path.name + ".enc")
    tmp = enc_path.with_name(enc_path.name + ".tmp")
    nonce_prefix = os.urandom(_NONCE_PREFIX_SIZE)
    header = MAGIC_V2 + bytes([kdf_id]) + salt + nonce_prefix + AES_CHUNK_SIZE.to_bytes(4, "big")
    aesgcm = AESGCM(key)

    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
            out.write(header)
            index = 0
            chunk = src.read(AES_CHUNK_SIZE)
            while True:
                next_chunk = src.read(AES_CHUNK_SIZE)
                final = len(next_chunk) == 0
                nonce = _chunk_nonce(nonce_prefix, index, final)
                out.write(aesgcm.encrypt(nonce, chunk, header))
                if final:
                    break
                chunk = next_chunk
                index += 1
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, enc_path)
    finally:
        tmp.unlink(missing_ok=True)
    path.unlink()
    return enc_path


def _encrypt_file_qsafe(path: Path, recipients: list[str]) -> Path:
    """Encrypt one file to Qsafe recipients; tmp + os.replace keeps it atomic."""
    enc_path = path.with_name(path.name + ".enc")
    tmp = enc_path.with_name(enc_path.name + ".tmp")
    try:
        qsafe_backend.encrypt_file_to(path, tmp, recipients)
        os.replace(tmp, enc_path)
    finally:
        tmp.unlink(missing_ok=True)
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


def _resolve_decrypt_key(kdf_id: int, salt: bytes, passphrase: str | None, key_file: str | None) -> bytes:
    """Resolve the AES key for a header's kdf_id, validating credentials."""
    if kdf_id == KDF_KEYFILE:
        if not key_file:
            raise ValueError("File was encrypted with a key file but no key_file was provided for decryption")
        return load_key_file(key_file)
    if kdf_id in (KDF_PBKDF2, KDF_ARGON2ID):
        if not passphrase:
            raise ValueError(f"File was encrypted with {_kdf_name(kdf_id)} but no passphrase was provided")
        return derive_key(passphrase, salt, kdf_id=kdf_id)
    raise ValueError(f"Unsupported KDF id in header: {kdf_id:#x}")


def _decrypt_v1(data: bytes, passphrase: str | None, key_file: str | None) -> bytes:
    """Decrypt a v1 .enc payload: [magic][kdf_id][salt][nonce][ct+tag]."""
    kdf_id = data[MAGIC_LEN]
    header_prefix = data[:_HEADER_PREFIX_LEN]
    salt = data[_HEADER_PREFIX_LEN : _HEADER_PREFIX_LEN + SALT_SIZE]
    nonce = data[_HEADER_PREFIX_LEN + SALT_SIZE : _HEADER_LEN_V1]
    ciphertext = data[_HEADER_LEN_V1:]

    key = _resolve_decrypt_key(kdf_id, salt, passphrase, key_file)
    return AESGCM(key).decrypt(nonce, ciphertext, header_prefix)


def _decrypt_v2_stream(enc_path: Path, out_path: Path, passphrase: str | None, key_file: str | None) -> None:
    """
    Stream-decrypt a v2 chunked file to ``out_path`` with constant memory.

    Every chunk's tag is verified before its plaintext is written; the
    final-flag bit in the nonce makes truncation or extension fail loudly.
    Writes via tmp + fsync + rename so a failed decrypt leaves nothing behind.
    """
    with open(enc_path, "rb") as f:
        header = f.read(_HEADER_LEN_V2)
        if len(header) != _HEADER_LEN_V2:
            raise ValueError("Truncated v2 header")
        kdf_id = header[MAGIC_LEN]
        salt = header[_HEADER_PREFIX_LEN : _HEADER_PREFIX_LEN + SALT_SIZE]
        nonce_prefix = header[
            _HEADER_PREFIX_LEN + SALT_SIZE : _HEADER_PREFIX_LEN + SALT_SIZE + _NONCE_PREFIX_SIZE
        ]
        chunk_size = int.from_bytes(header[-4:], "big")
        if not 0 < chunk_size <= _MAX_CHUNK_SIZE:
            raise ValueError(f"Invalid v2 chunk size: {chunk_size}")

        key = _resolve_decrypt_key(kdf_id, salt, passphrase, key_file)
        aesgcm = AESGCM(key)
        ct_chunk_size = chunk_size + _GCM_TAG_SIZE

        tmp = out_path.with_name(out_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                index = 0
                chunk = f.read(ct_chunk_size)
                if len(chunk) < _GCM_TAG_SIZE:
                    raise ValueError("Truncated v2 ciphertext: missing final chunk")
                while True:
                    next_chunk = f.read(ct_chunk_size)
                    final = len(next_chunk) == 0
                    nonce = _chunk_nonce(nonce_prefix, index, final)
                    out.write(aesgcm.decrypt(nonce, chunk, header))
                    if final:
                        break
                    chunk = next_chunk
                    index += 1
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, out_path)
        finally:
            tmp.unlink(missing_ok=True)


def decrypt_file(
    enc_path: str | os.PathLike,
    passphrase: str | None = None,
    key_file: str | None = None,
    qsafe_secret_key: str | None = None,
) -> Path:
    """
    Decrypt a ``.enc`` file. Dispatches by magic prefix: Qsafe files
    ("QSAFE") decrypt via the qsafe backend using the secret key, BHE2
    files via the streaming AES v2 path, BHE1 via AES v1, anything else
    via the legacy AES path.

    For Qsafe files, ``passphrase`` unwraps the secret key.
    """
    enc_path = Path(enc_path)

    if enc_path.name.endswith(".enc"):
        out_path = enc_path.with_name(enc_path.name[:-4])
    else:
        out_path = enc_path.with_suffix("")

    with open(enc_path, "rb") as f:
        head = f.read(max(MAGIC_LEN, len(qsafe_backend.QSAFE_MAGIC)))

    if qsafe_backend.is_qsafe_data(head):
        if not qsafe_secret_key:
            raise ValueError(
                "File was encrypted with Qsafe but no qsafe_secret_key was provided. "
                "Set [ENCRYPTION] qsafe_secret_key to the secret key path."
            )
        tmp = out_path.with_name(out_path.name + ".tmp")
        try:
            qsafe_backend.decrypt_file_to(enc_path, tmp, qsafe_secret_key, passphrase)
            os.replace(tmp, out_path)
        finally:
            tmp.unlink(missing_ok=True)
        enc_path.unlink()
        return out_path

    if head[:MAGIC_LEN] == MAGIC_V2:
        _decrypt_v2_stream(enc_path, out_path, passphrase, key_file)
        enc_path.unlink()
        return out_path

    data = enc_path.read_bytes()
    if data[:MAGIC_LEN] == MAGIC:
        plaintext = _decrypt_v1(data, passphrase, key_file)
    else:
        plaintext = _decrypt_legacy(data, passphrase, key_file)

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
    backend: str = "aes",
    qsafe_recipients: list[str] | None = None,
) -> int:
    """
    Encrypt all eligible files in a directory tree.

    Skips files already ending in ``.enc``, backup manifest JSON files
    (needed for status/restore lookups without decryption keys), and their
    detached ``.sig`` signatures (must stay verifiable without decryption).
    """
    if backend == "qsafe":
        if not qsafe_recipients:
            raise ValueError(
                "Qsafe backend requires at least one recipient public key ([ENCRYPTION] qsafe_recipients)"
            )
        if not qsafe_backend.is_available():
            raise RuntimeError(
                "Qsafe backend selected but neither the qsafe Python bindings nor "
                "the qsafe CLI are available. Install Qsafe or set backend = aes."
            )
        if logger:
            logger.info(f"Qsafe engine: {qsafe_backend.engine_description()}")
    elif backend != "aes":
        raise ValueError(f"Unknown encryption backend: {backend!r}. Use 'aes' or 'qsafe'.")

    directory = Path(directory)
    files = [
        f
        for f in directory.rglob("*")
        if f.is_file()
        and f.suffix != ".enc"
        and not (
            f.name.startswith("backup_manifest_") and (f.suffix == ".json" or f.name.endswith(".json.sig"))
        )
    ]

    if not files:
        return 0

    encrypted = 0
    workers = max(1, workers)

    def _encrypt_one(file: Path) -> Path:
        encrypt_file(
            file,
            passphrase=passphrase,
            key_file=key_file,
            kdf=kdf,
            backend=backend,
            qsafe_recipients=qsafe_recipients,
        )
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
    qsafe_secret_key: str | None = None,
) -> int:
    """Decrypt all ``.enc`` files in a directory tree (AES or Qsafe, per-file)."""
    directory = Path(directory)
    files = [f for f in directory.rglob("*.enc") if f.is_file()]

    if not files:
        return 0

    decrypted = 0
    workers = max(1, workers)

    def _decrypt_one(file: Path) -> Path:
        decrypt_file(file, passphrase=passphrase, key_file=key_file, qsafe_secret_key=qsafe_secret_key)
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
