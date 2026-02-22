import os
import smtplib
import configparser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication


def _load_email_config():
    config = configparser.ConfigParser()
    config.read('config/email_config.ini')
    sender_email = config['EMAIL']['sender_email']
    app_password = config['EMAIL']['app_password']
    smtp_host = config['EMAIL'].get('smtp_host', 'smtp.gmail.com')
    smtp_port = config['EMAIL'].getint('smtp_port', 465)
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

    This function creates and sends an email using SMTP with optional file attachments and logs key events.
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

    Parameters:
    - message (MIMEMultipart): The email message object to which attachments will be added.
    - attachment_paths (list of str): List of file paths to attach to the email.
    - logger (logging.Logger, optional): Logger object for logging (default: None).

    This function attaches files from the provided paths to the email message and logs the process.
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

    Parameters:
    - sender_email (str): Sender's email address.
    - app_password (str): App-specific password for authentication.
    - receiver_emails (list of str): List of recipient email addresses.
    - message (MIMEMultipart): The email message to send.
    - logger (logging.Logger, optional): Logger object for logging (default: None).
    - smtp_host (str): SMTP server hostname (default: smtp.gmail.com).
    - smtp_port (int): SMTP server port (default: 465).

    This function handles the SMTP connection and sends the email.
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


