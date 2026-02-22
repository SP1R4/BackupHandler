import logging
from logging import handlers


class AppLogger:
    def __init__(self, log_file, log_level=logging.INFO):
        """
        Initializes the AppLogger with a specified log file and log level.

        :param log_file: The path to the log file.
        :param log_level: The logging level. Defaults to INFO.
        """
        self.logger = self.setup_logger(log_file, log_level)

    def setup_logger(self, log_file, log_level):
        """
        Sets up the logger with a file handler and a console handler.

        :param log_file: The path to the log file.
        :param log_level: The logging level.
        :return: The configured logger.
        """
        logger = logging.getLogger(__name__)
        logger.setLevel(log_level)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        if not logger.handlers:
            # File handler
            file_handler = handlers.RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        return logger

    def log(self, level, msg, *args, **kwargs):
        """
        Logs a message at the specified level.

        :param level: The logging level.
        :param msg: The message to log.
        :param args: Additional arguments for the message.
        :param kwargs: Keyword arguments for the message.
        """
        self.logger.log(level, msg, *args, **kwargs)
