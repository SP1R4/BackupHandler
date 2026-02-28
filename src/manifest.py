import json
import time
from pathlib import Path
from datetime import datetime


class BackupManifest:
    """
    Records per-file backup operations and writes a summary manifest JSON.

    Usage:
        manifest = BackupManifest(mode='full')
        manifest.record_copy('/path/to/file', 1024)
        manifest.record_skip('/path/to/unchanged')
        manifest.record_failure('/path/to/bad', 'permission denied')
        manifest.save('/backups/daily')
    """

    def __init__(self, mode='full'):
        self._start_time = time.time()
        self._mode = mode
        self._copied = []
        self._skipped = []
        self._failed = []
        self._total_bytes = 0

    def record_copy(self, file_path, size_bytes):
        """Record a successfully copied file."""
        self._copied.append({'path': str(file_path), 'size': size_bytes})
        self._total_bytes += size_bytes

    def record_skip(self, file_path):
        """Record a skipped (unchanged) file."""
        self._skipped.append({'path': str(file_path)})

    def record_failure(self, file_path, reason):
        """Record a failed file operation."""
        self._failed.append({'path': str(file_path), 'reason': reason})

    def save(self, output_dir):
        """
        Write the manifest JSON to output_dir.

        Parameters:
        - output_dir (str or Path): Directory to write the manifest file.

        Returns:
        - Path: Path to the written manifest file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        duration = time.time() - self._start_time
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        manifest_data = {
            'timestamp': timestamp,
            'mode': self._mode,
            'duration_seconds': round(duration, 2),
            'files_copied': len(self._copied),
            'files_skipped': len(self._skipped),
            'files_failed': len(self._failed),
            'total_bytes': self._total_bytes,
            'copied': self._copied,
            'skipped': self._skipped,
            'failed': self._failed,
        }

        manifest_path = output_dir / f'backup_manifest_{timestamp}.json'
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f, indent=2)

        return manifest_path

    def summary(self):
        """Return a summary dict (without per-file details)."""
        duration = time.time() - self._start_time
        return {
            'mode': self._mode,
            'duration_seconds': round(duration, 2),
            'files_copied': len(self._copied),
            'files_skipped': len(self._skipped),
            'files_failed': len(self._failed),
            'total_bytes': self._total_bytes,
        }


def load_latest_manifest(directory):
    """
    Load the most recent backup manifest from a directory.

    Parameters:
    - directory (str or Path): Directory to search for manifest files.

    Returns:
    - dict or None: Parsed manifest data, or None if no manifest found.
    """
    directory = Path(directory)
    manifests = sorted(directory.glob('backup_manifest_*.json'), reverse=True)
    if not manifests:
        return None
    with open(manifests[0], 'r') as f:
        return json.load(f)


def load_manifests_up_to(directory, timestamp):
    """
    Load all manifests up to (and including) the given timestamp, sorted chronologically.

    Parameters:
    - directory (str or Path): Directory containing manifest files.
    - timestamp (str): Cutoff timestamp in YYYYMMDD_HHMMSS format.

    Returns:
    - list of dict: Manifests sorted oldest-first.
    """
    directory = Path(directory)
    manifests = []
    for manifest_file in sorted(directory.glob('backup_manifest_*.json')):
        # Extract timestamp from filename
        name = manifest_file.stem  # backup_manifest_YYYYMMDD_HHMMSS
        parts = name.replace('backup_manifest_', '')
        if parts <= timestamp:
            with open(manifest_file, 'r') as f:
                data = json.load(f)
                data['_manifest_path'] = str(manifest_file)
                manifests.append(data)
    return manifests
