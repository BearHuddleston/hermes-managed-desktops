"""Invocation-local settings read through the native PluginContext API."""

from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType


DEFAULTS = MappingProxyType({
    "cpus": 4, "memory_mib": 4096, "disk_gib": 32, "ready_timeout": 1200,
    "ovmf_code": "", "ovmf_vars": "",
})
# Relative imports give each directory-plugin namespace its own ContextVar;
# context-local binding also isolates shared pip modules and concurrent calls.
_config_reader = ContextVar(__name__ + ".config_reader", default=None)


@contextmanager
def bind(home, get_config):
    """Bind a callback to its registration home, restoring both scopes on exit."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_token = set_hermes_home_override(home)
    config_token = _config_reader.set(get_config)
    try:
        yield
    finally:
        _config_reader.reset(config_token)
        reset_hermes_home_override(home_token)


def settings():
    """Return fresh settings; direct unbound library calls use immutable defaults."""
    get_config = _config_reader.get()
    if get_config is None:
        return dict(DEFAULTS)
    return {key: get_config(key, default) for key, default in DEFAULTS.items()}
