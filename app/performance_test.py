import argparse
import json
import socket
import threading
import time
import statistics
from concurrent.futures import ThreadPoolExecutor


def recv_exact(sock, size):
    """Receive exactly size bytes from a TCP socket."""

    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError(
                "Connection closed before all data was received"
            )

        data.extend(chunk)

    return bytes(data)


def percentile(values, p):
    """Calculate percentile from a list of values."""

    if not values:
        return 0

    values = sorted(values)

    index = int((p / 100) * (len(values) - 1))

    return values[index]


def client_worker(
    client_id,
    host,
    port,
    messages,
    payload_size,
    barrier
):
    """Connect to the server and measure message latency."""

    latencies = []

    connection_start = time.perf_counter()

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    sock.settimeout(30)

    try:

        # Establish TCP connection
        sock.connect((host, port))

        connection_time = (
            time.perf_counter() - connection_start
        ) * 1000

        # Fixed-size payload
        payload = b"x" * payload_size

        # Wait until all clients are ready
        barrier.wait(timeout=60)

        # Send messages and measure RTT
        for _ in range(messages):

            start = time.perf_counter()

            sock.sendall(payload)

            response = recv_exact(
                sock,
                payload_size
            )

            end = time.perf_counter()

            # Verify echoed data
            if response != payload:

                raise ValueError(
                    "Received response does not match sent data"
                )

            latency_ms = (
                end - start
            ) * 1000

            latencies.append(
                latency_ms
            )

        return {
            "client_id": client_id,
            "success": True,
            "latencies_ms": latencies,
            "connection_time_ms": connection_time,
            "messages_completed": len(latencies),
            "error": None
        }

    except Exception as e:

        # Prevent other clients from waiting forever
        try:
            barrier.abort()
        except Exception:
            pass

        return {
            "client_id": client_id,
            "success": False,
            "latencies_ms": latencies,
            "connection_time_ms": None,
            "messages_completed": len(latencies),
            "error": str(e)
        }

    finally:

        sock.close()


