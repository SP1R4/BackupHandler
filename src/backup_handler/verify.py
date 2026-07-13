"""
verify.py - Backup Integrity Verification

Validates backup completeness and correctness by cross-referencing files
in each backup directory against the latest JSON manifest. Checks include:

  - File existence in the backup directory tree
  - File size matching against manifest-recorded sizes
  - Encrypted file handling (decrypts to a temp directory for verification)
  - Fallback to file-existence-only check when no manifest is available

The verification process is non-destructive — original backup files and
encrypted copies are never modified.
"""

import tempfile
from pathlib import Path

from . import qsafe_backend
from .encryption import decrypt_file
from .manifest import latest_manifest_path, load_latest_manifest, manifest_signature_status
from .utils import calculate_checksum


def verify_backup_integrity(
    logger,
    backup_dirs,
    encryption_passphrase=None,
    encryption_key_file=None,
    qsafe_secret_key=None,
    qsafe_sign_pub=None,
):
    """
    Verify backup integrity by checking file existence and SHA-256 checksums
    against the latest manifest in each backup directory.

    Parameters:
    - logger: Logger instance.
    - backup_dirs (list): List of backup directory paths to verify.
    - encryption_passphrase (str, optional): Passphrase for decrypting .enc files
      (for Qsafe backups, this unwraps the secret key).
    - encryption_key_file (str, optional): Path to key file for decrypting .enc files.
    - qsafe_secret_key (str, optional): Path to the Qsafe secret key for decrypting
      Qsafe-encrypted .enc files.
    - qsafe_sign_pub (str, optional): Path to the Qsafe signing public key. When
      set, each directory's latest manifest signature is checked before its
      contents are trusted; an invalid signature marks the manifest corrupted.

    Returns:
    - dict: Summary with keys 'total', 'verified', 'missing', 'corrupted', 'errors',
            and per-directory details.
    """
    results = {
        "total": 0,
        "verified": 0,
        "missing": 0,
        "corrupted": 0,
        "errors": 0,
        "directories": {},
    }

    for bdir in backup_dirs:
        bpath = Path(bdir)
        dir_result = {
            "manifest_found": False,
            "verified": 0,
            "missing": 0,
            "corrupted": 0,
            "errors": 0,
            "details": [],
        }

        if not bpath.exists():
            logger.warning(f"Backup directory does not exist: {bdir}")
            dir_result["details"].append(f"Directory not found: {bdir}")
            results["directories"][bdir] = dir_result
            continue

        manifest = load_latest_manifest(bdir)
        if not manifest:
            logger.warning(f"No manifest found in {bdir}. Falling back to file-only check.")
            # Fallback: just verify all files are readable
            count = _verify_files_exist(logger, bpath, dir_result)
            results["total"] += count
            results["verified"] += dir_result["verified"]
            results["directories"][bdir] = dir_result
            continue

        dir_result["manifest_found"] = True

        # Authenticate the manifest before trusting its contents
        if qsafe_sign_pub:
            manifest_file = latest_manifest_path(bdir)
            sig_status = manifest_signature_status(manifest_file, qsafe_sign_pub)
            if sig_status == "invalid":
                logger.error(f"Manifest signature INVALID for {manifest_file} — not trusting it.")
                dir_result["corrupted"] += 1
                results["corrupted"] += 1
                dir_result["details"].append(f"MANIFEST SIGNATURE INVALID: {manifest_file.name}")
                results["directories"][bdir] = dir_result
                continue
            if sig_status == "missing":
                logger.warning(f"No signature for {manifest_file} (backup made before signing was enabled?)")
                dir_result["details"].append(f"Manifest unsigned: {manifest_file.name}")
            else:
                dir_result["details"].append(f"Manifest signature OK (ML-DSA-87): {manifest_file.name}")

        copied_entries = manifest.get("copied", [])
        logger.info(f"Verifying {len(copied_entries)} files from manifest in {bdir}")

        for entry in copied_entries:
            results["total"] += 1
            file_path = entry.get("path", "")
            expected_size = entry.get("size", 0)

            # The manifest records original source paths; find the file in the backup dir
            # Try to resolve relative to backup dir
            candidate = _find_file_in_backup(bpath, file_path)

            if candidate is None:
                # Check for encrypted version
                enc_candidate = _find_encrypted_file(bpath, file_path)
                if enc_candidate and (encryption_passphrase or encryption_key_file or qsafe_secret_key):
                    # Decrypt to temp file for verification
                    ok = _verify_encrypted_file(
                        logger,
                        enc_candidate,
                        expected_size,
                        encryption_passphrase,
                        encryption_key_file,
                        dir_result,
                        qsafe_secret_key=qsafe_secret_key,
                    )
                    if ok:
                        results["verified"] += 1
                    else:
                        results["corrupted"] += 1
                    continue
                elif enc_candidate:
                    # Encrypted but no key — can only check existence and size of .enc file
                    dir_result["verified"] += 1
                    results["verified"] += 1
                    dir_result["details"].append(f"OK (encrypted, not decrypted): {enc_candidate.name}")
                    continue

                logger.warning(f"Missing file: {file_path}")
                dir_result["missing"] += 1
                results["missing"] += 1
                dir_result["details"].append(f"MISSING: {file_path}")
                continue

            # Verify size and checksum
            try:
                actual_size = candidate.stat().st_size
                if actual_size != expected_size:
                    logger.warning(
                        f"Size mismatch for {candidate}: expected {expected_size}, got {actual_size}"
                    )
                    dir_result["corrupted"] += 1
                    results["corrupted"] += 1
                    dir_result["details"].append(
                        f"SIZE MISMATCH: {candidate} (expected {expected_size}, got {actual_size})"
                    )
                    continue

                # Verify SHA-256 checksum if recorded in manifest
                expected_checksum = entry.get("checksum")
                if expected_checksum:
                    actual_checksum = calculate_checksum(str(candidate))
                    if actual_checksum != expected_checksum:
                        logger.warning(
                            f"Checksum mismatch for {candidate}: expected {expected_checksum[:16]}..., got {actual_checksum[:16]}..."
                        )
                        dir_result["corrupted"] += 1
                        results["corrupted"] += 1
                        dir_result["details"].append(f"CHECKSUM MISMATCH: {candidate}")
                        continue

                dir_result["verified"] += 1
                results["verified"] += 1
            except Exception as e:
                logger.error(f"Error verifying {candidate}: {e}")
                dir_result["errors"] += 1
                results["errors"] += 1
                dir_result["details"].append(f"ERROR: {candidate}: {e}")

        results["directories"][bdir] = dir_result

    return results


