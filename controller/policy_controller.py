import time
import json
import ipaddress

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.ofproto import ofproto_v1_3

from ryu.lib.packet import (
    packet,
    ethernet,
    ether_types,
    ipv4,
)

from ryu.lib import hub

from ryu.app.wsgi import (
    WSGIApplication,
    ControllerBase,
    route,
)

from webob import Response


# ============================================================
# POLICY CONTROLLER
# ============================================================

class PolicyController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        "wsgi": WSGIApplication
    }

    # --------------------------------------------------------
    # AUTOMATIC DETECTION CONFIGURATION
    # --------------------------------------------------------

    # How often to request OpenFlow port statistics
    MONITOR_INTERVAL = 2

    # Maximum packets received on a switch port during
    # one monitoring interval before blocking the host
    PACKET_THRESHOLD = 20

    # Duration of automatic/manual blocks in seconds
    BLOCK_DURATION = 15

    # --------------------------------------------------------

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        # Learning switch table
        self.mac_to_port = {}

        # Connected OpenFlow switches
        self.datapaths = {}

        # Map switch ports to host IP addresses
        self.port_to_ips = {}

        # Previous OpenFlow port statistics
        self.previous_stats = {}

        # Blocked IP addresses and expiration timestamps
        self.blocked_ips = {}

        # WSGI REST API
        wsgi = kwargs["wsgi"]

        wsgi.register(
            PolicyAPI,
            {
                "policy_controller": self
            }
        )

        # Start automatic traffic monitoring
        self.monitor_thread = hub.spawn(
            self.monitor_traffic
        )

        self.logger.info(
            "Automatic Policy Controller started"
        )

    # ========================================================
    # FLOW INSTALLATION
    # ========================================================

    def add_flow(
        self,
        datapath,
        priority,
        match,
        actions
    ):

        parser = datapath.ofproto_parser

        instructions = [
            parser.OFPInstructionActions(
                datapath.ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                instructions=instructions,
            )
        )

    # ========================================================
    # SWITCH CONNECTION
    # ========================================================

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures,
        CONFIG_DISPATCHER
    )
    def switch_features_handler(self, ev):

        dp = ev.msg.datapath

        self.datapaths[dp.id] = dp

        parser = dp.ofproto_parser

        # Table-miss flow:
        # Send unknown packets to the controller
        self.add_flow(
            dp,
            0,
            parser.OFPMatch(),
            [
                parser.OFPActionOutput(
                    dp.ofproto.OFPP_CONTROLLER,
                    dp.ofproto.OFPCML_NO_BUFFER
                )
            ]
        )

        # A switch joining after a block was created must inherit active
        # source/destination drop rules as well.
        for blocked_ip in list(self.blocked_ips):
            self._install_ip_drop_rules(dp, blocked_ip)

        self.logger.info(
            "Switch connected: %s",
            dp.id
        )

    # ========================================================
    # DATAPATH STATE CHANGES
    # ========================================================

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER]
    )
    def state_change_handler(self, ev):

        dp = ev.datapath

        if ev.state == MAIN_DISPATCHER:

            self.datapaths[dp.id] = dp

        elif ev.state == DEAD_DISPATCHER:

            if dp.id in self.datapaths:
                del self.datapaths[dp.id]

            if dp.id in self.previous_stats:
                del self.previous_stats[dp.id]

            self.logger.info(
                "Switch disconnected: %s",
                dp.id
            )

    # ========================================================
    # PACKET IN HANDLER
    # ========================================================

    @set_ev_cls(
        ofp_event.EventOFPPacketIn,
        MAIN_DISPATCHER
    )
    def packet_in_handler(self, ev):

        msg = ev.msg

        dp = msg.datapath

        parser = dp.ofproto_parser

        ofp = dp.ofproto

        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)

        eth = pkt.get_protocol(
            ethernet.ethernet
        )

        # Ignore packets without Ethernet header
        if eth is None:
            return

        # Ignore LLDP packets
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # ----------------------------------------------------
        # LEARN MAC ADDRESS
        # ----------------------------------------------------

        table = self.mac_to_port.setdefault(
            dp.id,
            {}
        )

        table[eth.src] = in_port

        # ----------------------------------------------------
        # LEARN IP ADDRESS
        # ----------------------------------------------------

        ip_pkt = pkt.get_protocol(
            ipv4.ipv4
        )

        if ip_pkt:

            port_key = (
                dp.id,
                in_port
            )

            ip_set = self.port_to_ips.setdefault(
                port_key,
                set()
            )

            ip_set.add(ip_pkt.src)

        # ----------------------------------------------------
        # LEARNING SWITCH
        # ----------------------------------------------------

        out_port = table.get(
            eth.dst,
            ofp.OFPP_FLOOD
        )

        actions = [
            parser.OFPActionOutput(
                out_port
            )
        ]

        # Install forwarding rule if destination is known
        if out_port != ofp.OFPP_FLOOD:

            match = parser.OFPMatch(
                in_port=in_port,
                eth_src=eth.src,
                eth_dst=eth.dst
            )

            self.add_flow(
                dp,
                1,
                match,
                actions
            )

        # ----------------------------------------------------
        # SEND PACKET OUT
        # ----------------------------------------------------

        data = (
            msg.data
            if msg.buffer_id == ofp.OFP_NO_BUFFER
            else None
        )

        dp.send_msg(
            parser.OFPPacketOut(
                datapath=dp,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=data
            )
        )

    # ========================================================
    # AUTOMATIC MONITORING LOOP
    # ========================================================

    def monitor_traffic(self):

        while True:

            # Request statistics from all switches
            for dp in list(
                self.datapaths.values()
            ):

                self.request_port_stats(dp)

            # Check whether any block has expired
            self.check_block_expirations()

            hub.sleep(
                self.MONITOR_INTERVAL
            )

    # ========================================================
    # REQUEST OPENFLOW PORT STATISTICS
    # ========================================================

    def request_port_stats(self, datapath):

        parser = datapath.ofproto_parser

        ofp = datapath.ofproto

        request = parser.OFPPortStatsRequest(
            datapath,
            0,
            ofp.OFPP_ANY
        )

        datapath.send_msg(
            request
        )

    # ========================================================
    # HANDLE PORT STATISTICS REPLY
    # ========================================================

    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER
    )
    def port_stats_reply_handler(self, ev):

        dp = ev.msg.datapath

        dpid = dp.id

        body = ev.msg.body

        # Previous counters for this switch
        previous = self.previous_stats.setdefault(
            dpid,
            {}
        )

        for stat in body:

            port_no = stat.port_no

            # Ignore special OpenFlow ports
            if port_no >= dp.ofproto.OFPP_MAX:
                continue

            current_packets = stat.rx_packets

            current_bytes = stat.rx_bytes

            # ------------------------------------------------
            # FIRST STATISTICS SAMPLE
            # ------------------------------------------------

            if port_no not in previous:

                previous[port_no] = {
                    "packets": current_packets,
                    "bytes": current_bytes
                }

                continue

            # ------------------------------------------------
            # CALCULATE PACKET DELTA
            # ------------------------------------------------

            previous_packets = previous[
                port_no
            ]["packets"]

            packet_delta = (
                current_packets - previous_packets
            )

            # Update baseline
            previous[port_no] = {
                "packets": current_packets,
                "bytes": current_bytes
            }

            # Counter reset or wraparound
            if packet_delta < 0:
                continue

            # ------------------------------------------------
            # CHECK AUTOMATIC BLOCKING RULE
            # ------------------------------------------------

            if packet_delta > self.PACKET_THRESHOLD:

                port_key = (
                    dpid,
                    port_no
                )

                ips = self.port_to_ips.get(
                    port_key,
                    set()
                )

                for ip in list(ips):

                    # Do not repeatedly block an IP
                    if ip in self.blocked_ips:
                        continue

                    self.logger.warning(
                        "Suspicious traffic detected: "
                        "IP=%s packets=%s threshold=%s",
                        ip,
                        packet_delta,
                        self.PACKET_THRESHOLD
                    )

                    # Automatically block suspicious IP
                    self.set_ip_block(
                        ip,
                        "block"
                    )

    # ========================================================
    # INSTALL BOTH DIRECTIONS OF AN IP DROP RULE
    # ========================================================

    def _install_ip_drop_rules(self, dp, ip):
        parser = dp.ofproto_parser
        for field in ("ipv4_src", "ipv4_dst"):
            match = parser.OFPMatch(eth_type=0x0800, **{field: ip})
            dp.send_msg(
                parser.OFPFlowMod(
                    datapath=dp,
                    priority=100,
                    match=match,
                    instructions=[],
                )
            )

    # ========================================================
    # BLOCK / UNBLOCK IP
    # ========================================================

    def set_ip_block(
        self,
        ip,
        action
    ):

        changed = 0

        # ----------------------------------------------------
        # BLOCK IP
        # ----------------------------------------------------

        if action == "block":

            # Do not reinstall the same block
            if ip in self.blocked_ips:

                self.logger.info(
                    "IP already blocked: %s",
                    ip
                )

                return 0

            # Set block expiration time
            expiry = (
                time.monotonic() + self.BLOCK_DURATION
            )

            self.blocked_ips[ip] = expiry

            # Install block flow on every switch
            for dp in list(
                self.datapaths.values()
            ):

                self._install_ip_drop_rules(dp, ip)
                changed += 1

            self.logger.warning(
                "BLOCKED IP: %s for %s seconds",
                ip,
                self.BLOCK_DURATION
            )

        # ----------------------------------------------------
        # UNBLOCK IP
        # ----------------------------------------------------

        elif action == "unblock":

            # Remove block status
            if ip in self.blocked_ips:

                del self.blocked_ips[ip]

            # Delete block flow from every switch
            for dp in list(
                self.datapaths.values()
            ):

                parser = dp.ofproto_parser

                ofp = dp.ofproto

                # Delete source IP block flow
                match_src = parser.OFPMatch(
                    eth_type=0x0800,
                    ipv4_src=ip
                )

                dp.send_msg(
                    parser.OFPFlowMod(
                        datapath=dp,
                        command=ofp.OFPFC_DELETE,
                        priority=100,
                        match=match_src,
                        out_port=ofp.OFPP_ANY,
                        out_group=ofp.OFPG_ANY
                    )
                )

                # Delete destination IP block flow
                match_dst = parser.OFPMatch(
                    eth_type=0x0800,
                    ipv4_dst=ip
                )

                dp.send_msg(
                    parser.OFPFlowMod(
                        datapath=dp,
                        command=ofp.OFPFC_DELETE,
                        priority=100,
                        match=match_dst,
                        out_port=ofp.OFPP_ANY,
                        out_group=ofp.OFPG_ANY
                    )
                )

                changed += 1

            self.logger.info(
                "UNBLOCKED IP: %s",
                ip
            )

        return changed

    # ========================================================
    # AUTOMATIC BLOCK EXPIRATION
    # ========================================================

    def check_block_expirations(self):

        current_time = time.monotonic()

        expired_ips = []

        for ip, expiry in list(
            self.blocked_ips.items()
        ):

            if current_time >= expiry:

                expired_ips.append(
                    ip
                )

        # Unblock expired IP addresses
        for ip in expired_ips:

            self.logger.info(
                "Block duration expired for IP: %s",
                ip
            )

            self.set_ip_block(
                ip,
                "unblock"
            )


