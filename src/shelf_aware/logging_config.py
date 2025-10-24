import logging.config

# This dictionary defines your entire logging setup.
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # Formatter for the console
        "console_formatter": {
            "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        # Formatter for the file
        "file_formatter": {
            "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        # Console handler: prints to your terminal (or systemd journal)
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG", # Show DEBUG messages on the console
            "formatter": "console_formatter",
            "stream": "ext://sys.stderr", # Use stderr for errors
        },
        # File handler: saves logs to a file
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG", # Also log DEBUG messages to the file
            "formatter": "file_formatter",
            "filename": "data/app.log", # We'll save the log here
            "maxBytes": 10485760,  # 10MB
            "backupCount": 3,  # Keep 3 old log files
            "encoding": "utf8",
        },
    },
    "loggers": {
        # Logger for our application
        "shelf_aware": {
            "level": "DEBUG", # Capture all messages from DEBUG level up
            "handlers": ["console", "file"], # Send to both console and file
            "propagate": False, # Don't pass messages to the root logger
        },
        # Logger for the web server
        "uvicorn": {
            "level": "INFO", # Uvicorn's own logs (e.g., "Application startup complete")
            "handlers": ["console", "file"],
            "propagate": False,
        },
    },
    # The "root" logger catches everything else
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}