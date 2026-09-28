"""Errors that block a whole run (as opposed to failing one item)."""

from __future__ import annotations


class BlockingError(RuntimeError):
    """A dependency can't serve *any* request: logged out, token revoked, plan limit.

    Pipelines stop instead of failing every remaining item (unprocessed items stay
    pending), and the CLI turns it into an alert note in the vault naming the
    *component* and a *hint* on how to fix it.
    """

    component = "Second Brain"

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class GmailAuthError(BlockingError):
    """Gmail authorization is missing, expired or revoked."""

    component = "Gmail"
