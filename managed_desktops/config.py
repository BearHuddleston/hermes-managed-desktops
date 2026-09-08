"""Invocation-local settings, with an optional native Hermes adapter."""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import MappingProxyType


DEFAULTS = MappingProxyType({
    "cpus": 4, "memory_mib": 4096, "disk_gib": 32, "ready_timeout": 1200,
    "ovmf_code": "", "ovmf_vars": "",
})
# Relative imports give each directory-plugin namespace its own ContextVar;
# context-local binding also isolates shared pip modules and concurrent calls.
_config_reader = ContextVar(__name__ + ".config_reader", default=None)


@contextmanager
def settings_scope(get_config=None):
    """Bind settings without importing Hermes or consulting ambient profiles."""
    token = _config_reader.set(get_config)
    try:
        yield
    finally:
        _config_reader.reset(token)


@contextmanager
def bind(home, get_config):
    """Bind native callbacks to their registration home; restore nested scopes."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from .state import profile_scope

    home_token = set_hermes_home_override(home)
    try:
        with profile_scope(home), settings_scope(get_config):
            yield
    finally:
        reset_hermes_home_override(home_token)


def profile_settings(home):
    """Read only an explicitly selected profile's namespaced config.yaml settings."""
    import os
    import stat
    import yaml
    from .state import VMError, profile_identity

    profile_identity(home)
    path = Path(home).expanduser().resolve() / "config.yaml"
    try:
        # No secret-file symlinks or blocking FIFOs disguised as config.yaml.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="utf-8-sig") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise VMError(f"Profile config.yaml must be a regular file: {path}")
            config = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        # YAML errors can quote unrelated config values, including credentials.
        raise VMError(f"Cannot read a valid profile config.yaml at {path}; no operation performed.") from exc
    for key in ("plugins", "entries", "managed-desktops", "settings"):
        if not isinstance(config, dict):
            raise VMError(f"Invalid profile config.yaml at {path}: expected a settings mapping.")
        config = config.get(key, {})
    if not isinstance(config, dict):
        raise VMError(f"Invalid managed-desktops settings in {path}: expected a mapping.")
    values = {key: config.get(key, default) for key, default in DEFAULTS.items()}
    for key, default in DEFAULTS.items():
        if type(values[key]) is not type(default):
            raise VMError(f"Invalid managed-desktops setting {key!r}: expected {type(default).__name__}.")
    for key, low, high in (("cpus", 1, 256), ("memory_mib", 1024, 1048576), ("disk_gib", 8, 4096)):
        if not low <= values[key] <= high:
            raise VMError(f"{key} must be an integer between {low} and {high}.")
    if values["ready_timeout"] <= 0:
        raise VMError("ready_timeout must be a positive integer.")
    return values


def settings():
    """Return fresh settings; direct unbound library calls use immutable defaults."""
    get_config = _config_reader.get()
    if get_config is None:
        return dict(DEFAULTS)
    return {key: get_config(key, default) for key, default in DEFAULTS.items()}
