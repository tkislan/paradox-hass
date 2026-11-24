import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "paradox"
LOGGER = logging.getLogger(__package__)

TITLE: Final = "Paradox"
MANUFACTURER: Final = "Paradox"

POLL_INTERVAL: Final = timedelta(seconds=1)
KEEP_ALIVE_INTERVAL: Final = timedelta(seconds=3)
RELOGIN_MIN_INTERVAL: Final = 60.0
UNANSWERED_GRACE: Final = 60.0
UNAVAILABLE_AFTER: Final = 30.0
COMMAND_DEADLINE: Final = 20.0
COMMAND_ATTEMPT_WINDOW: Final = 5.0
COMMAND_CHECK_INTERVAL: Final = 1.0
