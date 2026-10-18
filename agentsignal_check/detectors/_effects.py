"""Effect watcher shared by the crash-test child processes (_lg_child.py, _oai_child.py).

install(specs, ledger, crash_at) replaces each watched ``module:function`` with a wrapper that appends
one ledger row ``[spec, repr(args), repr(sorted(kwargs)), repr(result)]`` *after the effect has actually
run*, and hard-kills the process (os._exit(137)) right after the Nth recorded effect when crash_at=N.

Async effects (``async def``, or a sync wrapper that returns an awaitable, e.g. a decorator that is not
itself marked async) are awaited first, so the row holds the real result, not a coroutine object, and the
kill lands after the effect, not before it. An effect that raises is not recorded; if it raised a class
named ``EffectInFlight`` (matched by name: no import dependency) a row goes to ``<ledger>.inflight``.
"""
import functools, importlib, inspect, json, os

_state = {"calls": 0}


def load(spec):
    m, f = spec.split(":")
    return importlib.import_module(m), f


def _append(path, row):
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n"); fh.flush(); os.fsync(fh.fileno())


def install(specs, ledger, crash_at=0):
    for spec in specs:
        _wrap(spec, ledger, crash_at)


def _wrap(spec, ledger, crash_at):
    mod, name = load(spec)
    orig = getattr(mod, name)

    def failed(e, a, k):
        if any(c.__name__ == "EffectInFlight" for c in type(e).__mro__):
            _append(ledger + ".inflight", [spec, repr(a), repr(sorted(k.items()))])

    def done(a, k, res):
        _append(ledger, [spec, repr(a), repr(sorted(k.items())), repr(res)])
        _state["calls"] += 1
        if crash_at and _state["calls"] == crash_at:
            os._exit(137)  # hard kill right after the side effect: no finally, no checkpoint flush
        return res

    async def finish(aw, a, k):
        try:
            res = await aw
        except Exception as e:
            failed(e, a, k); raise
        return done(a, k, res)

    if inspect.iscoroutinefunction(orig):
        @functools.wraps(orig)
        async def w(*a, **k):
            return await finish(orig(*a, **k), a, k)
    else:
        @functools.wraps(orig)
        def w(*a, **k):
            try:
                res = orig(*a, **k)
            except Exception as e:
                failed(e, a, k); raise
            if inspect.isawaitable(res):   # sync callable handing back a coroutine/awaitable
                return finish(res, a, k)
            return done(a, k, res)
    setattr(mod, name, w)
