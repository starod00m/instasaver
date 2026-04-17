"""Runtime configuration for the bot.

Reads environment variables with sensible defaults. Secrets and operational
parameters are supplied by Docker Compose ``env_file`` in production and by a
local ``.env`` (optional) during development.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env if present. In containers env vars are already provided by
# docker-compose env_file, so override=False keeps the compose-provided values.
# Having load_dotenv here is safe: missing .env is a no-op.
load_dotenv(override=False)


def _get_required(name: str) -> str:
    """Read a required environment variable or exit the process.

    :param name: Name of the environment variable.
    :type name: str
    :return: Non-empty value of the environment variable.
    :rtype: str
    """
    value = os.environ.get(name, "").strip()
    if value == "":
        logger.error(f"Required environment variable {name} is not set")
        sys.exit(1)
    return value


def _get_optional(name: str, default: str = "") -> str:
    """Read an optional environment variable with a default.

    :param name: Name of the environment variable.
    :type name: str
    :param default: Value to return when the variable is unset or empty.
    :type default: str
    :return: Value of the environment variable or the default.
    :rtype: str
    """
    value = os.environ.get(name, "").strip()
    if value == "":
        return default
    return value


class Config:
    """Typed access to runtime configuration.

    All values are resolved once at construction time. The process is expected
    to terminate if any required variable is missing.
    """

    def __init__(self) -> None:
        """Resolve configuration from environment variables.

        :return: None
        """
        self.telegram_bot_token: str = _get_required(name="TELEGRAM_BOT_TOKEN")
        self.webhook_secret: str = _get_required(name="WEBHOOK_SECRET")

        self.webhook_url: str = _get_optional(name="WEBHOOK_URL", default="")
        self.port: int = int(_get_optional(name="PORT", default="8080"))
        self.log_level: str = _get_optional(name="LOG_LEVEL", default="INFO").upper()

        self.proxy_url: Optional[str] = _get_optional(name="PROXY_URL", default="") or None
        self.admin_user_id: Optional[str] = _get_optional(name="ADMIN_USER_ID", default="") or None

        self.google_credentials_json_base64: Optional[str] = (
            _get_optional(name="GOOGLE_CREDENTIALS_JSON_BASE64", default="") or None
        )
        self.google_sheets_spreadsheet_id: Optional[str] = (
            _get_optional(name="GOOGLE_SHEETS_SPREADSHEET_ID", default="") or None
        )

        # Writable scratch space inside the container. Lives on tmpfs in
        # production so it's wiped on restart — intentional, videos are ephemeral.
        self.temp_dir: Path = Path("/tmp/instasaver")

        # Graceful shutdown deadline. Docker sends SIGTERM then SIGKILL after 10s
        # by default, so we mirror that here.
        self.shutdown_timeout_seconds: float = 10.0

    def webhook_path(self) -> str:
        """Return the HTTP path where Telegram posts updates.

        :return: Path component of the webhook URL, always ``/webhook``.
        :rtype: str
        """
        return "/webhook"
