"""Start the server.

Always serves plain HTTP on PORT (default 8000). When backend/certs/{cert,key}.pem
exist (create them with `python scripts/make_cert.py`) it also serves HTTPS on
HTTPS_PORT (default 8443) from the same process, so both listeners share one
device hub. Phones need the HTTPS one for real motion-sensor data.

    python backend/run.py
    PORT=8000 HTTPS_PORT=8443 python backend/run.py
"""
import asyncio
import os
from pathlib import Path

import uvicorn

BASE = Path(__file__).resolve().parent
CERT, KEY = BASE / "certs" / "cert.pem", BASE / "certs" / "key.pem"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
HTTPS_PORT = int(os.getenv("HTTPS_PORT", "8443"))
HTTPS = CERT.exists() and KEY.exists()

# main.py reads this to advertise the HTTPS address in /api/join-info
os.environ["HTTPS_PORT"] = str(HTTPS_PORT) if HTTPS else ""


async def main() -> None:
    from app.main import app  # single app instance shared by both listeners

    servers = [uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="info"))]
    if HTTPS:
        servers.append(uvicorn.Server(uvicorn.Config(
            app, host=HOST, port=HTTPS_PORT, log_level="info",
            ssl_certfile=str(CERT), ssl_keyfile=str(KEY),
        )))
        print(f"HTTP on :{PORT}, HTTPS on :{HTTPS_PORT}")
    else:
        print(f"HTTP on :{PORT} (run scripts/make_cert.py to enable HTTPS)")

    # Only the first server installs signal handlers; the rest follow it.
    for s in servers[1:]:
        s.install_signal_handlers = lambda: None
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(BASE))
    try:
        asyncio.run(main())
    except SystemExit:
        # uvicorn exits with code 3 when it can't bind; the ERROR line above says which port
        print("startup failed - is another copy of the server already running on that port?")
        sys.exit(3)
    except KeyboardInterrupt:
        pass
