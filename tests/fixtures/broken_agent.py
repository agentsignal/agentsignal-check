"""Agent factory that fails during setup."""
def build():
    raise RuntimeError("agent setup failed: missing tool config")
