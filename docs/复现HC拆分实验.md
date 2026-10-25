# 复现：大容量业务（HC）星上拆分卸载实验

本文说明如何复现 [HC 拆分实验报告](HC拆分实验报告.md) 中的全部结果。实验比较 4 种算法：S0 BFS、最优单节点、S6 注水法、S7 分段线性规划。

## 1. 环境

| 项目 | 要求 |
|---|---|
| Python | 3.9 及以上（已在 3.9.6 上验证） |
| 依赖 | 见 [requirements.txt](../requirements.txt)，版本已固定 |
| 系统 | macOS 或 Linux。无需 GPU |
| 耗时 | 单独求解约 30 s；完整仿真约 17 分钟（S7 约占 8 分钟） |

```bash
git clone https://github.com/wall96/computing-routing.git
cd computing-routing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 实验参数

仓库默认的 [config.yaml](../config.yaml) 就是本实验的配置，不需要修改。关键参数如下：

| 参数 | 配置位置 | 取值 |
|---|---|---|
| 星座 | `constellation.shells` | L1：20 个轨道面 × 每面 25 颗，550 km，53°，Walker Delta |
| 星间链路带宽 | `link_params.isl_leo_leo.bw_mbps` | 100000（100 Gbps） |
| 星地链路带宽 / 最小仰角 | `link_params.sgl_leo` | 30000（30 Gbps）/ 20° |
| 星上算力 | `compute_capacity_tflops.LEO` | [10, 20]，每颗随机，随机种子 `compute_capacity_seed: 7` |
| 并发上限 | `constellation.enforce_max_concurrency` | false，超过上限也正常服务 |
| 终端 | `traffic.endpoint_mode` / `traffic.terminal` | terminal；纬度 ±55° |
| HC 速率 | `traffic.qos.HC.rate_mbps` | [1000, 10000]（1~10 Gbps） |
| HC 每 bit 计算量 | `traffic.qos.HC.cycles_per_bit_base` | 1000 |
| HC 包大小 | `traffic.qos.HC.packet_bits` | [500000000, 500000000]（固定 500 Mbit） |
| 拆分开销 | `algorithm.split.overhead_s_per_Tbit` | 0.01 |
| 随机种子 | `simulation.random_seed` | 42 |

实验脚本会自动做三件事：
- 只保留 HC 业务；
- 使用场景 1（500 颗 LEO，无 GEO、无地面算力）；
- 依次切换 4 种算法，其中"最优单节点"是 S6 设 `max_splits=1`。

## 3. 运行

### 3.1 快速验证（约 30 s，只做单独求解）

```bash
python3 experiments/run_hc_split.py --scenario 1 --t-end 3600 --out results/hc_split_check --skip-sim
```

### 3.2 完整实验（约 17 分钟）

```bash
python3 experiments/run_hc_split.py --scenario 1 --t-end 3600 --out results/hc_split_D500_w1000
```

脚本做两组实验：
1. **单独求解**：每个时间片新到达的任务，在空网络上分别用 4 种算法求解，记录时延分量、子包数和求解耗时。
2. **完整仿真**：4 种算法各跑一次 3600 s 的完整仿真，任务之间争用资源，每 30 s 全部重新路由。

### 3.3 可选参数

| 参数 | 作用 |
|---|---|
| `--scenario` | 1 / 2 / 3，默认 1 |
| `--t-end` | 仿真时长（秒），默认 3600 |
| `--omega` | 额外跑一组单独求解，HC 每 bit 计算量取该值，用于敏感性分析 |
| `--skip-sim` | 只做单独求解，不跑完整仿真 |

### 3.4 单独运行某个算法

```bash
python3 main.py --scenario 1 --solver S6 --strategy latency --t-end 3600 --out results/run_s6
```

注意：`main.py` 会跑配置中的全部 4 类业务，而不是只跑 HC。只跑 HC 请用上面的实验脚本。

## 4. 预期结果

所有时延都由固定随机种子决定，多次运行结果应完全一致；只有求解耗时会因机器不同而变化。

### 4.1 单独求解（`isolated_summary.csv`）

| 算法 | 可行任务 | 平均时延 (ms) | p95 (ms) | 最大 (ms) | 比最优单节点快 | 子包数分布 |
|---|---|---|---|---|---|---|
| S0_BFS | 391 | 132.22 | 171.03 | 191.92 | −16.41% | 全部 1 份 |
| 单节点最优 | 391 | 113.99 | 142.33 | 151.50 | 0 | 全部 1 份 |
| S6注水 | 391 | 99.30 | 128.03 | 138.14 | 13.27% | 4 份：11；5 份：380 |
| S7分段LP | 391 | 95.26 | 123.75 | 135.38 | 16.90% | 3～9 份，以 6～7 份为主 |

### 4.2 完整仿真（`sim_summary.csv`）

| 算法 | 接受 | 首次分配时延平均 (ms) | p95 (ms) | 所有分配中最大 (ms) | 最忙单星利用率 |
|---|---|---|---|---|---|
| S0_BFS | 391 | 138.02 | 180.10 | 199.87 | 99.98% |
| 单节点最优 | 391 | 117.00 | 145.28 | 156.93 | 77.37% |
| S6注水 | 391 | 100.06 | 128.35 | 139.09 | 73.10% |
| S7分段LP | 391 | 95.87 | 123.95 | 136.62 | 63.41% |

## 5. 输出文件

| 文件 | 内容 |
|---|---|
| `isolated_summary.csv` | 单独求解的各算法汇总：时延统计、各时延分量平均值、拆分比例、子包数分布、求解耗时 |
| `isolated_tasks.csv` | 单独求解的逐任务明细。列名格式为"算法\|字段"，例如 `S7分段LP\|compute_ms` |
| `sim_summary.csv` | 完整仿真的各算法汇总 |
| `sim/<算法>/generated_tasks.csv` | 全部生成任务，含终端坐标、包大小、最终状态 |
| `sim/<算法>/routing_allocations.csv` | 每次分配的计算节点、路径、时延；拆分时 `splits_json` 列出各子包 |
| `sim/<算法>/resource_allocations.csv` | 每次带宽、算力、功率的占用与释放流水 |
| `sim/<算法>/slot_stats.csv` | 每个时间片的统计 |
| `sim/<算法>/kpi.json` | 整体指标 |
| `run_meta.json` | 运行参数 |

时延分量的列名含义：

| 列名 | 含义 |
|---|---|
| `uplink_prop_ms` / `uplink_tx_ms` | 终端 → 接入卫星的传播 / 传输时延 |
| `split_overhead_ms` | 拆分开销 |
| `isl_prop_ms` / `isl_tx_ms` | 接入卫星 → 算力节点的传播 / 传输时延 |
| `compute_ms` | 计算时延（含 1 ms 固定开销） |
| `down_prop_ms` / `down_tx_ms` | 算力节点 → 目的终端的传播 / 传输时延 |
| `total_ms` | 端到端时延，拆分时取最晚到达的子包 |

## 6. 运行测试

```bash
python3 -m unittest discover -s tests
```

`test_csv_guide` 和 `test_randomized_batch` 中有 5 个测试会报错。原因是它们依赖 `results/` 下的历史结果文件，而这些文件不在仓库中，和本实验无关。其余测试应全部通过，其中 `tests/test_split_offloading.py` 覆盖星上拆分的各项规则。
