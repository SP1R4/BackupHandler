import os
import hashlib
from pathlib import Path


def _file_hash(file_path, chunk_size=8192):
    """Calculate SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def deduplicate_directory(logger, directory):
    """
    Deduplicate files within a single directory using hardlinks.
    Files with identical content (same SHA-256) are replaced with hardlinks
    to the first occurrence, saving disk space.

    Parameters:
    - logger: Logger instance.
    - directory (str or Path): Directory to deduplicate.

    Returns:
    - dict: Summary with 'files_checked', 'duplicates_found', 'bytes_saved'.
    """
    directory = Path(directory)
    if not directory.exists():
        logger.warning(f"Dedup directory does not exist: {directory}")
        return {'files_checked': 0, 'duplicates_found': 0, 'bytes_saved': 0}

    hash_to_path = {}  # hash -> first file path with that hash
    files_checked = 0
    duplicates_found = 0
    bytes_saved = 0

    for file in sorted(directory.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        # Skip manifests
        if file.name.startswith('backup_manifest_') and file.suffix == '.json':
            continue
        # Skip encrypted files (different nonce = different hash even for same content)
        if file.suffix == '.enc':
            continue

        files_checked += 1

        try:
            file_size = file.stat().st_size
            # Skip empty files
            if file_size == 0:
                continue

            # Check if this file already has multiple hardlinks (already deduped)
            if file.stat().st_nlink > 1:
                continue

            h = _file_hash(file)

            if h in hash_to_path:
                original = hash_to_path[h]
                # Verify the original still exists (could have been moved)
                if not original.exists():
                    hash_to_path[h] = file
                    continue

                # Verify they are not already hardlinks to each other
                if file.stat().st_ino == original.stat().st_ino:
                    continue

                # Replace duplicate with hardlink to original
                try:
                    file.unlink()
                    os.link(original, file)
                    duplicates_found += 1
                    bytes_saved += file_size
                    logger.debug(f"Dedup: hardlinked {file} -> {original}")
                except OSError as e:
                    logger.warning(f"Cannot hardlink {file} to {original}: {e}")
            else:
                hash_to_path[h] = file

        except Exception as e:
            logger.warning(f"Dedup error processing {file}: {e}")

    logger.info(f"Dedup in {directory}: {files_checked} checked, "
                f"{duplicates_found} duplicates hardlinked, "
                f"{bytes_saved} bytes saved")

    return {
        'files_checked': files_checked,
        'duplicates_found': duplicates_found,
        'bytes_saved': bytes_saved,
    }


def deduplicate_backup_dirs(logger, backup_dirs):
    """
    Run deduplication across all backup directories.
    Also deduplicates across directories if they share the same filesystem.

    Parameters:
    - logger: Logger instance.
    - backup_dirs (list): List of backup directory paths.

    Returns:
    - dict: Aggregate summary.
    """
    total = {'files_checked': 0, 'duplicates_found': 0, 'bytes_saved': 0}

    # First pass: dedup within each directory
    for bdir in backup_dirs:
        result = deduplicate_directory(logger, bdir)
        total['files_checked'] += result['files_checked']
        total['duplicates_found'] += result['duplicates_found']
        total['bytes_saved'] += result['bytes_saved']

    # Second pass: cross-directory dedup (only if on same filesystem)
    if len(backup_dirs) > 1:
        cross = _cross_directory_dedup(logger, backup_dirs)
        total['duplicates_found'] += cross['duplicates_found']
        total['bytes_saved'] += cross['bytes_saved']

    if total['bytes_saved'] >= 1048576:
        saved_str = f"{total['bytes_saved'] / 1048576:.2f} MB"
    elif total['bytes_saved'] >= 1024:
        saved_str = f"{total['bytes_saved'] / 1024:.2f} KB"
    else:
        saved_str = f"{total['bytes_saved']} B"

    logger.info(f"Dedup total: {total['duplicates_found']} duplicates, {saved_str} saved")
    return total


def _cross_directory_dedup(logger, backup_dirs):
    """Deduplicate identical files across directories using hardlinks (same filesystem only)."""
    result = {'duplicates_found': 0, 'bytes_saved': 0}

    # Group directories by filesystem device
    dev_groups = {}
    for bdir in backup_dirs:
        bpath = Path(bdir)
        if not bpath.exists():
            continue
        try:
            dev = bpath.stat().st_dev
            dev_groups.setdefault(dev, []).append(bpath)
        except OSError:
            continue

    for dev, dirs in dev_groups.items():
        if len(dirs) < 2:
            continue

        # Build hash index from first directory
        hash_index = {}
        for file in sorted(dirs[0].rglob('*')):
            if not file.is_file() or file.is_symlink():
                continue
            if file.name.startswith('backup_manifest_') and file.suffix == '.json':
                continue
            if file.suffix == '.enc':
                continue
            if file.stat().st_size == 0:
                continue
            try:
                h = _file_hash(file)
                hash_index[h] = file
            except Exception:
                continue

        # Check remaining directories against the index
        for other_dir in dirs[1:]:
            for file in sorted(other_dir.rglob('*')):
                if not file.is_file() or file.is_symlink():
                    continue
                if file.name.startswith('backup_manifest_') and file.suffix == '.json':
                    continue
                if file.suffix == '.enc':
                    continue
                if file.stat().st_size == 0:
                    continue

                try:
                    h = _file_hash(file)
                    if h in hash_index:
                        original = hash_index[h]
                        if file.stat().st_ino == original.stat().st_ino:
                            continue
                        file_size = file.stat().st_size
                        file.unlink()
                        os.link(original, file)
                        result['duplicates_found'] += 1
                        result['bytes_saved'] += file_size
                        logger.debug(f"Cross-dedup: hardlinked {file} -> {original}")
                    else:
                        hash_index[h] = file
                except Exception as e:
                    logger.warning(f"Cross-dedup error for {file}: {e}")

    return result
