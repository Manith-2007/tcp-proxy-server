
import json
import logging
import socket
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_FILE = ROOT / "config" / "policies.json"
LOG_FILE = ROOT / "logs" / "proxy.log"

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8000
BUFFER_SIZE = 4096


def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )


def load_policy():
    with POLICY_FILE.open("r", encoding="utf-8") as file:
        policy = json.load(file)

    if policy.get("default_action") not in {"allow", "deny"}:
        raise ValueError("default_action must be 'allow' or 'deny'")

    for rule in policy.get("allowed_destinations", []):
        if rule.get("action") not in {"allow", "deny"}:
            raise ValueError("Each destination action must be 'allow' or 'deny'")
        if not isinstance(rule.get("port"), int) or not 1 <= rule["port"] <= 65535:
            raise ValueError("Each destination port must be between 1 and 65535")
        if not isinstance(rule.get("host"), str) or not rule["host"]:
            raise ValueError("Each destination must have a host")

    return policy


def destination_allowed(policy, host, port):
    for rule in policy.get("allowed_destinations", []):
        if rule["host"] == host and rule["port"] == port:
            return rule["action"] == "allow"

    return policy["default_action"] == "allow"


def relay(source, destination):
    try:
        while True:
            data = source.recv(BUFFER_SIZE)
            if not data:
                break
            destination.sendall(data)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle_client(client, address):
    upstream = None
    try:
        # This first version uses a fixed upstream for a controlled test.
        host, port = "127.0.0.1", 9001
        policy = load_policy()

        if not destination_allowed(policy, host, port):
            logging.warning(
                "DENY client=%s destination=%s:%s",
                address, host, port
            )
            client.sendall(b"Connection denied by proxy policy\n")
            return

        logging.info(
            "ALLOW client=%s destination=%s:%s",
            address, host, port
        )

        upstream = socket.create_connection((host, port), timeout=5)
        upstream.settimeout(None)

        t1 = threading.Thread(
            target=relay, args=(client, upstream), daemon=True
        )
        t2 = threading.Thread(
            target=relay, args=(upstream, client), daemon=True
        )
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logging.error("ERROR client=%s reason=%s", address, exc)
        try:
            client.sendall(b"Proxy error\n")
        except OSError:
            pass
    finally:
        client.close()
        if upstream is not None:
            upstream.close()


def main():
    setup_logging()
    policy = load_policy()
    logging.info("Loaded policy: default_action=%s", policy["default_action"])

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((LISTEN_HOST, LISTEN_PORT))
        server.listen(50)
        logging.info(
            "TCP proxy listening on %s:%s", LISTEN_HOST, LISTEN_PORT
        )

        while True:
            client, address = server.accept()
            threading.Thread(
                target=handle_client,
                args=(client, address),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
