import sys
import configparser
import logging
from colorama import init
from datetime import datetime
from email_nots.email import send_email


if __name__ == "__main__":
    # Example receiver emails
    receiver_emails = ["user@example.com", "admin@example.com"]

    # Example email subject and body
    subject = "Backup Completed"
    body = "Your backup has completed successfully."

    # Example attachments (optional)
    attachment_paths = ["path/to/backup.zip", "path/to/report.txt"]

    # Example logger (optional) - You can also pass None if you don't use logging
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Send the email
    send_email(receiver_emails, subject, body, attachment_paths, logger=logger)
