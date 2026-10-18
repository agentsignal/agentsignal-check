"""Graph factory that fails during setup (stands in for a bad import / misconfigured graph)."""
def build(cp):
    raise RuntimeError("graph setup failed: DATABASE_URL not set")
