# analysis.awk - performance metrics from an NS-2 trace file (CN Lab Assignment 8 style)
#
#   awk -f analysis.awk out.tr
#   awk -v node=2 -f analysis.awk out.tr      # receiving node (default 2 = Laptop C)
#
# Trace columns:  $1 event (+ enqueue, - dequeue, r receive, d drop)   $2 time
#   $3 from-node  $4 to-node  $5 type  $6 size  $8 flow id  $9 src  $10 dst  $11 seq  $12 packet id
#
# Prints, per flow: packets/bytes received at `node`, throughput (kbit/s) over the flow's
# active time, average end-to-end delay, and the number of dropped packets.

BEGIN {
    if (node == "") node = 2
}

{
    event = $1; t = $2; from = $3; to = $4; type = $5; size = $6
    flow = $8; id = $12

    # remember the first time each packet appears anywhere (its send time)
    if (!(id in sent)) sent[id] = t

    if (event == "r" && to == node && type != "ack") {
        recv_pkts[flow]++
        recv_bytes[flow] += size
        if (!(flow in first)) first[flow] = t
        last[flow] = t
        delay_sum[flow] += t - sent[id]
    }
    if (event == "d") {
        drops[flow]++
        drops_total++
    }
}

END {
    printf("%-6s %10s %12s %16s %16s %8s\n", "flow", "packets", "bytes", "throughput_kbps", "avg_delay_ms", "drops")
    for (f in recv_pkts) {
        span = last[f] - first[f]
        thr = (span > 0) ? recv_bytes[f] * 8 / span / 1000 : 0
        printf("%-6s %10d %12d %16.2f %16.2f %8d\n", f, recv_pkts[f], recv_bytes[f], thr,
               1000 * delay_sum[f] / recv_pkts[f], drops[f] + 0)
    }
    printf("total drops: %d\n", drops_total + 0)
}
