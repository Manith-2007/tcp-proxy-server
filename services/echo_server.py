
import socket

HOST = "127.0.0.1"
PORT = 9001

def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(5)

        print(f"Echo service listening on {HOST}:{PORT}")

        while True:
            conn, addr = server.accept()
            with conn:
                print(f"Connection from {addr}")
                data = conn.recv(4096)

                if data:
                    print(f"Received: {data.decode(errors='replace')}")
                    conn.sendall(b"Echo service received: " + data)

if __name__ == "__main__":
    main()
