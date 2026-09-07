#!/usr/bin/python3
"""Loopback-only noVNC with same-origin WebSocket admission.

SSH changes the browser-facing port, so compare Origin with the request Host,
not the guest's listen port. Neither header may name a non-loopback host.
"""
import argparse
from urllib.parse import urlsplit


def local_host(host):
    try:
        parsed = urlsplit("http://" + host)
        return (parsed.hostname in ("127.0.0.1", "localhost")
                and parsed.port is not None and 0 < parsed.port < 65536
                and not parsed.username and not parsed.password
                and not parsed.path and not parsed.query and not parsed.fragment
                and parsed.netloc == host)
    except (ValueError, TypeError):
        return False


def allowed_origin(origin, host):
    if not isinstance(origin, str) or not isinstance(host, str) or not local_host(host):
        return False
    # Exact serialization rejects userinfo, alternate schemes, paths and suffix tricks.
    return origin == "http://" + host


def request_handler(base):
    # Debian 13 websockify 0.12's handle_upgrade hook runs before its handshake.
    # Its built-in ExpectOrigin accepts only a static list, not dynamic SSH ports.
    class LocalViewHandler(base):
        def handle_upgrade(self):
            origins = self.headers.get_all("Origin", [])
            hosts = self.headers.get_all("Host", [])
            if len(origins) != 1 or len(hosts) != 1 or not allowed_origin(origins[0], hosts[0]):
                self.send_error(403, "WebSocket origin not allowed")
                return
            super().handle_upgrade()

        def do_GET(self):
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or not local_host(hosts[0]):
                self.send_error(403, "Loopback Host required")
                return
            super().do_GET()

        def end_headers(self):
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    return LocalViewHandler


def main():
    from websockify.websocketproxy import ProxyRequestHandler, WebSocketProxy

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", type=int, choices=(1, 2))
    args = parser.parse_args()
    server = WebSocketProxy(
        RequestHandlerClass=request_handler(ProxyRequestHandler),
        listen_host="127.0.0.1", listen_port=6080 + args.screen,
        target_host="127.0.0.1", target_port=5900 + args.screen,
        web="/usr/share/novnc", file_only=True,
    )
    server.start_server()


if __name__ == "__main__":
    main()
