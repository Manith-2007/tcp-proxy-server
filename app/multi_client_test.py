import socket
import threading

PROXY_HOST = "10.0.0.2"
PROXY_PORT = 9000


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Connection closed before full echo arrived")
        data.extend(chunk)
    return bytes(data)


def client_worker(client_id):
    try:
        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        )

        sock.connect((PROXY_HOST, PROXY_PORT))

        print(f"[CLIENT {client_id}] Connected")

        for i in range(3):
            message = f"Client {client_id} message {i + 1}"

            sock.sendall(message.encode())

            response = recv_exact(sock, len(message.encode()))

            print(
                f"[CLIENT {client_id}] "
                f"Sent: {message} | "
                f"Received: {response.decode(errors='replace')}"
            )

        sock.close()

        print(f"[CLIENT {client_id}] Disconnected")

    except OSError as e:
        print(f"[CLIENT {client_id}] Error: {e}")


def main():
    threads = []

    # Start two clients concurrently
    for client_id in range(1, 3):
        thread = threading.Thread(
            target=client_worker,
            args=(client_id,),
        )

        threads.append(thread)
        thread.start()

    # Wait for both clients to finish
    for thread in threads:
        thread.join()

    print("[TEST] Both clients completed")


if __name__ == "__main__":
    main()
