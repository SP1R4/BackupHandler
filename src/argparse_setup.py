import sys
import argparse
from src.utils import is_valid_email


def setup_argparse():
    """
    Set up and  parse command-line arguments for the backup handler script.

    Returns:
    - argparse.Namespace: A namespace populated with command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Backup Handler Script for managing backups and notifications."
    )

    # Version
    parser.add_argument(
        '--version',
        action='version',
        version='backup-handler 1.4.0'
    )
    
    # Configuration file argument
    parser.add_argument(
        '--config', 
        type=str, 
        default='config/config.ini', 
        help='Path to the configuration file (default: config.ini)'
    )
    
    # Operation Modes Selection
    parser.add_argument(
        '--operation-modes', 
        nargs='+', 
        choices=['local', 'ssh'], 
        default=['local'], 
        help='Select operation modes to run (default: local). Choices: local, ssh'
    )
    
    # Directory and server overrides
    parser.add_argument(
        '--source-dir',
        type=str,
        help='Override the source directory from the configuration'
    )
    parser.add_argument( 
        '--backup-dirs', 
        nargs='+', 
        help='Override the backup directories (space-separated list)'
    )
    parser.add_argument(
        '--ssh-servers', 
        nargs='+', 
        help='Override the SSH servers for remote backups (space-separated list)'
    )
    
    # Backup behavior options
    parser.add_argument(
        '--backup-mode', 
        type=str, 
        choices=['full', 'incremental', 'differential'], 
        help='Specify the type of backup. Choices: full, incremental, differential'
    )
    parser.add_argument(
        '--show-setup', 
        action='store_true', 
        help='Display the current backup configuration and settings'
    )
    
    # Compression options
    parser.add_argument(
        '--compress', 
        type=str, 
        choices=['zip', 'zip_pw'], 
        help="Compress the source directory. Choices: 'zip' (normal zip), 'zip_pw' (password-protected zip)"
    )
    
    # Scheduling options
    parser.add_argument(
        '--scheduled',
        action='store_true',
        help='Execute the backup as a scheduled task'
    )

    # Dry run option
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without actually copying or syncing any files'
    )

    # Notifications option
    parser.add_argument(
        '--notifications', 
        action='store_true', 
        help='Enable notifications for backup operations'
    )
    parser.add_argument(
        '--receiver', 
        nargs='+', 
        help='List of email addresses to receive notifications (space-separated)'
    )

    # Parse the arguments
    args = parser.parse_args()

    # If no arguments were passed, show the help message
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    return args

def validate_args(args, logger):
    # --scheduled and --dry-run don't make sense together
    if args.scheduled and args.dry_run:
        logger.error("--scheduled and --dry-run cannot be used together. "
                      "Dry-run is for one-off previews, not scheduled execution.")
        sys.exit(1)

    # Check if --backup-mode is specified without source and backup directories
    if args.backup_mode and (not args.source_dir or not args.backup_dirs):
        logger.error("Source directory and backup directories must be specified when using --backup-mode.")
        sys.exit(1)

    # Validate email addresses if --receiver is provided
    if args.receiver:
        for email in args.receiver:
            if not is_valid_email(email):
                logger.error(f"Invalid email address: {email}")
                sys.exit(1)
    
    # Warn if both local and ssh modes are selected
    if 'local' in args.operation_modes and 'ssh' in args.operation_modes:
        logger.warning("Both 'local' and 'ssh' operation modes selected. Ensure that both are intended to run concurrently.")
