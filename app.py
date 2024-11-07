import networkx as nx
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, ether_types
from ryu.lib import hub
import time
import logging

class AdvancedTrafficManager(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(AdvancedTrafficManager, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.packet_counts = {}
        self.graph = nx.DiGraph()
        self.monitor_thread = hub.spawn(self._monitor)

        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        self.mac_to_port.setdefault(datapath.id, {})
        parser = datapath.ofproto_parser

        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER, datapath.ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

        # Initialize graph
        for dpid in self.datapaths:
            self.graph.add_node(dpid)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match, instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # Ignore LLDP packets
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        self.logger.info("Packet in DPID %s SRC %s DST %s IN_PORT %s", dpid, src, dst, in_port)

        # Learn MAC address to avoid flooding next time
        self.mac_to_port[dpid][src] = in_port

        out_port = None
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            # Calculate the best path with load balancing
            next_hop = self._calculate_best_path(dpid, src, dst)
            if next_hop:
                out_port = self.mac_to_port[next_hop][dst]
            else:
                out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install a flow to avoid future packet_in events
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(datapath, 1, match, actions, msg.buffer_id)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                return

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

        # Track the number of packets per switch
        if dpid not in self.packet_counts:
            self.packet_counts[dpid] = 0
        self.packet_counts[dpid] += 1

    def _calculate_best_path(self, dpid, src, dst):
        # Find the shortest path using Dijkstra's algorithm
        target_dpid = self._get_host_location(dst)
        if target_dpid is None:
            return None

        try:
            # Compute paths considering both load and path length
            shortest_path = nx.shortest_path(self.graph, source=dpid, target=target_dpid, weight='weight')
            if len(shortest_path) > 1:
                next_hop = self._get_least_loaded_next_hop(shortest_path)
                return next_hop
            else:
                return None
        except nx.NetworkXNoPath:
            self.logger.warning("No path found between DPID %s and DPID %s", dpid, target_dpid)
            return None

    def _get_least_loaded_next_hop(self, path):
        min_load = float('inf')
        selected_hop = None

        for i in range(1, len(path)):
            current_load = self._get_link_load(path[i - 1], path[i])
            if current_load < min_load:
                min_load = current_load
                selected_hop = path[i]

        return selected_hop

    def _get_link_load(self, src_dpid, dst_dpid):
        # Placeholder function to return the load on the link between src_dpid and dst_dpid
        # This could be based on port statistics, queue sizes, etc.
        return self.packet_counts.get(src_dpid, 0) + self.packet_counts.get(dst_dpid, 0)

    def _get_host_location(self, mac):
        for dpid, mac_table in self.mac_to_port.items():
            if mac in mac_table:
                return dpid
        return None

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_handler(self, ev):
        datapath = ev.msg.datapath
        body = ev.msg.body

        for stat in body:
            self.logger.info('Datapath %s Port %d RxBytes: %d TxBytes: %d',
                             datapath.id, stat.port_no, stat.rx_bytes, stat.tx_bytes)

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp)
                self.display_statistics()
            time.sleep(5)

    def _request_stats(self, datapath):
        self.logger.debug('Sending stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    def display_statistics(self):
        self.logger.info("\n=== Network Statistics ===")
        self.logger.info("Total Packets Handled by SDN Controller: %d", sum(self.packet_counts.values()))

        for dp_id, count in self.packet_counts.items():
            self.logger.info("Switch %s handled %d packets", dp_id, count)

        active_switches = [dp_id for dp_id, count in self.packet_counts.items() if count > 0]
        inactive_switches = [dp_id for dp_id in self.datapaths if dp_id not in active_switches]

        self.logger.info("Active Switches: %s", active_switches)
        self.logger.info("Inactive Switches: %s", inactive_switches)
