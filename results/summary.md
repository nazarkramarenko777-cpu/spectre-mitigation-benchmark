| Workload | Group | Median ON (s) | Median OFF (s) | Slowdown (%) | 95% CI (%) | Beyond noise | n ON / OFF |
|---|---|---|---|---|---|---|---|
| file_create_delete | kernel-heavy | 2.578 | 2.562 | +0.6 | -6.7 to +5.1 | no | 30 / 15 |
| file_stat | kernel-heavy | 3.345 | 3.081 | +8.6 | -0.3 to +17.6 | no | 30 / 15 |
| zip_extract | kernel-heavy | 1.986 | 2.184 | -9.1 | -11.3 to -0.7 | yes | 30 / 15 |
| process_spawn | kernel-heavy | 0.936 | 0.936 | +0.0 | -1.1 to +1.3 | no | 30 / 15 |
| thread_pingpong | kernel-heavy | 0.768 | 0.764 | +0.5 | -12.8 to +5.4 | no | 30 / 15 |
| cpu_python | control | 0.553 | 0.561 | -1.4 | -2.8 to -0.5 | yes | 30 / 15 |
| cpu_sha256 | control | 0.942 | 0.953 | -1.2 | -1.4 to -0.8 | yes | 30 / 15 |

Sessions: off: off_20261124_165702; on: on_20261124_164537, on_20261124_171424

A/A noise check (ON vs ON): on_20261124_171424 vs on_20261124_164537
| Workload | Median first ON (s) | Median last ON (s) | Difference (%) |
|---|---|---|---|
| file_create_delete | 2.649 | 2.494 | -5.9 |
| file_stat | 3.626 | 3.055 | -15.7 |
| zip_extract | 1.988 | 1.923 | -3.3 |
| process_spawn | 0.950 | 0.922 | -2.9 |
| thread_pingpong | 0.782 | 0.761 | -2.7 |
| cpu_python | 0.555 | 0.550 | -0.9 |
| cpu_sha256 | 0.942 | 0.939 | -0.3 |
