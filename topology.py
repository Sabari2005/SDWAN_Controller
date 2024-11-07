from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel


class MyTopo(Topo):
    def build(self):
        # Add switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')
        s5 = self.addSwitch('s5')

        # Add hosts
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')
        h5 = self.addHost('h5')
        h6 = self.addHost('h6')
        h7 = self.addHost('h7')
        h8 = self.addHost('h8')
        h9 = self.addHost('h9')
        h10 = self.addHost('h10')

        # Add links between hosts and switches
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s2)
        self.addLink(h4, s2)
        self.addLink(h5, s3)
        self.addLink(h6, s3)
        self.addLink(h7, s4)
        self.addLink(h8, s4)
        self.addLink(h9, s5)
        self.addLink(h10, s5)

        # Add links between switches for partial mesh topology
        self.addLink(s1, s2)
        self.addLink(s2, s3)
        self.addLink(s3, s4)
        self.addLink(s4, s5)
        self.addLink(s5, s1)


# Define a function to run Mininet with the custom topology
def run():
    topo = MyTopo()
    net = Mininet(topo=topo, controller=RemoteController)
    net.start()

    # Optional: enable STP (Spanning Tree Protocol) on all switches
    for i in range(1, 6):
        switch = net.get(f's{i}')
        switch.cmd(f'ovs-vsctl set bridge s{i} stp_enable=true')

    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()