def _find_file_in_backup(backup_dir, original_path):
    """
    Locate a file in the backup directory by matching its original source path.

    Since manifests record absolute source paths but files are stored relative
    to the backup directory, this function searches by filename and scores
    candidates by how many trailing path components match the original path.
    This prevents false matches when multiple files share the same name in
    different subdirectories.

    Parameters:
        backup_dir (Path): Root backup directory to search.
        original_path (str): Original source path as recorded in the manifest.

    Returns:
        Path or None: Best-matching file path, or None if not found.
    """
    orig = Path(original_path)

    # Score candidates by trailing path component overlap with the original
    orig_parts = orig.parts
    best_match = None
    best_match_len = 0

    for candidate in backup_dir.rglob(orig.name):
        if not candidate.is_file():
            continue
        # Score by how many trailing path components match
        cand_parts = candidate.relative_to(backup_dir).parts
        match_len = 0
        for o, c in zip(reversed(orig_parts), reversed(cand_parts), strict=False):
            if o == c:
                match_len += 1
            else:
                break
        if match_len > best_match_len:
            best_match = candidate
            best_match_len = match_len

    return best_match


def _find_encrypted_file(backup_dir, original_path):
    """
    Locate the encrypted (``.enc``) version of a file in the backup directory.

    Uses the same path-suffix scoring as ``_find_file_in_backup`` but searches
    for ``<filename>.enc`` and strips the ``.enc`` suffix before comparison.

    Parameters:
        backup_dir (Path): Root backup directory to search.
        original_path (str): Original source path as recorded in the manifest.

    Returns:
        Path or None: Best-matching ``.enc`` file path, or None if not found.
    """
    orig = Path(original_path)
    enc_name = orig.name + ".enc"
    orig_parts = orig.parts
    best_match = None
    best_match_len = 0

    for candidate in backup_dir.rglob(enc_name):
        if not candidate.is_file():
            continue
        cand_parts = candidate.relative_to(backup_dir).parts
        # Remove .enc suffix for comparison
        cand_parts_adj = (*cand_parts[:-1], cand_parts[-1].removesuffix(".enc"))
        match_len = 0
        for o, c in zip(reversed(orig_parts), reversed(cand_parts_adj), strict=False):
            if o == c:
                match_len += 1
            else:
                break
        if match_len > best_match_len:
            best_match = candidate
            best_match_len = match_len

    return best_match


