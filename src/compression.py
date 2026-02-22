import io
import os
import shutil
import keyring
import pyminizip
from datetime import datetime
from email_nots.email import send_email


def save_file_passwd(logger, timestamp, passwd):
    try:
        keyring.set_password("compression_service", timestamp, passwd)  # Store the password securely
        logger.info(f"Password stored securely for '{timestamp}'")
    except Exception as e:
        logger.error(f"Failed to store password securely: {e}")

def compress_directory(logger, src_dirs=None, output_dirs=None, password=None, bot_handler=None, receiver_emails=None):
    """
    Compress multiple source directories into ZIP files with optional password protection.
    The output ZIP files will be saved in the corresponding output directories.

    Parameters:
    - logger (logging.Logger): The logger instance to use for logging messages.
    - src_dirs (list of str): The list of paths to the source directories to be compressed.
    - output_dirs (list of str): The list of output directories where the resulting ZIP files will be saved.
    - password (str, optional): If provided, the ZIP files will be encrypted with this password.
    - bot_handler (TelegramBot, optional): The instance of the TelegramBot to send the document.
    - receiver_emails (list of str, optional): List of email addresses to receive the password via email.
    """
    if src_dirs is None or output_dirs is None:
        logger.error("Source and output directories must be provided.")
        return

    for src_dir in src_dirs:
        files = []
        for root, dirs, file_list in os.walk(src_dir):
            for file in file_list:
                files.append(os.path.join(root, file))

        for output_dir in output_dirs:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_zip = os.path.join(output_dir, f"backup_{timestamp}.zip")

            try:
                if password:
                    pyminizip.compress_multiple(files, [], output_zip, password, 5)
                    logger.info(f"Compressed directory '{src_dir}' to '{output_zip}' with password protection")

                    save_file_passwd(logger, timestamp, password)
                else:
                    shutil.make_archive(output_zip[:-4], 'zip', src_dir)
                    logger.info(f"Compressed directory '{src_dir}' to '{output_zip}' without password protection")

                # Send password via bot if enabled (pass password directly)
                if bot_handler and password:
                    _send_password_via_bot(logger, bot_handler, timestamp, password)

                # Send password via email if enabled (pass password directly)
                if receiver_emails and password:
                    _send_password_via_email(logger, receiver_emails, timestamp, password)

            except Exception as e:
                logger.error(f"Failed to compress directory '{src_dir}' to '{output_zip}': {e}")


def _send_password_via_bot(logger, bot_handler, timestamp, password):
    """
    Sends the password to the user via Telegram bot using an in-memory buffer.
    Uses TelegramBot.send_document which handles per-user errors and BytesIO.

    Args:
        logger: Logger instance.
        bot_handler (TelegramBot): The TelegramBot instance.
        timestamp (str): The timestamp identifier for the backup.
        password (str): The password to send.
    """
    try:
        content = f"{timestamp}: {password}\n"
        buf = io.BytesIO(content.encode('utf-8'))
        buf.name = "backup_password.txt"

        bot_handler.send_document(document=buf, caption="Here is your backup password.")
        logger.info("Sent backup password to users via bot.")
    except Exception as e:
        logger.error(f"Failed to send password via bot: {e}")


def _send_password_via_email(logger, receiver_emails, timestamp, password):
    """
    Sends the password to the user via email using an in-memory buffer written to a temp file.

    Args:
        logger: Logger instance.
        receiver_emails (list of str): List of recipient email addresses.
        timestamp (str): The timestamp identifier for the backup.
        password (str): The password to send.
    """
    try:
        import tempfile
        content = f"{timestamp}: {password}\n"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            subject = "Backup Password"
            body = "Please find the attached file containing your backup password."
            send_email(receiver_emails, subject, body, attachment_paths=[tmp_path], logger=logger)
            logger.info("Sent backup password to users via email.")
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Failed to send password via email: {e}")
