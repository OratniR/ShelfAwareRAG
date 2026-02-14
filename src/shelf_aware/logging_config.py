import os

# 環境変数からログレベルを取得 (デフォルトは INFO)
# 開発時は .env で LOG_LEVEL=DEBUG にすれば詳細ログが出ます
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": LOG_LEVEL,
            "formatter": "standard",
            "stream": "ext://sys.stdout",  # Dockerログとして扱いやすいようstdoutに出力
        },
    },
    "loggers": {
        # アプリケーション自体のロガー
        "shelf_aware": {
            "level": LOG_LEVEL,
            "handlers": ["console"],
            "propagate": False,
        },
        # Webサーバー (Uvicorn) のロガー
        "uvicorn": {
            "level": "INFO",  # サーバーログはINFOで十分
            "handlers": ["console"],
            "propagate": False,
        },
        # HTTP通信ライブラリ (httpx) のノイズ抑制
        "httpx": {
            "level": "WARNING",  # WARNING以上のみ表示
            "handlers": ["console"],
            "propagate": False,
        },
    },
    # その他のライブラリからのログを拾うルートロガー
    "root": {
        "level": "WARNING",
        "handlers": ["console"],
    },
}
