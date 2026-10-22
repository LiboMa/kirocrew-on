"""Worker connections as the platform stores them (D59, K10): the access token sealed with the D35 key, never plaintext."""
from . import vault


def seal(config, key):
    """``config`` with its token sealed: ``token`` removed, ``token_sealed`` in its place."""
    return {**{k: v for k, v in config.items() if k != "token"}, "token_sealed": vault.seal(key, config["token"])}
