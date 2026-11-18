# Repair notes

This repair intentionally retains the supplied features and file layout.

## Fixed

1. `app/proxy.py` had a syntax/indentation error: a second `accept()` loop was outside the `try` block and the original loop was duplicated. The program could not start. The accept loop is now one valid loop.
2. The connection-count threshold code is now part of that accept loop. It counts accepted connections by client IP and invokes the Ryu REST block endpoint when the configured threshold is reached.
3. The proxy now supports command-line and environment configuration for listener, upstream, Ryu URL, threshold and block duration, rather than requiring edits to hard-coded addresses.
4. Threshold notifications are re-armed after the configured block duration, so a client that reconnects after the controller expires its temporary block can be blocked again.
5. The Ryu controller uses a monotonic clock for block expiry and reapplies active drop rules to switches that connect while a block is active.
6. The controller REST handler validates action names and IPv4 addresses.
7. The two-client test now reads the full expected echo payload rather than assuming one `recv()` returns an entire TCP message.
8. The performance test validates positive client/message/payload values and valid port ranges before creating its worker pool/barrier.

## Preserved

- Concurrent bidirectional TCP forwarding.
- Interactive TCP client and concurrent echo server.
- Two-client concurrent test.
- Performance test options and measurements: concurrent clients, messages/client, payload size, RTT latency average/median/min/max/p95/p99, connection setup time, throughput and optional JSON output.
- Ryu OpenFlow 1.3 learning-switch behavior.
- Periodic OpenFlow port statistics collection.
- Automatic packet-threshold detection using learned host IPs per switch port.
- Automatic temporary blocking and expiry.
- Manual REST block/unblock API.
- Source-IP and destination-IP drop flows.
- Earlier `proxy/tcp_proxy.py`, `services/echo_server.py`, config and logs files from the archive.

## Verification performed here

- `python3 -m py_compile` passed for all project Python files.
- Local TCP proxy forwarding smoke test passed for four successive connections.
- The fourth connection triggered the expected mock REST request `/policy/block/127.0.0.1`.
- The performance script passed a local 5-client x 10-message echo test (50/50 messages completed).

The live Ryu/Open vSwitch automatic port-statistics path still needs to be run in the target Mininet environment. Syntax checks and mock REST tests do not replace that live integration test.
