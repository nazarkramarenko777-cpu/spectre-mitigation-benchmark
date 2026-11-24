"""Benchmark: cost of Spectre/Meltdown mitigations on a Windows desktop.

Runs the same set of workloads several times and writes every single
measurement to a CSV file, plus a JSON file describing the system and the
mitigation state reported by the kernel.

Usage (from the project folder):
    python bench.py --condition on      # mitigations as Windows ships them
    python bench.py --condition off     # after mitigations_off.bat + reboot

The script refuses to run if --condition does not match the state the
kernel reports (use --force to override, not recommended).

Workloads, in two groups:
  kernel-heavy (expected to slow down: they cross into the kernel often)
    file_create_delete   create, read back and delete many small files
    file_stat            query attributes of one file many times
    zip_extract          extract an archive of many small files
    process_spawn        start and wait for many short processes
    thread_pingpong      two threads hand control back and forth
  control (expected NOT to slow down: pure computation in user space)
    cpu_python           integer arithmetic in the Python interpreter
    cpu_sha256           SHA-256 over an in-memory buffer
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import winreg
import time
import zipfile
from pathlib import Path

import mitigation_status

HERE = Path(__file__).resolve().parent
ORDER_SEED = 2026  # same workload order in every session and condition

# Workload sizes. "quick" is only for a smoke test of the script.
SIZES = {
    "full": {
        "files": 3000,
        "stats": 150_000,
        "zip_files": 6000,
        "spawns": 200,
        "pingpong": 60_000,
        "cpu_loop": 6_000_000,
        "sha_mb": 64,
        "sha_passes": 8,
    },
    "quick": {
        "files": 200,
        "stats": 5_000,
        "zip_files": 300,
        "spawns": 10,
        "pingpong": 1_000,
        "cpu_loop": 200_000,
        "sha_mb": 8,
        "sha_passes": 1,
    },
}

GROUP = {
    "file_create_delete": "kernel-heavy",
    "file_stat": "kernel-heavy",
    "zip_extract": "kernel-heavy",
    "process_spawn": "kernel-heavy",
    "thread_pingpong": "kernel-heavy",
    "cpu_python": "control",
    "cpu_sha256": "control",
}


# ---------------------------------------------------------------- workloads
# Each workload returns (seconds, operations). Only the core loop is timed.

def w_file_create_delete(work, s):
    d = work / "files"
    d.mkdir()
    n = s["files"]
    payload = b"x" * 100
    names = [d / f"f{i:05d}.txt" for i in range(n)]
    t0 = time.perf_counter()
    for p in names:
        with open(p, "wb") as f:
            f.write(payload)
    for p in names:
        with open(p, "rb") as f:
            f.read()
    for p in names:
        os.remove(p)
    t = time.perf_counter() - t0
    d.rmdir()
    return t, n * 3


def w_file_stat(work, s):
    p = work / "stat_target.txt"
    p.write_bytes(b"stat")
    n = s["stats"]
    st = os.stat
    t0 = time.perf_counter()
    for _ in range(n):
        st(p)
    t = time.perf_counter() - t0
    p.unlink()
    return t, n


def _make_zip(path, n):
    rng = random.Random(42)
    words = ["cache", "branch", "kernel", "pipeline", "register", "memory", "fetch", "decode"]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for i in range(n):
            text = " ".join(rng.choice(words) for _ in range(rng.randint(60, 300)))
            z.writestr(f"src/mod{i // 200:03d}/file{i:05d}.txt", text)


def w_zip_extract(work, s):
    archive = work / "small_files.zip"
    if not archive.exists():
        _make_zip(archive, s["zip_files"])
    out = work / "unzipped"
    t0 = time.perf_counter()
    with zipfile.ZipFile(archive) as z:
        z.extractall(out)
    t = time.perf_counter() - t0
    shutil.rmtree(out)
    return t, s["zip_files"]


def w_process_spawn(work, s):
    n = s["spawns"]
    cmd = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/c", "exit", "0"]
    t0 = time.perf_counter()
    for _ in range(n):
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    t = time.perf_counter() - t0
    return t, n


def w_thread_pingpong(work, s):
    n = s["pingpong"]
    ping, pong = threading.Event(), threading.Event()

    def partner():
        for _ in range(n):
            ping.wait()
            ping.clear()
            pong.set()

    th = threading.Thread(target=partner)
    th.start()
    t0 = time.perf_counter()
    for _ in range(n):
        ping.set()
        pong.wait()
        pong.clear()
    t = time.perf_counter() - t0
    th.join()
    return t, n


def w_cpu_python(work, s):
    n = s["cpu_loop"]
    t0 = time.perf_counter()
    acc = 0
    for i in range(n):
        acc = (acc + i * i) % 1_000_003
    t = time.perf_counter() - t0
    if acc < 0:  # keeps the loop result "used"
        print(acc)
    return t, n


def w_cpu_sha256(work, s):
    buf = bytes(range(256)) * (s["sha_mb"] * 4096)
    t0 = time.perf_counter()
    for _ in range(s["sha_passes"]):
        hashlib.sha256(buf).digest()
    t = time.perf_counter() - t0
    return t, s["sha_passes"]


WORKLOADS = {
    "file_create_delete": w_file_create_delete,
    "file_stat": w_file_stat,
    "zip_extract": w_zip_extract,
    "process_spawn": w_process_spawn,
    "thread_pingpong": w_thread_pingpong,
    "cpu_python": w_cpu_python,
    "cpu_sha256": w_cpu_sha256,
}


# ---------------------------------------------------------------- system info

def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception as exc:  # info only, never fatal
        return f"unavailable: {exc}"


def _microcode():
    """CPU microcode revision as Windows loaded it (high dword of 'Update Revision')."""
    try:
        key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            raw = winreg.QueryValueEx(key, "Update Revision")[0]
        return hex(int.from_bytes(bytes(raw), "little") >> 32)
    except OSError as exc:
        return f"unavailable: {exc}"


def system_info(work):
    ps = ["powershell", "-NoProfile", "-Command"]
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "windows_build": platform.version(),
        "cpu": _run(ps + ["(Get-CimInstance Win32_Processor).Name"]),
        "cpu_microcode_revision": _microcode(),
        "logical_cpus": os.cpu_count(),
        "ram_gb": _run(ps + ["[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)"]),
        "power_plan": _run(["powercfg", "/getactivescheme"]),
        "work_dir": str(work),
        "work_disk": _run(ps + [
            f"$d=(Get-Partition -DriveLetter '{str(work)[0]}' | Get-Disk);"
            "\"$($d.FriendlyName) | $($d.BusType)\""]),
        "uptime_minutes": _run(ps + [
            "[math]::Round(((Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalMinutes,1)"]),
        "defender_realtime": _run(ps + ["(Get-MpComputerStatus).RealTimeProtectionEnabled"]),
    }


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--condition", required=True, choices=["on", "off"],
                    help="on = Windows default mitigations, off = mitigations disabled")
    ap.add_argument("--rounds", type=int, default=15, help="measured rounds (default 15)")
    ap.add_argument("--warmup", type=int, default=2, help="warm-up rounds, saved but flagged (default 2)")
    ap.add_argument("--settle", type=int, default=60, help="seconds to wait before starting (default 60)")
    ap.add_argument("--quick", action="store_true", help="tiny sizes, only to test the script")
    ap.add_argument("--force", action="store_true", help="run even if detected state != --condition")
    ap.add_argument("--out", default=str(HERE / "results"), help="output folder")
    ap.add_argument("--workdir", default=tempfile.gettempdir(),
                    help="where temporary test files go (default: system TEMP, on the system SSD)")
    args = ap.parse_args()

    st = mitigation_status.get_status()
    print("Mitigation state reported by Windows:")
    print("  " + mitigation_status.short_summary(st))
    if st["overall"] != args.condition and not args.force:
        sys.exit(f"\nSTOP: you said --condition {args.condition}, but Windows reports '{st['overall']}'.\n"
                 "Did you reboot after changing the setting? Nothing was measured.")

    size_name = "quick" if args.quick else "full"
    sizes = SIZES[size_name]
    out = Path(args.out)
    if args.quick:
        out = out / "smoke_test"  # never mixed with real data
    out.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session = f"{args.condition}_{stamp}"
    work = Path(args.workdir) / "spectre_bench_work" / session
    work.mkdir(parents=True)

    info = {
        "session": session,
        "condition": args.condition,
        "size_profile": size_name,
        "sizes": sizes,
        "rounds": args.rounds,
        "warmup_rounds": args.warmup,
        "order_seed": ORDER_SEED,
        "forced": args.force,
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "mitigations": st,
        "system": system_info(work),
    }

    if args.settle > 0:
        print(f"\nClose other programs and do not touch the PC. Starting in {args.settle} s...")
        time.sleep(args.settle)

    csv_path = out / f"raw_{session}.csv"
    fields = ["session", "condition", "round", "warmup", "position", "workload", "group",
              "seconds", "operations", "us_per_op", "timestamp"]
    rng = random.Random(ORDER_SEED)
    total = args.warmup + args.rounds
    names = list(WORKLOADS)
    t_start = time.perf_counter()

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        wr.writeheader()
        for r in range(1, total + 1):
            warm = r <= args.warmup
            order = names[:]
            rng.shuffle(order)  # different order each round, same across sessions
            for pos, name in enumerate(order, 1):
                secs, ops = WORKLOADS[name](work, sizes)
                wr.writerow({
                    "session": session, "condition": args.condition,
                    "round": r, "warmup": int(warm), "position": pos,
                    "workload": name, "group": GROUP[name],
                    "seconds": f"{secs:.6f}", "operations": ops,
                    "us_per_op": f"{secs / ops * 1e6:.3f}",
                    "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                })
                fh.flush()
            label = "warm-up" if warm else "measured"
            print(f"  round {r}/{total} done ({label}), {time.perf_counter() - t_start:.0f} s so far")

    shutil.rmtree(work, ignore_errors=True)
    st_after = mitigation_status.get_status()
    info["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    info["mitigations_after"] = st_after
    if st_after["spec_raw"] != st["spec_raw"] or st_after["kva_raw"] != st["kva_raw"]:
        info["warning"] = "mitigation flags changed during the run"
        print("WARNING: mitigation flags changed during the run, check the JSON file.")
    json_path = out / f"system_{session}.json"
    json_path.write_text(json.dumps(info, indent=2), encoding="utf-8")

    print(f"\nDone in {time.perf_counter() - t_start:.0f} s.")
    print(f"  measurements: {csv_path}")
    print(f"  system info:  {json_path}")
    if st_after["overall"] == "off":
        print("\n!!! Mitigations are still OFF. Now run 4_mitigations_RESTORE_admin.bat "
              "as administrator and reboot. !!!")


if __name__ == "__main__":
    main()
