"""
ssh_client.py - Hardened paramiko SSHClient factory with host-key pinning.

Centralizes SSH client construction so every backup/restore path uses the
same strict policy: known hosts come from a configured ``known_hosts`` file
(or the user's ``~/.ssh/known_hosts``), and unknown hosts cause a refusal
with an actionable error message — never silent acceptance.

Trust-on-first-use is deliberately disabled. For a backup tool, accepting an
unverified key on the wire would let a MITM observe every byte of the backup.
Operators provision host keys ahead of time via ``ssh-keyscan -H``.
"""

from __future__ import annotations

import os
from pathlib import Path

import paramiko

DEFAULT_KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"


class UnknownHostKeyError(RuntimeError):
    """Raised when a remote host's key is not in any known_hosts source."""


def _load_known_hosts(client: paramiko.SSHClient, known_hosts_path: str | None, logger) -> None:
    """Populate the client's host-key store from the given path and the system store."""
    explicit_path = Path(known_hosts_path) if known_hosts_path else DEFAULT_KNOWN_HOSTS
    if explicit_path.exists():
        client.load_host_keys(str(explicit_path))
        if logger:
            logger.debug(f"Loaded known_hosts from {explicit_path}")
    elif logger:
        logger.warning(
            f"known_hosts file not found at {explicit_path}. "
            f"Connections will fail until host keys are added "
            f"(use: ssh-keyscan -H <host> >> {explicit_path})."
        )
    # Also load the user's system store (covers OpenSSH config style locations).
    try:
        client.load_system_host_keys()
    except OSError:
        pass


def build_ssh_client(known_hosts_path: str | None = None, logger=None) -> paramiko.SSHClient:
    """
    Build a paramiko SSHClient with strict host-key checking.

    Parameters:
        known_hosts_path: Path to a known_hosts file. Defaults to
            ``~/.ssh/known_hosts``. The system store is also consulted.
        logger: Optional logger for debug/warning messages.

    Returns:
        Configured paramiko.SSHClient with RejectPolicy installed.
    """
    client = paramiko.SSHClient()
    _load_known_hosts(client, known_hosts_path, logger)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client


def explain_host_key_failure(host: str, known_hosts_path: str | None = None) -> str:
    """
    Build a user-facing error message for an unknown-host-key failure.

    Returns guidance on how to add the host's key to known_hosts.
    """
    path = known_hosts_path or os.fspath(DEFAULT_KNOWN_HOSTS)
    return (
        f"Host key for {host!r} is not in {path}. "
        f"To trust this host, run: ssh-keyscan -H {host} >> {path} "
        f"(verify the fingerprint out-of-band before doing so)."
    )
