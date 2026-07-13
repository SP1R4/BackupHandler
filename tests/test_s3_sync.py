"""Unit tests for s3_sync helpers — mocked, no real AWS calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

from backup_handler.s3_sync import STALE_MULTIPART_HOURS, _abort_stale_multipart_uploads


class TestAbortStaleMultipartUploads:
    def test_aborts_only_old_uploads(self, logger):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=STALE_MULTIPART_HOURS + 1)
        fresh = now - timedelta(hours=1)

        s3 = mock.MagicMock()
        paginator = mock.MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Uploads": [
                    {"Key": "old-key", "UploadId": "old-id", "Initiated": old},
                    {"Key": "fresh-key", "UploadId": "fresh-id", "Initiated": fresh},
                ]
            }
        ]

        aborted = _abort_stale_multipart_uploads(s3, "my-bucket", "prefix/", logger)

        assert aborted == 1
        s3.abort_multipart_upload.assert_called_once_with(
            Bucket="my-bucket", Key="old-key", UploadId="old-id"
        )

    def test_swallows_list_errors(self, logger):
        s3 = mock.MagicMock()
        s3.get_paginator.side_effect = RuntimeError("perms denied")
        # Must not raise — the sweep is best-effort.
        result = _abort_stale_multipart_uploads(s3, "b", "", logger)
        assert result == 0

    def test_swallows_per_upload_abort_errors(self, logger):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=STALE_MULTIPART_HOURS + 1)
        s3 = mock.MagicMock()
        paginator = mock.MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{"Uploads": [{"Key": "k", "UploadId": "u", "Initiated": old}]}]
        s3.abort_multipart_upload.side_effect = RuntimeError("nope")

        # No exception; the failed abort is logged at warning level.
        result = _abort_stale_multipart_uploads(s3, "b", "", logger)
        assert result == 0
