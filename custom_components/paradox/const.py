import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "paradox"
LOGGER = logging.getLogger(__package__)

TITLE: Final = "Paradox"
MANUFACTURER: Final = "Paradox"

POLL_INTERVAL: Final = timedelta(seconds=1)
KEEP_ALIVE_INTERVAL: Final = timedelta(seconds=3)
