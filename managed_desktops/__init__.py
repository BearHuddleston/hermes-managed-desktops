"""Named Linux/KVM desktops; importing never touches profile state or a guest."""


def register(ctx):
    """Register only CLI and explicitly loaded instructions, using public APIs."""
    from pathlib import Path
    from hermes_constants import get_hermes_home
    from .config import bind

    home = get_hermes_home().resolve()

    def dispatch(args):
        from .cli import _dispatch
        from .state import VMError

        try:
            with bind(home, ctx.get_config):
                return _dispatch(args)
        except (VMError, OSError, ValueError) as exc:
            import sys

            print(f"desktop-vm: {exc}", file=sys.stderr)
            return 1

    def setup(parser):
        from .cli import setup_parser

        setup_parser(parser, native=True)
        # setup_parser supports direct use and sets its own handler. Restore
        # the bound handler even for callers which attach defaults before setup.
        parser.set_defaults(func=dispatch)

    ctx.register_cli_command(
        "desktop-vm",
        "Manage dedicated Linux/KVM agent desktops (never the host)",
        setup,
        dispatch,
        description="Named Debian guests with explicit VM/screen targeting.",
    )
    ctx.register_skill(
        "managed-agent-desktops",
        Path(__file__).parent / "skills" / "managed-agent-desktops" / "SKILL.md",
    )
