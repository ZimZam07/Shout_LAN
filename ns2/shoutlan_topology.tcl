# shoutlan_topology.tcl - NS-2 wired SIMULATION modelled on the SHOUT LAN relay topology
# (CN Lab, Module 4 / Evaluation 2: "NS-2 topology designing").
#
#   n0 = Laptop A (TCP/FTP sender)        n3 = another sender (UDP/CBR)
#              \                         /
#               n1 = Laptop B (relay / router)
#                         |
#                   n2 = Laptop C (receiver)        n1 -> n2 is the bottleneck link
#
# This is a SIMULATION of ordinary wired links, NOT of the acoustic channel.
# Run (in WSL Ubuntu, after "sudo apt install ns2 nam"):
#     ns shoutlan_topology.tcl
#     awk -f analysis.awk out.tr
# Output files: out.tr (trace), out.nam (animation for nam)

# Step 0: simulator object and output files (same structure as Lab Assignment 8)
set ns [new Simulator]
set tracefile [open out.tr w]
$ns trace-all $tracefile
set namfile [open out.nam w]
$ns namtrace-all $namfile

# Step 1: nodes and links
set n0 [$ns node]
set n1 [$ns node]
set n2 [$ns node]
set n3 [$ns node]
$n0 label "Laptop A"
$n1 label "Laptop B (relay)"
$n2 label "Laptop C"
$n3 label "Other sender"

$ns duplex-link $n0 $n1 2Mb 10ms DropTail
$ns duplex-link $n3 $n1 2Mb 10ms DropTail
$ns duplex-link $n1 $n2 1Mb 20ms DropTail          ;# bottleneck: 2 x 2Mb in, 1Mb out
$ns queue-limit $n1 $n2 10                         ;# small queue -> drops under congestion

$ns duplex-link-op $n0 $n1 orient right-down
$ns duplex-link-op $n3 $n1 orient right-up
$ns duplex-link-op $n1 $n2 orient right
$ns duplex-link-op $n1 $n2 queuePos 0.5

# Step 2: agents (transport layer)
set tcp [new Agent/TCP]
$ns attach-agent $n0 $tcp
set sink [new Agent/TCPSink]
$ns attach-agent $n2 $sink
$ns connect $tcp $sink
$tcp set fid_ 1
$ns color 1 Blue

set udp [new Agent/UDP]
$ns attach-agent $n3 $udp
set null [new Agent/Null]
$ns attach-agent $n2 $null
$ns connect $udp $null
$udp set fid_ 2
$ns color 2 Red

# Step 3: applications
set ftp [new Application/FTP]
$ftp attach-agent $tcp

set cbr [new Application/Traffic/CBR]
$cbr attach-agent $udp
$cbr set packetSize_ 1000
$cbr set rate_ 0.8Mb

# Step 4: schedule
$ns at 0.5 "$cbr start"
$ns at 1.0 "$ftp start"
$ns at 9.0 "$ftp stop"
$ns at 9.5 "$cbr stop"
$ns at 10.0 "finish"

proc finish {} {
    global ns tracefile namfile
    $ns flush-trace
    close $tracefile
    close $namfile
    puts "Simulation finished: out.tr and out.nam written"
    exit 0
}

# Step 5: run
$ns run
