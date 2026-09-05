#!/usr/bin/env python3
"""Tail a mihomo/Clash external-controller WebSocket stream to stdout as NDJSON.
Stdlib only. Usage:
  python clashtail.py ws://192.168.1.50:9090/connections?interval=1000 --token SECRET
  python clashtail.py ws://192.168.1.50:9090/logs?level=info          --token SECRET
"""
import argparse, base64, json, os, socket, ssl, struct, sys, time
from urllib.parse import urlsplit, urlencode, parse_qsl, urlunsplit

def connect(url, token, timeout=10):
    u = urlsplit(url)
    q = dict(parse_qsl(u.query))
    if token:
        q["token"] = token               # mihomo accepts ?token= ONLY on websocket upgrades
    path = urlunsplit(("", "", u.path or "/", urlencode(q), ""))
    port = u.port or (443 if u.scheme == "wss" else 80)
    s = socket.create_connection((u.hostname, port), timeout)
    if u.scheme == "wss":
        s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
           + (f"Authorization: Bearer {token}\r\n" if token else "") + "\r\n")
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        c = s.recv(4096)
        if not c:
            raise ConnectionError("closed during handshake")
        buf += c
    head, rest = buf.split(b"\r\n\r\n", 1)
    if b"101" not in head.split(b"\r\n")[0]:
        raise ConnectionError("handshake failed: " + head.decode("latin1", "replace")[:300])
    s.settimeout(None)
    return s, rest

def frames(sock, rest=b""):
    buf = bytearray(rest)
    def need(n):
        while len(buf) < n:
            c = sock.recv(65536)
            if not c:
                raise ConnectionError("stream closed")
            buf.extend(c)
    while True:
        need(2)
        b0, b1 = buf[0], buf[1]
        op, masked, ln, off = b0 & 0x0F, b1 & 0x80, b1 & 0x7F, 2
        if ln == 126:
            need(4); ln = struct.unpack(">H", buf[2:4])[0]; off = 4
        elif ln == 127:
            need(10); ln = struct.unpack(">Q", buf[2:10])[0]; off = 10
        if masked:
            off += 4
        need(off + ln)
        payload = bytes(buf[off:off + ln])
        del buf[:off + ln]
        if op == 0x8:
            raise ConnectionError("server sent close")
        if op in (0x1, 0x2):
            yield payload

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--token", default=os.environ.get("CLASH_SECRET", ""))
    ap.add_argument("--out", help="append NDJSON here as well as stdout")
    ap.add_argument("--once", action="store_true", help="exit after first frame (self-test)")
    a = ap.parse_args()
    fh = open(a.out, "a", encoding="utf-8", buffering=1) if a.out else None
    while True:
        try:
            sock, rest = connect(a.url, a.token)
            print(f"# connected {a.url}", file=sys.stderr, flush=True)
            for payload in frames(sock, rest):
                line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "data": json.loads(payload)}, ensure_ascii=False)
                print(line, flush=True)
                if fh:
                    fh.write(line + "\n")
                if a.once:
                    return
        except Exception as e:
            print(f"# reconnect in 3s: {e}", file=sys.stderr, flush=True)
            if a.once:
                sys.exit(1)
            time.sleep(3)

if __name__ == "__main__":
    main()
