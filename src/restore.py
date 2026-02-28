import os
import shutil
import zipfile
from pathlib import Path
from .utils import verify_backup
from .manifest import load_manifests_up_to


def restore_backup(logger, from_dir, to_dir, timestamp=None):
    """
    Restore files from a backup directory or ZIP archive.

    Parameters:
    - logger: Logger instance.
    - from_dir (str): Source backup directory or ZIP archive path.
    - to_dir (str): Destination directory to restore files to.
    - timestamp (str, optional): Restore to a specific point in time (YYYYMMDD_HHMMSS).
        When provided, reads manifests up to that timestamp and applies files in order.

    Returns:
    - bool: True if restore completed successfully, False otherwise.
    """
    from_path = Path(from_dir)
    to_path = Path(to_dir)

    if not from_path.exists():
        logger.error(f"Restore source does not exist: {from_dir}")
        return False

    to_path.mkdir(parents=True, exist_ok=True)

    # ZIP archive restore
    if from_path.is_file() and from_path.suffix == '.zip':
        return _restore_from_zip(logger, from_path, to_path)

    # Directory restore
    if from_path.is_dir():
        if timestamp:
            return _restore_with_manifests(logger, from_path, to_path, timestamp)
        else:
            return _restore_full_directory(logger, from_path, to_path)

    logger.error(f"Unsupported restore source: {from_dir}")
    return False


def _restore_from_zip(logger, zip_path, to_dir):
    """Extract a ZIP archive to the destination directory."""
    logger.info(f"Restoring from ZIP archive: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(to_dir)
        logger.info(f"Successfully restored {zip_path} to {to_dir}")
        return True
    except zipfile.BadZipFile:
        logger.error(f"Bad ZIP file: {zip_path}")
        return False
    except Exception as e:
        logger.error(f"Failed to restore from ZIP: {e}")
        return False


def _restore_full_directory(logger, from_dir, to_dir):
    """Full reverse copy from backup directory to destination with verification."""
    logger.info(f"Restoring full directory: {from_dir} -> {to_dir}")
    files = [f for f in from_dir.rglob('*') if f.is_file()
             and not (f.name.startswith('backup_manifest_') and f.suffix == '.json')]

    copied = 0
    failed = 0

    for file in files:
        relative = file.relative_to(from_dir)
        dest_file = to_dir / relative

        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            if file.is_symlink():
                link_target = os.readlink(file)
                if dest_file.exists() or dest_file.is_symlink():
                    dest_file.unlink()
                os.symlink(link_target, dest_file)
                copied += 1
                continue

            shutil.copy2(file, dest_file)

            if verify_backup(file, dest_file):
                logger.info(f"Restored: {relative}")
                copied += 1
            else:
                logger.error(f"Checksum verification failed during restore: {relative}")
                failed += 1
        except Exception as e:
            logger.error(f"Failed to restore {relative}: {e}")
            failed += 1

    logger.info(f"Restore complete: {copied} files restored, {failed} failures")
    return failed == 0


def _restore_with_manifests(logger, from_dir, to_dir, timestamp):
    """
    Restore using manifests up to a specific timestamp.
    Applies manifests in chronological order to reconstruct state at that point.
    """
    logger.info(f"Restoring with manifests up to timestamp: {timestamp}")
    manifests = load_manifests_up_to(from_dir, timestamp)

    if not manifests:
        logger.warning(f"No manifests found up to timestamp {timestamp}. Falling back to full directory restore.")
        return _restore_full_directory(logger, from_dir, to_dir)

    logger.info(f"Found {len(manifests)} manifest(s) to apply")

    # Collect all files that were copied (latest version wins)
    files_to_restore = {}
    for manifest in manifests:
        for entry in manifest.get('copied', []):
            files_to_restore[entry['path']] = entry

    copied = 0
    failed = 0

    for file_path, entry in files_to_restore.items():
        src = Path(file_path)
        # Try to find the file in the backup directory structure
        # The manifest records the original source path; the file is stored
        # relative to from_dir
        if src.is_absolute():
            # Try to find it relative to from_dir
            for candidate_base in [from_dir]:
                # Try matching by filename/relative path patterns
                matches = list(candidate_base.rglob(src.name))
                if matches:
                    src = matches[0]
                    break

        if not src.exists():
            logger.warning(f"Source file not found for restore: {file_path}")
            failed += 1
            continue

        # Determine destination
        try:
            relative = src.relative_to(from_dir)
        except ValueError:
            relative = Path(src.name)

        dest_file = to_dir / relative

        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_file)

            if verify_backup(src, dest_file):
                logger.info(f"Restored: {relative}")
                copied += 1
            else:
                logger.error(f"Checksum verification failed: {relative}")
                failed += 1
        except Exception as e:
            logger.error(f"Failed to restore {relative}: {e}")
            failed += 1

    logger.info(f"Manifest-based restore complete: {copied} files restored, {failed} failures")
    return failed == 0
