"""
win_task.py - remote test step for the Windows laptop (node 2) during two-laptop debugging.

Run from the repo root:  .venv\\Scripts\\python tools\\win_task.py
It talks to the shout.py node already running on this laptop through its local HTTP
API, performs the steps listed in TASK below, and writes what happened to results/
so the other laptop can read it after a git push. Standard library only.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8082
BASE = f"http://127.0.0.1:{PORT}"
STAMP = time.strftime("%H%M%S")
events = []
lock = threading.Lock()

# ---- what to do this time -------------------------------------------------
TASK = [
    ("kill_nodes", {}),                                   # stop every running node
    ("restart", {"extra": ["--peer", "1"]}),              # start the code now on disk
    ("sleep", 8),
    ("send", {"dst": 1, "text": "HELLO", "wait": 70}),    # three short messages to the Mac
    ("sleep", 3),
    ("send", {"dst": 1, "text": "HELLO", "wait": 70}),
    ("sleep", 3),
    ("send", {"dst": 1, "text": "HELLO", "wait": 70}),
]
# ---------------------------------------------------------------------------


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=10).read())


def cmd(body):
    req = urllib.request.Request(BASE + "/api/cmd", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def listen():
    try:
        with urllib.request.urlopen(BASE + "/api/events", timeout=3600) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    e = json.loads(line[5:])
                    if e.get("event") != "spectrum":
                        with lock:
                            events.append(e)
    except Exception as exc:                       # node restarted or stopped
        with lock:
            events.append({"event": "listener_ended", "text": str(exc), "time": time.time()})


def start_listener():
    threading.Thread(target=listen, daemon=True).start()
    time.sleep(1.5)


def wait_node(seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        try:
            return get("/api/status")
        except Exception:
            time.sleep(1)
    return None


def list_procs():
    """Python processes with their command lines (Windows: via PowerShell)."""
    if os.name != "nt":
        out = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True).stdout
        return [l.strip() for l in out.splitlines() if "python" in l.lower()]
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def kill_nodes():
    """Force-stop every node process (shout.py / run_engine.py), never this script."""
    killed = []
    for line in list_procs():
        pid, _, cmdline = line.partition("|") if os.name == "nt" else line.partition(" ")
        if ("shout.py" in cmdline or "run_engine.py" in cmdline) and str(os.getpid()) != pid.strip():
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid.strip()], capture_output=True)
            else:
                subprocess.run(["kill", "-9", pid.strip()])
            killed.append(line)
    time.sleep(3)
    return killed


def restart_node(extra=(), stacks_after=0):
    """Stop the running node (through its own API) and start the code now on disk."""
    try:
        cmd({"cmd": "shutdown"})
    except Exception:
        pass
    time.sleep(4)
    kill_nodes()
    log = open(os.path.join(ROOT, "results", f"win_node_{STAMP}.log"), "w")
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0     # detached, own process group
    argv = ["shout.py", "--node", "2", "--no-browser", *extra]
    if stacks_after:
        # same program, but after N seconds every thread's stack is written to a file:
        # shows exactly where a start-up that never finishes is waiting
        stacks = os.path.join(ROOT, "results", f"win_stacks_{STAMP}.txt")
        code = ("import faulthandler, sys, runpy; f = open(%r, 'w'); "
                "faulthandler.dump_traceback_later(%d, file=f); sys.argv = %r; "
                "runpy.run_path('shout.py', run_name='__main__')" % (stacks, stacks_after, argv))
        launch = [sys.executable, "-u", "-c", code]
    else:
        launch = [sys.executable, "-u", *argv]
    subprocess.Popen(launch, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    return wait_node()


def do_send(dst, text, wait):
    with lock:
        mark = len(events)
    cmd({"cmd": "send", "dst": dst, "text": text})
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.3)
        with lock:
            for e in events[mark:]:
                if e.get("event") == "message_status" and e.get("status") in ("delivered", "failed"):
                    return e["status"]
    return "no result in time"


def main():
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    report = {"stamp": STAMP, "steps": []}
    if wait_node(3) is not None:
        start_listener()
    for name, arg in TASK:
        if name == "send":
            result = do_send(arg["dst"], arg["text"], arg["wait"])
        elif name == "procs":
            result = list_procs()
        elif name == "kill_nodes":
            result = kill_nodes()
        elif name == "restart":
            status = restart_node(arg.get("extra", ()), arg.get("stacks_after", 0))
            result = "restarted" if status else "restart FAILED"
            start_listener()
        elif name == "cmd":
            result = cmd(arg)
        elif name == "sleep":
            time.sleep(arg)
            result = "ok"
        else:
            result = f"unknown step {name}"
        print(f"step {name} {arg}: {result}")
        report["steps"].append({"step": name, "arg": arg, "result": result})
    time.sleep(1)
    try:
        report["status"] = get("/api/status")
    except Exception as exc:
        report["status"] = {"error": str(exc)}
    with lock:
        report["events"] = [e for e in events]
    for extra_file in (f"win_node_{STAMP}.log", f"win_stacks_{STAMP}.txt"):
        fp = os.path.join(ROOT, "results", extra_file)
        if os.path.exists(fp):
            report[extra_file] = open(fp, encoding="utf-8", errors="replace").read()[-12000:]
    path = os.path.join(ROOT, "results", f"win_task_{STAMP}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("saved", path)


if __name__ == "__main__":
    main()
