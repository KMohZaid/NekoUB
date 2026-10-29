"""Persistent sudo, share, and explicit command grant access."""

from .database import AccessStore

__all__ = ["AccessStore"]
