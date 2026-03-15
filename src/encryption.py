"""
encryption.py - AES-256-GCM Encryption at Rest

Provides file-level encryption and decryption for backup data using
AES-256-GCM authenticated encryption. Supports two key sources:

  1. Key file   - A raw 32-byte key read directly from disk.
  2. Passphrase - Derived via PBKDF2-HMAC-SHA256 with 600,000 iterations.

Encrypted file format (binary):
    [16 bytes salt][12 bytes nonce][ciphertext + 16 bytes GCM auth tag]

When a key file is used, the salt field is zeroed out (unused on decrypt).
"""

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# ─── Cryptographic constants ────────────────────────────────────────────────
PBKDF2_ITERATIONS = 600_000   # OWASP-recommended minimum for HMAC-SHA256
SALT_SIZE = 16                # 128-bit random salt per file
NONCE_SIZE = 12               # 96-bit nonce (standard for AES-GCM)
KEY_SIZE = 32                 # 256-bit AES key


def derive_key(passphrase, salt):
    """Derive a 32-byte AES key from a passphrase using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode('utf-8'))


def load_key_file(path):
    """Read a raw 32-byte key from a file."""
    key_path = Path(path)
    if not key_path.exists():
        raise FileNotFoundError(f"Key file not found: {path}")
    key = key_path.read_bytes()
    if len(key) != KEY_SIZE:
        raise ValueError(f"Key file must be exactly {KEY_SIZE} bytes, got {len(key)}")
    return key


def get_encryption_key(passphrase=None, key_file=None, salt=None):
    """
    Get encryption key from either a key file or passphrase.
    Key file takes priority if both are provided.
    Returns (key_bytes, salt) — salt is None when using key_file.
    """
    if key_file:
        return load_key_file(key_file), None
    if passphrase:
        if salt is None:
            salt = os.urandom(SALT_SIZE)
        return derive_key(passphrase, salt), salt
    raise ValueError("Either passphrase or key_file must be provided for encryption")


def encrypt_file(path, passphrase=None, key_file=None):
    """
    Encrypt a single file with AES-256-GCM.

    Writes an encrypted copy at ``<original>.enc`` and deletes the plaintext original.
    Each file gets a unique random nonce to ensure ciphertext uniqueness even for
    identical plaintext inputs.

    File format: [16B salt][12B nonce][ciphertext + GCM tag]
    When using key_file, salt bytes are written as zeros (ignored on decrypt).

    Parameters:
        path (str or Path): File to encrypt.
        passphrase (str, optional): Passphrase for PBKDF2 key derivation.
        key_file (str, optional): Path to a 32-byte raw key file.

    Returns:
        Path: Path to the newly created ``.enc`` file.

    Raises:
        ValueError: If neither passphrase nor key_file is provided.
    """
    path = Path(path)
    plaintext = path.read_bytes()

    key, salt = get_encryption_key(passphrase=passphrase, key_file=key_file)
    if salt is None:
        salt = b'\x00' * SALT_SIZE  # Placeholder when using key file

    # Generate a unique nonce for this file (never reuse with the same key)
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Write encrypted output and remove plaintext original
    enc_path = path.with_name(path.name + '.enc')
    enc_path.write_bytes(salt + nonce + ciphertext)
    path.unlink()
    return enc_path


def decrypt_file(enc_path, passphrase=None, key_file=None):
    """
    Decrypt a ``.enc`` file back to its original plaintext.

    Reads the encrypted file, extracts the salt and nonce from the header,
    decrypts and verifies the GCM authentication tag, then writes the
    plaintext to the original filename (minus ``.enc``) and deletes the
    encrypted copy.

    Parameters:
        enc_path (str or Path): Path to the encrypted ``.enc`` file.
        passphrase (str, optional): Passphrase for PBKDF2 key derivation.
        key_file (str, optional): Path to a 32-byte raw key file.

    Returns:
        Path: Path to the decrypted plaintext file.

    Raises:
        cryptography.exceptions.InvalidTag: If the file is corrupted or the wrong key is used.
    """
    enc_path = Path(enc_path)
    data = enc_path.read_bytes()

    # Parse the binary header: [salt][nonce][ciphertext+tag]
    salt = data[:SALT_SIZE]
    nonce = data[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
    ciphertext = data[SALT_SIZE + NONCE_SIZE:]

    if key_file:
        key = load_key_file(key_file)
    else:
        key = derive_key(passphrase, salt)

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    # Restore the original filename by stripping the .enc suffix
    if enc_path.name.endswith('.enc'):
        out_path = enc_path.with_name(enc_path.name[:-4])
    else:
        out_path = enc_path.with_suffix('')

    out_path.write_bytes(plaintext)
    enc_path.unlink()
    return out_path


def encrypt_directory(directory, passphrase=None, key_file=None, logger=None, workers=1):
    """
    Encrypt all eligible files in a directory tree using AES-256-GCM.

    Skips files that are already encrypted (``.enc``) and backup manifest
    JSON files (needed for status/restore lookups without decryption).

    Parameters:
        directory (str or Path): Root directory to encrypt recursively.
        passphrase (str, optional): Passphrase for key derivation.
        key_file (str, optional): Path to a 32-byte raw key file.
        logger (logging.Logger, optional): Logger for progress/error reporting.
        workers (int): Number of parallel encryption threads (default: 1).

    Returns:
        int: Number of files successfully encrypted.
    """
    directory = Path(directory)
    files = [f for f in directory.rglob('*')
             if f.is_file() and f.suffix != '.enc'
             and not (f.name.startswith('backup_manifest_') and f.suffix == '.json')]

    if not files:
        return 0

    encrypted = 0
    workers = max(1, workers)

    def _encrypt_one(file):
        encrypt_file(file, passphrase=passphrase, key_file=key_file)
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
            for future in tqdm(as_completed(futures), total=len(futures),
                               desc="Encrypting files", unit="files"):
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


def decrypt_directory(directory, passphrase=None, key_file=None, logger=None, workers=1):
    """
    Decrypt all ``.enc`` files in a directory tree.

    Parameters:
        directory (str or Path): Root directory to decrypt recursively.
        passphrase (str, optional): Passphrase for key derivation.
        key_file (str, optional): Path to a 32-byte raw key file.
        logger (logging.Logger, optional): Logger for progress/error reporting.
        workers (int): Number of parallel decryption threads (default: 1).

    Returns:
        int: Number of files successfully decrypted.
    """
    directory = Path(directory)
    files = [f for f in directory.rglob('*.enc') if f.is_file()]

    if not files:
        return 0

    decrypted = 0
    workers = max(1, workers)

    def _decrypt_one(file):
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
            for future in tqdm(as_completed(futures), total=len(futures),
                               desc="Decrypting files", unit="files"):
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
