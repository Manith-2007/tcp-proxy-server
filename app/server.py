import socket
import threading

HOST = "0.0.0.0"
PORT = 9001


def handle_client(conn, addr):
    print(f"[SERVER] Client connected: {addr}")

    try:
        while True:
            data = conn.recv(4096)

            if not data:
                break

            print(f"[SERVER] Received: {data.decode(errors='replace')}")

            # Echo received data back to the client
            conn.sendall(data)

    except OSError as e:
        print(f"[SERVER] Connection error: {e}")

    finally:
        conn.close()
        print(f"[SERVER] Client disconnected: {addr}")


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server.bind((HOST, PORT))
    server.listen()

    print(f"[SERVER] Listening on {HOST}:{PORT}")

    try:
        while True:
            conn, addr = server.accept()

            # Handle each client concurrently
            thread = threading.Thread(
                target=handle_client,
                args=(conn, addr),
                daemon=True,
            )

            thread.start()

    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down")

    finally:
        server.close()


if __name__ == "__main__":
    main()
