"""Single source of truth for the backup_handler version.

The version string is read by :mod:`src.argparse_setup` for ``--version`` and
by ``pyproject.toml`` (via hatchling) when building distribution artifacts.
"""

__version__ = "2.5.0"
