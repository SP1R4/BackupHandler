import os
import time
import telebot
import configparser
from pathlib import Path as _Path
from threading import Thread, Event


CONFIG_FILE = str(_Path(__file__).parent.parent / 'config' / 'bot_config.ini')

class TelegramBot:
    def __init__(self, logger):
        """
        Initializes the TelegramBot instance with the given API token.
        """
        config = configparser.ConfigParser()
        if os.path.exists(CONFIG_FILE):
            config.read(CONFIG_FILE)
        else:
            raise FileNotFoundError(f"Configuration file '{CONFIG_FILE}' not found.")
        self.api_token = config['TELEGRAM']['api_token']
        self.user_id = config['USERS']['interacted_users']
        self.bot = telebot.TeleBot(self.api_token)
        self.interacted_users = []  # List to keep track of users who have interacted with the bot
        self.polling_thread = None  # Thread for bot polling
        self.stop_event = Event()  # Event to signal stopping of the polling
        self.logger = logger  # Logger for bot activities
        self.user_file = 'interacted_users_ids.json'  # File to store interacted users' IDs
        self.load_interacted_users()  # Load the list of interacted users from file
        self.setup_handlers()  # Set up message handlers for the bot

    def setup_handlers(self):
        """
        Sets up message handlers for the bot to respond to incoming messages.
        """
        @self.bot.message_handler(func=lambda message: True)
        def handle_any_message(message):
            """
            Handles incoming messages and registers users if they are not already registered.

            Args:
                message (telebot.types.Message): The incoming message from a user.
            """
            user_id = message.chat.id
            if user_id not in self.interacted_users:
                self.interacted_users.append(user_id)
                self.save_interacted_users()
                self.bot.send_message(user_id, "You're now registered for notifications!")

    def get_username(self, user_id):
        """
        Retrieves the username of a user given their user ID.

        Args:
            user_id (int): The user ID to retrieve the username for.

        Returns:
            str or None: The username if set, otherwise None.
        """
        try:
            chat = self.bot.get_chat(user_id)
            return chat.username if chat.username else None
        except telebot.apihelper.ApiTelegramException as e:
            self.logger.error(f"Telegram API error occurred while retrieving username for user ID {user_id}: {e}")
            return None
        except telebot.apihelper.ApiException as e:
            self.logger.error(f"API error occurred while retrieving username for user ID {user_id}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error occurred while retrieving username for user ID {user_id}: {e}")
            return None

    def load_interacted_users(self):
        """
        Loads the list of interacted users from the bot_config.ini file.
        """
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        if 'USERS' in config and 'interacted_users' in config['USERS']:
            self.interacted_users = [int(user_id) for user_id in config['USERS']['interacted_users'].split(',') if user_id]
        else:
            self.interacted_users = []

    def save_interacted_users(self):
        """
        Saves the list of interacted users to the bot_config.ini file.
        """
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        if 'USERS' not in config:
            config['USERS'] = {}
        config['USERS']['interacted_users'] = ','.join(map(str, self.interacted_users))
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)

    def send_notification(self, text):
        """
        Sends a notification to all interacted users.
        Continues sending to remaining users even if one fails.

        Args:
            text (str): The message text to be sent as a notification.
        """
        if not self.interacted_users:
            return
        failed_count = 0
        for user_id in self.interacted_users:
            try:
                self.bot.send_message(user_id, text)
                self.logger.info(f"Notification sent to {self.get_username(user_id)}: {text}")
            except telebot.apihelper.ApiTelegramException as e:
                self.logger.error(f"Telegram API error sending to user {user_id}: {e}")
                failed_count += 1
            except telebot.apihelper.ApiException as e:
                self.logger.error(f"API error sending to user {user_id}: {e}")
                failed_count += 1
            except Exception as e:
                self.logger.error(f"Failed to send notification to user {user_id}: {e}")
                failed_count += 1
        if failed_count == len(self.interacted_users):
            self.logger.error("Failed to send notification to all users. Try to interact with the bot first.")
            self.start_polling_for_limited_time(timeout=5)

    def send_image(self, file_path, caption=None):
        """
        Sends an image to all interacted users.

        Args:
            file_path (str): Path to the image file.
            caption (str, optional): Caption to include with the image.
        """
        if not self.interacted_users:
            return
        for user_id in self.interacted_users:
            try:
                with open(file_path, 'rb') as f:
                    self.bot.send_photo(user_id, photo=f, caption=caption)
                self.logger.info(f"Image sent to {self.get_username(user_id)}")
            except telebot.apihelper.ApiTelegramException as e:
                self.logger.error(f"Telegram API error sending image to user {user_id}: {e}")
            except telebot.apihelper.ApiException as e:
                self.logger.error(f"API error sending image to user {user_id}: {e}")
            except Exception as e:
                self.logger.error(f"Failed to send image to user {user_id}: {e}")

    def send_geolocation(self, latitude, longitude, live_period=60):
        """
        Sends a geolocation to all interacted users.

        Args:
            latitude (float): Latitude of the location.
            longitude (float): Longitude of the location.
            live_period (int, optional): Period in seconds for which the location should be updated. Defaults to 60.
        """
        if not self.interacted_users:
            return
        for user_id in self.interacted_users:
            try:
                self.bot.send_location(user_id, latitude=latitude, longitude=longitude, live_period=live_period)
                self.logger.info(f"Geolocation sent to user ID {user_id}: Latitude {latitude}, Longitude {longitude}")
            except telebot.apihelper.ApiTelegramException as e:
                self.logger.error(f"Telegram API error sending geolocation to user {user_id}: {e}")
            except telebot.apihelper.ApiException as e:
                self.logger.error(f"API error sending geolocation to user {user_id}: {e}")
            except Exception as e:
                self.logger.error(f"Failed to send geolocation to user {user_id}: {e}")

    def send_document(self, file_path=None, caption=None, document=None):
        """
        Sends a document to all interacted users.
        Supports both file paths and file-like objects (e.g. BytesIO).

        Args:
            file_path (str, optional): Path to the document file.
            caption (str, optional): Caption to include with the document.
            document (file-like, optional): A file-like object to send directly.
        """
        if not self.interacted_users:
            return
        for user_id in self.interacted_users:
            try:
                if document is not None:
                    self.bot.send_document(user_id, document=document, caption=caption)
                    # Reset buffer position for next user if it's seekable
                    if hasattr(document, 'seek'):
                        document.seek(0)
                elif file_path is not None:
                    with open(file_path, 'rb') as f:
                        self.bot.send_document(user_id, document=f, caption=caption)
                else:
                    self.logger.error("send_document called with no file_path or document")
                    return
                self.logger.info(f"Document sent to {self.get_username(user_id)}")
            except telebot.apihelper.ApiTelegramException as e:
                self.logger.error(f"Telegram API error sending document to user {user_id}: {e}")
            except telebot.apihelper.ApiException as e:
                self.logger.error(f"API error sending document to user {user_id}: {e}")
            except Exception as e:
                self.logger.error(f"Failed to send document to user {user_id}: {e}")

    def start_polling_for_limited_time(self, timeout=30):
        """
        Starts polling the Telegram API for a limited amount of time.
        Runs in a background thread so it doesn't block the caller.

        Args:
            timeout (int): The duration in seconds to keep polling.
        """
        self.stop_event.clear()
        self.polling_thread = Thread(target=self._poll_and_stop, args=(timeout,), daemon=True)
        self.polling_thread.start()

    def _poll_and_stop(self, timeout):
        """
        Internal method: starts polling, waits for timeout, then stops.
        """
        polling_thread = Thread(target=self.start_polling, daemon=True)
        polling_thread.start()
        time.sleep(timeout)
        self.stop_polling()

    def start_polling(self):
        """
        Starts the bot's polling process to receive messages.
        """
        try:
            self.logger.info("Starting bot polling...")
            self.bot.polling(non_stop=True)
        except Exception as e:
            self.logger.error(f"Polling error occurred: {e}")
        finally:
            self.stop_event.set()

    def stop_polling(self):
        """
        Stops the bot's polling process.
        """
        if self.polling_thread and self.polling_thread.is_alive():
            self.logger.info("Stopping bot polling...")
            self.bot.stop_polling()  # Stop polling immediately
            self.polling_thread.join(timeout=10)  # Wait with timeout to avoid hanging
