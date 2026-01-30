import sys

def _try(module: str):
    try:
        m = __import__(module, fromlist=["main"])
        if hasattr(m, "main"):
            return m.main
    except Exception:
        return None

def main():
    for mod in ("sdlc_agent.cli", "sdlc_agent.main", "sdlc_agent.app"):
        fn = _try(mod)
        if fn:
            return fn()
    print("No CLI entrypoint found. Try: python -m sdlc_agent.<module> --help")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
