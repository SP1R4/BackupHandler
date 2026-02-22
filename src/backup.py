import shutil
from .utils import calculate_checksum


def copy_file(logger, src_path, dst_path):
    """
    Copies a file from the source path to the destination path.

    Parameters:
    - src_path (str): The path to the source file.
    - dst_path (str): The path to the destination file.
    
    Raises:
    - Exception: Logs an error message if the copy operation fails or if there is a checksum mismatch.
    """
    try:
        # Perform the file copy operation, preserving metadata
        shutil.copy2(src_path, dst_path)
        logger.info(f"Copied '{src_path}' to '{dst_path}'")

        # Generate and compare checksums to verify the integrity of the copied file
        src_checksum = calculate_checksum(src_path, logger=logger)
        dst_checksum = calculate_checksum(dst_path, logger=logger)
        
        if src_checksum != dst_checksum:
            logger.error(f"Checksum mismatch for '{src_path}' and '{dst_path}'")
        else:
            logger.info(f"Checksum verified for '{src_path}'")
    except Exception as e:
        # Log an error if the copy operation fails
        logger.error(f"Failed to copy '{src_path}' to '{dst_path}': {e}")
