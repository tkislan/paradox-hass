class ParadoxError(Exception):
    pass


class ParadoxPageError(ParadoxError):
    pass


class ParadoxConnectionError(ParadoxError):
    """The panel did not answer: no whole HTTP response came back."""


class ParadoxTimeoutError(ParadoxConnectionError):
    def __init__(self) -> None:
        super().__init__("timeout of 20000ms exceeded")


class ParadoxHttpError(ParadoxError):
    """The panel answered with an HTTP status outside 200-299."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Request failed with status code {status}")
        self.status = status


class ParadoxAuthError(ParadoxError):
    pass


class ParadoxSessionExpiredError(ParadoxError):
    """A keep-alive or command was answered with the login page."""


class ParadoxClosedError(ParadoxError):
    pass
