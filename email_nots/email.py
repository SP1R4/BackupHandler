import os
import smtplib
import configparser
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication


_EMAIL_CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'email_config.ini'


def _load_email_config():
    config_path = _EMAIL_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"Email config not found at '{config_path}'. "
            "Copy config/email_config.ini.example to config/email_config.ini and fill in your values."
        )

    config = configparser.ConfigParser()
    config.read(str(config_path))

    if 'EMAIL' not in config:
        raise KeyError("Missing [EMAIL] section in config/email_config.ini")

    email_section = config['EMAIL']

    # Required fields
    sender_email = email_section.get('sender_email', '').strip()
    app_password = email_section.get('app_password', '').strip()

    if not sender_email or sender_email == 'your_email@gmail.com':
        raise ValueError("Config error: 'sender_email' is not set in config/email_config.ini [EMAIL]")
    if not app_password or app_password == 'YOUR_APP_PASSWORD':
        raise ValueError("Config error: 'app_password' is not set in config/email_config.ini [EMAIL]")

    # Optional fields with defaults
    smtp_host = email_section.get('smtp_host', 'smtp.gmail.com').strip()
    smtp_port = email_section.getint('smtp_port', 465)

    return sender_email, app_password, smtp_host, smtp_port

def send_email(receiver_emails, subject, body, attachment_paths=None, logger=None):
    """
    Send an email with optional attachments and log key events.

    Parameters:
    - receiver_emails (list of str): List of recipient email addresses.
    - subject (str): Subject of the email.
    - body (str): Body text of the email.
    - attachment_paths (list of str, optional): List of file paths to attach to the email.
    - logger (logging.Logger, optional): Logger object for logging (default: None).
    """
    try:
        sender_email, app_password, smtp_host, smtp_port = _load_email_config()

        # Create a multipart message object
        message = MIMEMultipart()
        message['From'] = sender_email
        message['To'] = ', '.join(receiver_emails)
        message['Subject'] = subject

        # Attach body text to the message
        message.attach(MIMEText(body, 'plain'))
        if logger:
            logger.info("Email body attached.")

        # Attach files if any
        if attachment_paths:
            attach_files_to_email(message, attachment_paths, logger)

        # Send the email
        send_via_smtp(sender_email, app_password, receiver_emails, message, logger,
                      smtp_host=smtp_host, smtp_port=smtp_port)

    except Exception as e:
        error_message = f"Failed to send email: {e}"
        if logger:
            logger.error(error_message)
        else:
            print(error_message)

def attach_files_to_email(message, attachment_paths, logger=None):
    """
    Attach files to the email message with a fallback for unsupported file types.
    """
    file_type_map = {
        'pdf': 'application',
        'doc': 'application',
        'docx': 'application',
        'xls': 'application',
        'xlsx': 'application',
        'ppt': 'application',
        'pptx': 'application',
        'txt': 'text',
        'jpg': 'image',
        'jpeg': 'image',
        'png': 'image',
        'gif': 'image',
        'zip': 'application',
        'tar': 'application',
        'gz': 'application'
    }

    for attachment_path in attachment_paths:
        try:
            file_extension = os.path.splitext(attachment_path)[1][1:].lower()
            file_type = file_type_map.get(file_extension, 'application/octet-stream')  # Fallback MIME type

            # Open and attach the file
            with open(attachment_path, 'rb') as f:
                attachment = MIMEApplication(f.read(), _subtype=file_extension)
                attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(attachment_path))
                attachment.add_header('Content-Type', f'{file_type}/{file_extension}' if file_type in file_type_map else 'application/octet-stream')
                message.attach(attachment)

            if logger:
                logger.info(f"Attached file: {attachment_path}")
                if file_type == 'application/octet-stream':
                    logger.warning(f"Unknown file type for {attachment_path}. Using default MIME type 'application/octet-stream'.")

        except Exception as e:
            error_message = f"Error attaching file {attachment_path}: {e}"
            if logger:
                logger.error(error_message)
            else:
                print(error_message)


def send_via_smtp(sender_email, app_password, receiver_emails, message, logger=None,
                  smtp_host='smtp.gmail.com', smtp_port=465):
    """
    Send the email via SMTP.
    """
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, receiver_emails, message.as_string())

        if logger:
            logger.info(f"Email sent successfully to: {', '.join(receiver_emails)}")

    except smtplib.SMTPException as e:
        error_message = f"SMTP error occurred: {e}"
        if logger:
            logger.error(error_message)
        else:
            print(error_message)