def main():

    parser = argparse.ArgumentParser(
        description="TCP Proxy Performance Evaluation"
    )

    parser.add_argument(
        "--host",
        required=True,
        help="Target server IP"
    )

    parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="Target TCP port"
    )

    parser.add_argument(
        "--clients",
        type=int,
        default=1,
        help="Number of concurrent clients"
    )

    parser.add_argument(
        "--messages",
        type=int,
        default=100,
        help="Messages per client"
    )

    parser.add_argument(
        "--payload-size",
        type=int,
        default=1024,
        help="Payload size in bytes"
    )

    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output file"
    )

    args = parser.parse_args()

    if args.clients < 1:
        parser.error("--clients must be at least 1")
    if args.messages < 1:
        parser.error("--messages must be at least 1")
    if args.payload_size < 1:
        parser.error("--payload-size must be at least 1 byte")
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")

    # Synchronize all clients before testing
    barrier = threading.Barrier(
        args.clients + 1
    )

    print("\n===================================")
    print(" TCP PERFORMANCE EVALUATION")
    print("===================================")

    print("Target:", args.host, args.port)
    print("Concurrent clients:", args.clients)
    print("Messages per client:", args.messages)
    print("Payload size:", args.payload_size, "bytes")

    # Start client workers
    with ThreadPoolExecutor(
        max_workers=args.clients
    ) as executor:

        futures = []

        for client_id in range(args.clients):

            future = executor.submit(
                client_worker,
                client_id,
                args.host,
                args.port,
                args.messages,
                args.payload_size,
                barrier
            )

            futures.append(future)

        # Start timer when all clients are ready
        start = time.perf_counter()

        try:

            barrier.wait(
                timeout=60
            )

        except threading.BrokenBarrierError:

            print("\nERROR: One or more clients failed to connect.")
            print("Check that the proxy/server is running.")

            return

        # Collect client results
        results = [
            future.result()
            for future in futures
        ]

        end = time.perf_counter()

    # Calculate elapsed time
    elapsed = end - start

    # Combine latencies
    all_latencies = []

    connection_times = []

    successful_clients = 0

    failed_clients = 0

    total_messages = 0

    for result in results:

        all_latencies.extend(
            result["latencies_ms"]
        )

        if result["success"]:

            successful_clients += 1

        else:

            failed_clients += 1

        if result["connection_time_ms"] is not None:

            connection_times.append(
                result["connection_time_ms"]
            )

        total_messages += (
            result["messages_completed"]
        )

    # Calculate throughput
    total_received_bytes = (
        total_messages * args.payload_size
    )

    throughput_mbps = (
        total_received_bytes * 8
    ) / (
        elapsed * 1_000_000
    )

    messages_per_second = (
        total_messages / elapsed
        if elapsed > 0
        else 0
    )

    # Calculate latency statistics
    avg_latency = (
        statistics.mean(all_latencies)
        if all_latencies
        else 0
    )

    median_latency = (
        statistics.median(all_latencies)
        if all_latencies
        else 0
    )

    min_latency = (
        min(all_latencies)
        if all_latencies
        else 0
    )

    max_latency = (
        max(all_latencies)
        if all_latencies
        else 0
    )

    p95_latency = percentile(
        all_latencies,
        95
    )

    p99_latency = percentile(
        all_latencies,
        99
    )

    avg_connection_time = (
        statistics.mean(connection_times)
        if connection_times
        else 0
    )

    # Results
    report = {

        "target": f"{args.host}:{args.port}",

        "concurrent_clients": args.clients,

        "messages_per_client": args.messages,

        "payload_size_bytes": args.payload_size,

        "successful_clients": successful_clients,

        "failed_clients": failed_clients,

        "total_messages_completed": total_messages,

        "elapsed_seconds": round(
            elapsed,
            4
        ),

        "throughput_mbps": round(
            throughput_mbps,
            4
        ),

        "messages_per_second": round(
            messages_per_second,
            2
        ),

        "latency_ms": {

            "average": round(
                avg_latency,
                4
            ),

            "median": round(
                median_latency,
                4
            ),

            "min": round(
                min_latency,
                4
            ),

            "max": round(
                max_latency,
                4
            ),

            "p95": round(
                p95_latency,
                4
            ),

            "p99": round(
                p99_latency,
                4
            )
        },

        "average_connection_time_ms": round(
            avg_connection_time,
            4
        )
    }

    print("\n===================================")
    print(" RESULTS")
    print("===================================")

    print(
        "Successful clients:",
        successful_clients
    )

    print(
        "Failed clients:",
        failed_clients
    )

    print(
        "Total messages:",
        total_messages
    )

    print(
        "Elapsed time:",
        round(elapsed, 4),
        "seconds"
    )

    print(
        "Throughput:",
        round(throughput_mbps, 4),
        "Mbps"
    )

    print(
        "Messages/sec:",
        round(messages_per_second, 2)
    )

    print("\nLatency:")

    print(
        "Average:",
        round(avg_latency, 4),
        "ms"
    )

    print(
        "Median:",
        round(median_latency, 4),
        "ms"
    )

    print(
        "Minimum:",
        round(min_latency, 4),
        "ms"
    )

    print(
        "Maximum:",
        round(max_latency, 4),
        "ms"
    )

    print(
        "95th percentile:",
        round(p95_latency, 4),
        "ms"
    )

    print(
        "99th percentile:",
        round(p99_latency, 4),
        "ms"
    )

    print(
        "\nAverage connection establishment:",
        round(avg_connection_time, 4),
        "ms"
    )

    # Save JSON report
    if args.output:

        with open(
            args.output,
            "w"
        ) as f:

            json.dump(
                report,
                f,
                indent=4
            )

        print(
            "\nReport saved to:",
            args.output
        )


if __name__ == "__main__":

    main()
