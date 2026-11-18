# TCP Proxy with SDN-Based Automatic Traffic Blocking

This is a repaired copy of the supplied project. The original features are retained: concurrent TCP forwarding, interactive client, multiclient test, performance benchmark with latency percentiles/throughput/JSON output, Ryu REST block/unblock, OpenFlow learning-switch forwarding, automatic port-statistics monitoring, threshold-based blocking, and timed unblock.

## Components

- `app/proxy.py`: concurrent TCP proxy. Counts accepted connections per client IPv4 address and asks Ryu to block that address after the configured threshold.
- `app/server.py`: concurrent TCP echo server.
- `app/client.py`: interactive TCP client.
- `app/multi_client_test.py`: two concurrent clients, three messages each.
- `app/performance_test.py`: concurrent benchmark with configurable clients/messages/payload, RTT latency (average/median/min/max/p95/p99), connection setup time, throughput and optional JSON report.
- `controller/policy_controller.py`: Ryu OpenFlow 1.3 learning switch, port-statistics monitor, automatic packet-threshold detection, source-and-destination IPv4 drop rules, timed expiry, and REST API.

The older `proxy/tcp_proxy.py`, `services/echo_server.py`, `config/` and `logs/` files from the supplied archive are preserved too; they are a separate earlier implementation and are not the entry points for the `app/` demo described below.

## Network assumptions

The app demo uses these addresses by default:

| Node | Address | Role |
|---|---|---|
| Client | `10.0.0.1` | Runs `app/client.py` or tests |
| Proxy | `10.0.0.2:9000` | Runs `app/proxy.py` |
| Server | `10.0.0.3:9001` | Runs `app/server.py` |
| Ryu REST | `192.168.100.1:8080` | Host-reachable controller REST address |
| Switch | `s1` | OpenFlow 1.3 switch |

Adjust the controller address to an IP reachable from the Mininet proxy host. Do not assume `127.0.0.1` inside a Mininet host points to the Ubuntu host.

## Start

Use separate terminals. Activate the Python environment containing the working Ryu installation in the controller terminal:

```bash
cd ~/tcp-proxy-project
source ~/ryu-clean/bin/activate   # use the actual environment where ryu-manager works
ryu-manager controller/policy_controller.py
```

Start your Mininet topology with the remote controller configured for OpenFlow 1.3. At the `mininet>` prompt, start the server and proxy:

```text
h3 python3 /absolute/path/to/tcp-proxy-project/app/server.py &
h2 python3 /absolute/path/to/tcp-proxy-project/app/proxy.py --listen-host 0.0.0.0 --listen-port 9000 --server-host 10.0.0.3 --server-port 9001 --ryu-api http://192.168.100.1:8080 --threshold 4 --block-duration 15 &
```

Replace `/absolute/path/to/tcp-proxy-project` and the Ryu REST IP with the actual values in the lab. The server and proxy hosts must be able to reach each other, and the proxy host must be able to reach the Ryu REST listener.

## Tests

From the client host (`h1`):

```text
h1 python3 /absolute/path/to/tcp-proxy-project/app/client.py
h1 python3 /absolute/path/to/tcp-proxy-project/app/multi_client_test.py
h1 python3 /absolute/path/to/tcp-proxy-project/app/performance_test.py --host 10.0.0.2 --port 9000 --clients 5 --messages 20 --payload-size 1024 --output /tmp/proxy-benchmark.json
```

The connection-count rule counts accepted TCP sessions per client IP. With `--threshold 4`, the fourth accepted connection triggers a REST request to Ryu. The Ryu controller installs source and destination IPv4 drop rules for `--block-duration` seconds (default 15). Once the proxy sees a later connection after that duration, it can trigger another block. The controller also independently monitors switch port packet deltas every 2 seconds and blocks learned IPs on ports whose receive-packet delta exceeds `PACKET_THRESHOLD` (default 20).

Manual REST operations, from a machine that can reach the controller:

```bash
curl -i -X POST http://127.0.0.1:8080/policy/block/10.0.0.1
curl -i -X POST http://127.0.0.1:8080/policy/unblock/10.0.0.1
```

## Configuration knobs

Controller constants in `controller/policy_controller.py`:

- `MONITOR_INTERVAL = 2` seconds
- `PACKET_THRESHOLD = 20` packets received per monitoring interval
- `BLOCK_DURATION = 15` seconds

Proxy command-line options:

- `--listen-host`, `--listen-port`
- `--server-host`, `--server-port`
- `--ryu-api`
- `--threshold`
- `--block-duration` (keep aligned with the controller's `BLOCK_DURATION`)

The proxy also accepts corresponding environment variables: `PROXY_LISTEN_HOST`, `PROXY_LISTEN_PORT`, `SERVER_HOST`, `SERVER_PORT`, `RYU_API`, `CONNECTION_THRESHOLD`, and `BLOCK_DURATION`.

## Important lab notes

- The packet-rate detector is a teaching/demo heuristic, not production DDoS detection. A legitimate burst can exceed the threshold. Tune `PACKET_THRESHOLD` for the topology and traffic used in the demo.
- The REST API has no authentication. Keep it bound/reachable only inside an isolated lab network; do not expose it to an untrusted network.
- A block is installed on every connected switch and is also applied to a switch that connects while the block is active.
- `app/performance_test.py` measures echo round-trip latency. Its throughput value counts payload bytes sent by clients, not both directions of wire traffic.
- The archive includes an older proxy under `proxy/`; the `app/` proxy is the one with the connection-threshold feature.

## Syntax verification

From the project root:

```bash
python3 -m py_compile app/*.py controller/policy_controller.py proxy/tcp_proxy.py services/echo_server.py
```

Ryu must be installed in a compatible Python environment before starting the controller. Syntax compilation does not prove that the controller can import or run against a live Ryu/Open vSwitch installation; verify it in the target lab.
