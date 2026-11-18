import socket

PROXY_HOST = "10.0.0.2"
PROXY_PORT = 9000


def main():
    client = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )

    try:
        client.connect((PROXY_HOST, PROXY_PORT))

        print(
            f"[CLIENT] Connected to proxy "
            f"{PROXY_HOST}:{PROXY_PORT}"
        )

        while True:
            message = input("Enter message (or 'quit'): ")

            if message.lower() == "quit":
                break

            client.sendall(message.encode())

            response = client.recv(4096)

            if not response:
                print("[CLIENT] Connection closed")
                break

            print(
                "[CLIENT] Received:",
                response.decode(errors="replace"),
            )

    except OSError as e:
        print(f"[CLIENT] Error: {e}")

    finally:
        client.close()
        print("[CLIENT] Disconnected")


if __name__ == "__main__":
    main()
