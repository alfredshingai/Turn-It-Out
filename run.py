"""TurnitOut — Turnitin-style similarity + AI-writing checker.

Usage:
  python run.py                 # localhost only, port 8333
  python run.py --lan           # reachable from other devices on this network
  python run.py --port 9000     # custom port
  python run.py --host 0.0.0.0  # explicit bind
"""
import argparse
import os
import socket
import sys


def lan_addresses(port: int) -> list[str]:
    """Best-effort list of URLs students on the same network can use."""
    urls: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no traffic sent; picks the default route
            urls.append(f"http://{s.getsockname()[0]}:{port}")
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            url = f"http://{ip}:{port}"
            if url not in urls and not ip.startswith("127."):
                urls.append(url)
    except OSError:
        pass
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TurnitOut server")
    parser.add_argument("port", nargs="?", type=int, default=None,
                        help="port (positional, for backward compat)")
    parser.add_argument("--port", type=int, default=None, dest="port_opt")
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("--lan", action="store_true",
                        help="serve on all interfaces so other devices can connect")
    args = parser.parse_args()

    port = args.port_opt or args.port or int(os.environ.get("PORT", "8333"))
    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")

    from app.server import serve
    httpd = serve(host=host, port=port)

    if host == "0.0.0.0":
        print("\nLAN mode — students can open:")
        for url in lan_addresses(port):
            print(f"  {url}")
        print("(If the page doesn't load, allow Python through Windows Firewall.)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
