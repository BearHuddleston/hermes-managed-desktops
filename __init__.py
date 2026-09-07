"""Directory-discovery shim; implementation and assets live in the package."""


def register(ctx):
    from .managed_desktops import register as register_package

    return register_package(ctx)
