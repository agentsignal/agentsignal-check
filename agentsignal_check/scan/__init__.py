"""Static scan: `agentsignal check --scan PATH`. Pure ``ast``; never imports or executes user code."""
from .core import scan, public, exit_code, render_text, render_json  # noqa: F401