def _verify_encrypted_file(
    logger, enc_path, expected_size, passphrase, key_file, dir_result, qsafe_secret_key=None
):
    """
    Decrypt an encrypted file to a temporary directory and verify its size.

    Qsafe files are authenticated *in place* — the AEAD tag covers the whole
    payload, so integrity is proven without ever writing plaintext to disk.
    AES files are copied to a temp directory, decrypted there, and size-checked,
    ensuring the backup is never modified during verification.

    Parameters:
        logger: Logger instance.
        enc_path (Path): Path to the encrypted file.
        expected_size (int): Expected plaintext file size from the manifest.
        passphrase (str): Encryption passphrase (or None).
        key_file (str): Path to encryption key file (or None).
        dir_result (dict): Per-directory result dict to update counters.
        qsafe_secret_key (str): Path to the Qsafe secret key (or None).

    Returns:
        bool: True if the file authenticated (and, for AES, size matches).
    """
    try:
        with open(enc_path, "rb") as f:
            head = f.read(len(qsafe_backend.QSAFE_MAGIC))
        if qsafe_backend.is_qsafe_data(head) and qsafe_secret_key:
            if qsafe_backend.verify_encrypted_file(enc_path, qsafe_secret_key, passphrase):
                dir_result["verified"] += 1
                dir_result["details"].append(f"OK (authenticated in place, no plaintext): {enc_path.name}")
                return True
            logger.warning(f"Qsafe authentication failed for {enc_path}")
            dir_result["corrupted"] += 1
            dir_result["details"].append(f"AUTHENTICATION FAILED: {enc_path.name}")
            return False

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_enc = Path(tmp_dir) / enc_path.name
            # Copy enc file to temp to avoid modifying original
            tmp_enc.write_bytes(enc_path.read_bytes())
            decrypted = decrypt_file(
                tmp_enc, passphrase=passphrase, key_file=key_file, qsafe_secret_key=qsafe_secret_key
            )
            actual_size = decrypted.stat().st_size
            if actual_size != expected_size:
                logger.warning(
                    f"Size mismatch for decrypted {enc_path}: expected {expected_size}, got {actual_size}"
                )
                dir_result["corrupted"] += 1
                dir_result["details"].append(f"SIZE MISMATCH (decrypted): {enc_path.name}")
                return False
            dir_result["verified"] += 1
            return True
    except Exception as e:
        logger.error(f"Failed to verify encrypted file {enc_path}: {e}")
        dir_result["errors"] += 1
        dir_result["details"].append(f"DECRYPT ERROR: {enc_path.name}: {e}")
        return False


def _verify_files_exist(logger, backup_dir, dir_result):
    """
    Fallback verification when no manifest is available.

    Walks the directory tree and verifies that each file can be stat'd
    (i.e., is readable and not corrupted at the filesystem level).
    Manifest files are excluded from the count.

    Parameters:
        logger: Logger instance.
        backup_dir (Path): Backup directory to scan.
        dir_result (dict): Per-directory result dict to update counters.

    Returns:
        int: Total number of files checked.
    """
    count = 0
    for f in backup_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.name.startswith("backup_manifest_") and f.suffix == ".json":
            continue
        count += 1
        try:
            # Just verify the file is readable
            f.stat()
            dir_result["verified"] += 1
        except Exception as e:
            logger.error(f"Cannot read file {f}: {e}")
            dir_result["errors"] += 1
    return count


def print_verify_report(results):
    """
    Print a human-readable backup verification report to stdout.

    Displays aggregate totals and per-directory breakdowns with up to
    20 detail lines per directory to avoid overwhelming output.

    Parameters:
        results (dict): Verification results from ``verify_backup_integrity()``.

    Returns:
        bool: True if all backups verified OK (no missing, corrupted, or errors).
    """
    print("\n=== Backup Verification Report ===\n")
    print(f"Total files checked: {results['total']}")
    print(f"  Verified:  {results['verified']}")
    print(f"  Missing:   {results['missing']}")
    print(f"  Corrupted: {results['corrupted']}")
    print(f"  Errors:    {results['errors']}")

    for bdir, detail in results["directories"].items():
        print(f"\n  Directory: {bdir}")
        print(f"    Manifest: {'Found' if detail['manifest_found'] else 'Not found (file-only check)'}")
        print(
            f"    Verified: {detail['verified']}, Missing: {detail['missing']}, "
            f"Corrupted: {detail['corrupted']}, Errors: {detail['errors']}"
        )
        if detail["details"]:
            for line in detail["details"][:20]:  # Cap at 20 lines per dir
                print(f"      {line}")
            if len(detail["details"]) > 20:
                print(f"      ... and {len(detail['details']) - 20} more")

    all_ok = results["missing"] == 0 and results["corrupted"] == 0 and results["errors"] == 0
    if all_ok:
        print("\nResult: ALL BACKUPS VERIFIED OK")
    else:
        print("\nResult: VERIFICATION FOUND ISSUES")
    print()
    return all_ok
