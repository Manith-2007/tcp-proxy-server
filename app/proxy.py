"""Concurrent TCP proxy with connection-count-triggered Ryu blocking.

The connection threshold is per client IP and counts accepted TCP sessions.
Once the threshold is reached, the proxy asks Ryu to block that client. The
controller applies the temporary block and expires it after BLOCK_DURATION.
"""

import argparse
import os
import socket
import threading
import time
import urllib.error
import urllib.request

LISTEN_HOST = os.getenv("PROXY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("PROXY_LISTEN_PORT", "9000"))
SERVER_HOST = os.getenv("SERVER_HOST", "10.0.0.3")
SERVER_PORT = int(os.getenv("SERVER_PORT", "9001"))
RYU_API = os.getenv("RYU_API", "http://192.168.100.1:8080").rstrip("/")
CONNECTION_THRESHOLD = int(os.getenv("CONNECTION_THRESHOLD", "4"))
BLOCK_DURATION = int(os.getenv("BLOCK_DURATION", "15"))

client_connection_counts = {}
last_block_notification = {}
connection_lock = threading.Lock()


def forward(source, destination, direction):
    """Forward one TCP stream and propagate half-close when EOF is seen."""
    try:
        while True:
            data = source.recv(4096)
            if not data:
                break
            destination.sendall(data)
            print(f"[PROXY] {direction}: forwarded {len(data)} bytes")
    except OSError as exc:
        print(f"[PROXY] {direction} forwarding error: {exc}")
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def notify_controller(ip, action):
    """Ask the Ryu REST API to block/unblock a client IPv4 address."""
    url = f"{RYU_API}/policy/{action}/{ip}"
    request = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = response.read().decode("utf-8", errors="replace")
            print(f"[SDN] Controller response: {result}")
            return response.status == 200
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"[SDN] Failed to notify controller: {exc}")
        return False


def handle_client(client_socket, client_address, server_host, server_port):
    print(f"[PROXY] Client connected: {client_address}")
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.settimeout(10)
        server_socket.connect((server_host, server_port))
        server_socket.settimeout(None)
        print(f"[PROXY] Connected to server {server_host}:{server_port}")

        client_to_server = threading.Thread(
            target=forward,
            args=(client_socket, server_socket, "CLIENT -> SERVER"),
            daemon=True,
        )
        server_to_client = threading.Thread(
            target=forward,
            args=(server_socket, client_socket, "SERVER -> CLIENT"),
            daemon=True,
        )
        client_to_server.start()
        server_to_client.start()
        client_to_server.join()
        server_to_client.join()
    except OSError as exc:
        print(f"[PROXY] Connection error for {client_address}: {exc}")
    finally:
        client_socket.close()
        server_socket.close()
        print(f"[PROXY] Client disconnected: {client_address}")


def main():
    global RYU_API
    parser = argparse.ArgumentParser(description="Concurrent TCP proxy with Ryu threshold blocking")
    parser.add_argument("--listen-host", default=LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=LISTEN_PORT)
    parser.add_argument("--server-host", default=SERVER_HOST)
    parser.add_argument("--server-port", type=int, default=SERVER_PORT)
    parser.add_argument("--ryu-api", default=RYU_API)
    parser.add_argument("--threshold", type=int, default=CONNECTION_THRESHOLD)
    parser.add_argument("--block-duration", type=int, default=BLOCK_DURATION,
                        help="Controller block duration; used to re-arm threshold notifications")
    args = parser.parse_args()

    if not (1 <= args.listen_port <= 65535 and 1 <= args.server_port <= 65535):
        parser.error("ports must be between 1 and 65535")
    if args.threshold < 1:
        parser.error("threshold must be at least 1")
    if args.block_duration < 1:
        parser.error("block duration must be at least 1 second")

    RYU_API = args.ryu_api.rstrip("/")

    proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    proxy.bind((args.listen_host, args.listen_port))
    proxy.listen(128)
    print(f"[PROXY] Listening on {args.listen_host}:{args.listen_port}")
    print(f"[PROXY] Upstream: {args.server_host}:{args.server_port}")
    print(f"[PROXY] Ryu API: {RYU_API}; block threshold: {args.threshold} connections/IP; expected block duration: {args.block_duration}s")

    try:
        while True:
            client_socket, client_address = proxy.accept()
            client_ip = client_address[0]
            with connection_lock:
                count = client_connection_counts.get(client_ip, 0) + 1
                client_connection_counts[client_ip] = count
                now = time.monotonic()
                last_notified = last_block_notification.get(client_ip)
                should_notify = (
                    count >= args.threshold
                    and (last_notified is None or now - last_notified >= args.block_duration)
                )
                if should_notify:
                    # Reserve notification before making the REST request so
                    # simultaneous accepts do not issue duplicate requests.
                    last_block_notification[client_ip] = now

            print(f"[PROXY] Client {client_ip} connection count: {count}")
            if should_notify:
                print(f"[SDN] Threshold reached for {client_ip}; requesting block")
                if not notify_controller(client_ip, "block"):
                    # Permit a later accepted connection to retry if Ryu was
                    # temporarily unavailable.
                    with connection_lock:
                        last_block_notification.pop(client_ip, None)

            worker = threading.Thread(
                target=handle_client,
                args=(client_socket, client_address, args.server_host, args.server_port),
                daemon=True,
            )
            worker.start()
    except KeyboardInterrupt:
        print("\n[PROXY] Shutting down")
    finally:
        proxy.close()


if __name__ == "__main__":
    main()
