"""Mail-edge provider adapters."""

from .resend import ResendRelayAdapter
from .stalwart import StalwartAdapter

__all__ = ["ResendRelayAdapter", "StalwartAdapter"]
