# Cost of Spectre/Meltdown mitigations on a consumer Windows desktop

Code and raw data for the research paper *The Real-World Cost of Spectre and
Meltdown Mitigations on a Consumer Windows Desktop: A Replication Study on an
Intel Core i5-9600K* (Architecture of Computers, Vistula University, 2026).

The experiment runs the same workloads with the Windows default mitigations
(**ON**) and with Spectre variant 2 / Meltdown mitigations disabled through
Microsoft's documented registry switch (**OFF**), and compares the medians.

## Test system

| Part | Value |
|---|---|
| CPU | Intel Core i5-9600K (6 cores, no Hyper-Threading) |
| RAM | 16 GB |
| OS | Windows 10 Home, version 1909, build 18363 (no updates during the experiment) |
| Disk used for test files | Kingston SA400 SATA SSD (system drive, `%TEMP%`) |
| Power plan | High performance |
| Python | 3.13 (standard library only, no extra packages) |

Exact values for every session (microcode revision, power plan, uptime,
Defender state, raw kernel mitigation flags) are stored in
`results/system_*.json`.

## What the kernel reports

`mitigation_status.py` reads the mitigation state directly from the Windows
kernel via `NtQuerySystemInformation` (information classes 196 and 201, the same
ones used by Microsoft's `SpeculationControl` PowerShell module). Raw flag words
are saved, so the decoding can be checked independently.

On this machine the kernel reports (raw flags `kva=0x3020`, `spec=0x3d8193f9`
with default settings) that the CPU is **not vulnerable to Meltdown**
(KVA shadow not required, not active) and supports **enhanced IBRS**. So the
OFF condition in practice disables only the Spectre variant 2 (branch target
injection) mitigation; the Meltdown fix is not part of the comparison.

## Workloads

| Workload | Group | What it does |
|---|---|---|
| `file_create_delete` | kernel-heavy | create, read back and delete 3 000 small files |
| `file_stat` | kernel-heavy | `os.stat` on one file 150 000 times |
| `zip_extract` | kernel-heavy | extract a ZIP of 6 000 small text files (archive generated with a fixed seed) |
| `process_spawn` | kernel-heavy | start `cmd /c exit 0` 200 times |
| `thread_pingpong` | kernel-heavy | two threads hand control back and forth 60 000 times |
| `cpu_python` | control | 6 million integer operations in the interpreter |
| `cpu_sha256` | control | SHA-256 over a 64 MB in-memory buffer, 8 passes |

Kernel-heavy workloads cross the user/kernel boundary often, which is where the
mitigations add cost. The control workloads stay in user space; if they also
change a lot, the measurement is not trustworthy.

Each session: 2 warm-up rounds (saved, excluded from analysis) + 15 measured
rounds. In every round all workloads run once, in a shuffled order that is the
same in every session (fixed seed).

## How to reproduce

Run everything from this folder. Close other programs, keep the PC idle.

1. `python bench.py --condition on` (Windows default state)
2. As administrator: `powershell -NoProfile -ExecutionPolicy Bypass -File set_mitigations.ps1 off`, then reboot
3. `python bench.py --condition off`
4. As administrator: `powershell -NoProfile -ExecutionPolicy Bypass -File set_mitigations.ps1 restore`, then reboot
5. `python bench.py --condition on` again (A/A noise check)
6. `python analyze.py`

`bench.py` stops without measuring if `--condition` does not match the state
the kernel reports (for example when the reboot was forgotten).

The numbered `.bat` files in this folder do the same steps by double click.

**Always restore the mitigations after the experiment (step 4).**

## Output

| File | Content |
|---|---|
| `results/raw_<condition>_<time>.csv` | every single measurement, including warm-ups |
| `results/system_<condition>_<time>.json` | system description and mitigation flags before and after the session |
| `results/summary.csv`, `summary.md` | median per workload and condition, spread, slowdown, 95% CI |
| `results/slowdown.svg` | chart of the slowdown with confidence intervals |
| `results/noise_check.csv` | ON vs ON (first vs last ON session) |

Slowdown = (median ON − median OFF) / median OFF × 100 %. The 95% confidence
interval is a percentile bootstrap of the ratio of medians (10 000 resamples,
fixed seed). An interval that contains 0 means no difference can be claimed.

Both ON sessions are pooled (n = 30 ON vs n = 15 OFF). The bootstrap treats
rounds as independent and does not capture drift between sessions, so a
difference is reported as an effect ("Beyond noise" = yes) only if its CI
excludes 0 **and** it is larger than the ON-vs-ON difference.

Only sessions that finished cleanly are analysed: an interrupted run, a run
whose mitigation flags changed, or a run started with `--force` is skipped
and listed by `analyze.py`.

## Limitations

One machine, one Windows build, one CPU model. Windows Defender real-time
protection stayed in its default state in both conditions (as a typical user
has it), which adds noise to the file workloads.
