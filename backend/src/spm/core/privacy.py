"""Privacy helpers: client IPs are stored only as salted hashes."""

import hashlib


def hash_ip(salt: str, ip: str) -> str:
    """``sha256(salt + ip)`` as hex, for rate limits and report independence; never reversed."""
    return hashlib.sha256((salt + ip).encode()).hexdigest()