# ============================================================
# REST API
# ============================================================

class PolicyAPI(ControllerBase):

    def __init__(
        self,
        req,
        link,
        data,
        **config
    ):

        super().__init__(
            req,
            link,
            data,
            **config
        )

        self.policy_controller = data[
            "policy_controller"
        ]

    # --------------------------------------------------------
    # APPLY POLICY
    # --------------------------------------------------------

    def _apply_policy(
        self,
        ip,
        action
    ):

        if action not in ("block", "unblock"):
            return Response(status=400, body=b"Action must be block or unblock")

        try:
            ip = str(ipaddress.IPv4Address(ip))

        except ValueError:

            return Response(
                status=400,
                body=b"Invalid IPv4 address"
            )

        count = self.policy_controller.set_ip_block(
            ip,
            action
        )

        result = {
            "action": action,
            "ip": ip,
            "switches_updated": count
        }

        return Response(
            content_type="application/json",
            body=json.dumps(
                result
            ).encode()
        )

    # --------------------------------------------------------
    # BLOCK API
    # --------------------------------------------------------

    @route(
        "block",
        "/policy/block/{ip}",
        methods=["POST"]
    )
    def block(
        self,
        req,
        **kwargs
    ):

        return self._apply_policy(
            kwargs["ip"],
            "block"
        )

    # --------------------------------------------------------
    # UNBLOCK API
    # --------------------------------------------------------

    @route(
        "unblock",
        "/policy/unblock/{ip}",
        methods=["POST"]
    )
    def unblock(
        self,
        req,
        **kwargs
    ):

        return self._apply_policy(
            kwargs["ip"],
            "unblock"
        )
