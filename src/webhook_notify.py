"""
webhook_notify.py - Webhook Notification Delivery

Sends backup event notifications to external services via HTTP webhooks.
Supports JSON payloads with configurable URLs and optional authentication
headers. Compatible with Slack, Discord, Microsoft Teams, and custom endpoints.
"""

import json


def send_webhook(logger, url, message, headers=None, timeout=30):
    """
    Send a notification to a webhook URL.

    Parameters:
        logger: Logger instance.
        url (str): Webhook endpoint URL.
        message (str): Notification message text.
        headers (dict, optional): Additional HTTP headers (e.g., auth tokens).
        timeout (int): Request timeout in seconds (default: 30).

    Returns:
        bool: True if the webhook responded with a 2xx status.
    """
    try:
        import requests
    except ImportError:
        logger.error("requests is not installed. Install it with: pip install requests")
        return False

    payload = {
        'text': message,
        'content': message,
    }

    request_headers = {'Content-Type': 'application/json'}
    if headers:
        request_headers.update(headers)

    try:
        response = requests.post(url, json=payload, headers=request_headers, timeout=timeout)
        if response.ok:
            logger.info(f"Webhook notification sent to {url}")
            return True
        else:
            logger.error(f"Webhook returned {response.status_code}: {response.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Failed to send webhook to {url}: {e}")
        return False
