"""Errors raised by the domain functions when a request cannot be carried out."""


class NotFound(Exception):
    pass


class NotPermitted(Exception):
    """The user may not do this (role, ownership, custody, or a deactivated account)."""


class InvalidState(Exception):
    """The request is not valid in the current state, e.g. closing an incident that is not resolved."""
