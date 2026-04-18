"""Password hashing utilities using bcrypt directly."""

import bcrypt


def hash_password(plain: str) -> str:
    """Return the bcrypt hash of *plain* (UTF-8 encoded, random salt)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
