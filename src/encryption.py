import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # 256 bits


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
    Encrypt a file with AES-256-GCM. Writes path.enc and deletes the original.
    File format: [16B salt][12B nonce][ciphertext + GCM tag]
    When using key_file, salt bytes are written as zeros (ignored on decrypt).
    """
    path = Path(path)
    plaintext = path.read_bytes()

    key, salt = get_encryption_key(passphrase=passphrase, key_file=key_file)
    if salt is None:
        salt = b'\x00' * SALT_SIZE  # placeholder when using key file

    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    enc_path = path.with_name(path.name + '.enc')
    enc_path.write_bytes(salt + nonce + ciphertext)
    path.unlink()
    return enc_path


def decrypt_file(enc_path, passphrase=None, key_file=None):
    """
    Decrypt a .enc file. Writes the original filename (without .enc) and deletes the .enc file.
    """
    enc_path = Path(enc_path)
    data = enc_path.read_bytes()

    salt = data[:SALT_SIZE]
    nonce = data[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
    ciphertext = data[SALT_SIZE + NONCE_SIZE:]

    if key_file:
        key = load_key_file(key_file)
    else:
        key = derive_key(passphrase, salt)

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    # Remove .enc suffix for output filename
    if enc_path.name.endswith('.enc'):
        out_path = enc_path.with_name(enc_path.name[:-4])
    else:
        out_path = enc_path.with_suffix('')

    out_path.write_bytes(plaintext)
    enc_path.unlink()
    return out_path


def encrypt_directory(directory, passphrase=None, key_file=None, logger=None):
    """Encrypt all files in a directory tree, skipping .enc files and manifest JSONs."""
    directory = Path(directory)
    encrypted = 0
    for file in directory.rglob('*'):
        if not file.is_file():
            continue
        if file.suffix == '.enc':
            continue
        if file.name.startswith('backup_manifest_') and file.suffix == '.json':
            continue
        try:
            encrypt_file(file, passphrase=passphrase, key_file=key_file)
            encrypted += 1
            if logger:
                logger.debug(f"Encrypted: {file}")
        except Exception as e:
            if logger:
                logger.error(f"Failed to encrypt {file}: {e}")
    if logger:
        logger.info(f"Encrypted {encrypted} files in {directory}")
    return encrypted


def decrypt_directory(directory, passphrase=None, key_file=None, logger=None):
    """Decrypt all .enc files in a directory tree."""
    directory = Path(directory)
    decrypted = 0
    for file in directory.rglob('*.enc'):
        if not file.is_file():
            continue
        try:
            decrypt_file(file, passphrase=passphrase, key_file=key_file)
            decrypted += 1
            if logger:
                logger.debug(f"Decrypted: {file}")
        except Exception as e:
            if logger:
                logger.error(f"Failed to decrypt {file}: {e}")
    if logger:
        logger.info(f"Decrypted {decrypted} files in {directory}")
    return decrypted
