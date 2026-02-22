import time
import schedule
from .sync import sync_directories_with_progress


def schedule_backup(logger, source_dir, backup_dir, interval_hours=5):
    """
    Schedules a backup operation to run at regular intervals.

    Parameters:
    - logger (logging.Logger): The logger instance to use for logging messages.
    - source_dir (str): The directory to be backed up.
    - backup_dir (str): The directory where the backup will be stored.
    - interval_hours (int): The number of hours between each backup operation. Defaults to 5.
    """

    def perform_backup():
        try:
            sync_directories_with_progress(logger, [source_dir], [backup_dir])
            logger.info(f"Backup completed successfully at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            logger.error(f"Backup failed: {e}")

    schedule.every(interval_hours).hours.do(perform_backup)

    logger.info(f"Backup scheduled every {interval_hours} hours")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Backup scheduler stopped by user.")
    except Exception as e:
        logger.error(f"Scheduler encountered an error: {e}")
