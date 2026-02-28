import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_smtp_email(logger, smtp_host, smtp_port, smtp_user, smtp_password,
                    from_addr, to_addrs, subject, body, use_tls=True):
    """
    Send an email notification via SMTP.

    Parameters:
    - logger: Logger instance.
    - smtp_host (str): SMTP server hostname.
    - smtp_port (int): SMTP server port.
    - smtp_user (str): SMTP authentication username.
    - smtp_password (str): SMTP authentication password.
    - from_addr (str): Sender email address.
    - to_addrs (list of str): List of recipient email addresses.
    - subject (str): Email subject line.
    - body (str): Email body text.
    - use_tls (bool): Whether to use STARTTLS (default True).

    Returns:
    - bool: True if email sent successfully, False otherwise.
    """
    if not to_addrs:
        logger.warning("No recipient email addresses provided. Skipping SMTP notification.")
        return False

    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = ', '.join(to_addrs)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    retries = 3
    for attempt in range(1, retries + 1):
        try:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
            server.quit()
            logger.info(f"SMTP email sent to {', '.join(to_addrs)}: {subject}")
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return False  # Don't retry auth failures
        except Exception as e:
            logger.warning(f"SMTP send attempt {attempt}/{retries} failed: {e}")
            if attempt == retries:
                logger.error(f"Failed to send SMTP email after {retries} attempts: {e}")
                return False
    return False
