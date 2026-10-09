#!/usr/bin/env python3
"""Container health check against the local API. Succeeds only on HTTP 200."""

import os
import ssl
import urllib.error
import urllib.request


def main() -> None:
    port = os.environ.get("TRISUL_AI_PORT", "8200")
    use_tls = bool(os.environ.get("TRISUL_AI_SSL_CERTFILE") and os.environ.get("TRISUL_AI_SSL_KEYFILE"))
    scheme = "https" if use_tls else "http"
    context = ssl._create_unverified_context() if use_tls else None
    url = f"{scheme}://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=4, context=context) as response:
            if response.status != 200:
                raise SystemExit(1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
