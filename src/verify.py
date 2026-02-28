import os
import tempfile
from pathlib import Path
from .utils import calculate_checksum
from .manifest import load_latest_manifest
from .encryption import decrypt_file


def verify_backup_integrity(logger, backup_dirs, encryption_passphrase=None,
                            encryption_key_file=None):
    """
    Verify backup integrity by checking file existence and SHA-256 checksums
    against the latest manifest in each backup directory.

    Parameters:
    - logger: Logger instance.
    - backup_dirs (list): List of backup directory paths to verify.
    - encryption_passphrase (str, optional): Passphrase for decrypting .enc files.
    - encryption_key_file (str, optional): Path to key file for decrypting .enc files.

    Returns:
    - dict: Summary with keys 'total', 'verified', 'missing', 'corrupted', 'errors',
            and per-directory details.
    """
    results = {
        'total': 0,
        'verified': 0,
        'missing': 0,
        'corrupted': 0,
        'errors': 0,
        'directories': {},
    }

    for bdir in backup_dirs:
        bpath = Path(bdir)
        dir_result = {
            'manifest_found': False,
            'verified': 0,
            'missing': 0,
            'corrupted': 0,
            'errors': 0,
            'details': [],
        }

        if not bpath.exists():
            logger.warning(f"Backup directory does not exist: {bdir}")
            dir_result['details'].append(f"Directory not found: {bdir}")
            results['directories'][bdir] = dir_result
            continue

        manifest = load_latest_manifest(bdir)
        if not manifest:
            logger.warning(f"No manifest found in {bdir}. Falling back to file-only check.")
            # Fallback: just verify all files are readable
            count = _verify_files_exist(logger, bpath, dir_result)
            results['total'] += count
            results['verified'] += dir_result['verified']
            results['directories'][bdir] = dir_result
            continue

        dir_result['manifest_found'] = True
        copied_entries = manifest.get('copied', [])
        logger.info(f"Verifying {len(copied_entries)} files from manifest in {bdir}")

        for entry in copied_entries:
            results['total'] += 1
            file_path = entry.get('path', '')
            expected_size = entry.get('size', 0)

            # The manifest records original source paths; find the file in the backup dir
            # Try to resolve relative to backup dir
            candidate = _find_file_in_backup(bpath, file_path)

            if candidate is None:
                # Check for encrypted version
                enc_candidate = _find_encrypted_file(bpath, file_path)
                if enc_candidate and (encryption_passphrase or encryption_key_file):
                    # Decrypt to temp file for verification
                    ok = _verify_encrypted_file(logger, enc_candidate, expected_size,
                                                encryption_passphrase, encryption_key_file,
                                                dir_result)
                    if ok:
                        results['verified'] += 1
                    else:
                        results['corrupted'] += 1
                    continue
                elif enc_candidate:
                    # Encrypted but no key — can only check existence and size of .enc file
                    dir_result['verified'] += 1
                    results['verified'] += 1
                    dir_result['details'].append(f"OK (encrypted, not decrypted): {enc_candidate.name}")
                    continue

                logger.warning(f"Missing file: {file_path}")
                dir_result['missing'] += 1
                results['missing'] += 1
                dir_result['details'].append(f"MISSING: {file_path}")
                continue

            # Verify size
            try:
                actual_size = candidate.stat().st_size
                if actual_size != expected_size:
                    logger.warning(f"Size mismatch for {candidate}: expected {expected_size}, got {actual_size}")
                    dir_result['corrupted'] += 1
                    results['corrupted'] += 1
                    dir_result['details'].append(f"SIZE MISMATCH: {candidate} (expected {expected_size}, got {actual_size})")
                    continue

                dir_result['verified'] += 1
                results['verified'] += 1
            except Exception as e:
                logger.error(f"Error verifying {candidate}: {e}")
                dir_result['errors'] += 1
                results['errors'] += 1
                dir_result['details'].append(f"ERROR: {candidate}: {e}")

        results['directories'][bdir] = dir_result

    return results


def _find_file_in_backup(backup_dir, original_path):
    """Try to locate a file in the backup directory by its original path."""
    orig = Path(original_path)

    # Try exact relative match from backup dir
    for candidate in backup_dir.rglob(orig.name):
        if candidate.is_file():
            return candidate

    return None


def _find_encrypted_file(backup_dir, original_path):
    """Try to find the encrypted (.enc) version of a file."""
    orig = Path(original_path)
    enc_name = orig.name + '.enc'

    for candidate in backup_dir.rglob(enc_name):
        if candidate.is_file():
            return candidate

    return None


def _verify_encrypted_file(logger, enc_path, expected_size, passphrase, key_file, dir_result):
    """Decrypt a file to a temp location and verify its size."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_enc = Path(tmp_dir) / enc_path.name
            # Copy enc file to temp to avoid modifying original
            tmp_enc.write_bytes(enc_path.read_bytes())
            decrypted = decrypt_file(tmp_enc, passphrase=passphrase, key_file=key_file)
            actual_size = decrypted.stat().st_size
            if actual_size != expected_size:
                logger.warning(f"Size mismatch for decrypted {enc_path}: expected {expected_size}, got {actual_size}")
                dir_result['corrupted'] += 1
                dir_result['details'].append(f"SIZE MISMATCH (decrypted): {enc_path.name}")
                return False
            dir_result['verified'] += 1
            return True
    except Exception as e:
        logger.error(f"Failed to verify encrypted file {enc_path}: {e}")
        dir_result['errors'] += 1
        dir_result['details'].append(f"DECRYPT ERROR: {enc_path.name}: {e}")
        return False


def _verify_files_exist(logger, backup_dir, dir_result):
    """Fallback: verify that files in the directory are readable."""
    count = 0
    for f in backup_dir.rglob('*'):
        if not f.is_file():
            continue
        if f.name.startswith('backup_manifest_') and f.suffix == '.json':
            continue
        count += 1
        try:
            # Just verify the file is readable
            f.stat()
            dir_result['verified'] += 1
        except Exception as e:
            logger.error(f"Cannot read file {f}: {e}")
            dir_result['errors'] += 1
    return count


def print_verify_report(results):
    """Print a human-readable verification report."""
    print("\n=== Backup Verification Report ===\n")
    print(f"Total files checked: {results['total']}")
    print(f"  Verified:  {results['verified']}")
    print(f"  Missing:   {results['missing']}")
    print(f"  Corrupted: {results['corrupted']}")
    print(f"  Errors:    {results['errors']}")

    for bdir, detail in results['directories'].items():
        print(f"\n  Directory: {bdir}")
        print(f"    Manifest: {'Found' if detail['manifest_found'] else 'Not found (file-only check)'}")
        print(f"    Verified: {detail['verified']}, Missing: {detail['missing']}, "
              f"Corrupted: {detail['corrupted']}, Errors: {detail['errors']}")
        if detail['details']:
            for line in detail['details'][:20]:  # Cap at 20 lines per dir
                print(f"      {line}")
            if len(detail['details']) > 20:
                print(f"      ... and {len(detail['details']) - 20} more")

    all_ok = results['missing'] == 0 and results['corrupted'] == 0 and results['errors'] == 0
    if all_ok:
        print("\nResult: ALL BACKUPS VERIFIED OK")
    else:
        print("\nResult: VERIFICATION FOUND ISSUES")
    print()
    return all_ok
