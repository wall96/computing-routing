# 星地协同算力路由仿真器 (v5)

按《项目总体设计文档.md》v5 实现:**单节点两段分解(前段载 D₀、后段载 D₀ρ)+ 在网计算压缩 + 多算法可插拔 + 三级场景 + 时变信道 + 能量硬约束**,主 KPI 为 **QoS 达标率**。

默认（`traffic.endpoint_mode: terminal`）业务由地面随机终端产生：源和目的终端坐标在纬度带内随机撒点（LL/HR 的目的限定在源周围 `traffic.terminal.dst_max_km` 内）。每个时间片先按仰角门限计算终端被哪些卫星覆盖，终端只与这些卫星建立星地链路；数据上星计算后经星间链路下行到目的终端。终端只能作为路径首尾，不能计算或中转。覆盖为空时拒绝原因记为 `src_no_coverage` / `dst_no_coverage`。旧的卫星端点模式可用 `endpoint_mode: satellite` 恢复。每个新动态拓扑时间片的片首，把所有尚未结束的活跃任务与本片新任务合并，按业务优先级重新计算算力节点和前/后段路径；片内拓扑、路径和资源分配保持不变。可通过 `algorithm.reroute_all_each_slot` 切换回仅断链时重算的兼容模式。

四类业务可分别配置 `processing_logic`、`cycles_per_bit_base`、`cycles_per_bit_per_dst`、`fixed_processing_ms`、`fixed_processing_ms_per_dst` 和 `Lref_bits`。节点功率上限按 `Pmax = eta × compute_capacity` 联合标定。

## 安装
```bash
pip install -r requirements.txt      # numpy, PyYAML, pulp(仅 S5 需要)
```

## 场景(复杂度递进)
| 场景 | 组成 | 算力节点 | 链路 |
|---|---|---|---|
| 1 | 500颗单层LEO纯卫星网络，最大并发200 | LEO | ISL_LEO_LEO |
| 2 | 500颗LEO + 18 EDGE + 10 DC，最大并发250 | LEO, EDGE, DC | ISL_LEO_LEO + SGL_LEO + TER |
| 3 | 2×500颗LEO + 6颗GEO，最大并发500 | LEO, GEO | ISL_LEO_LEO + ISL_LEO_GEO |

## 策略(每次一个)
- `latency` 最小时延 · `balance` 负载均衡 · `energy` 能耗优化

## 算法(可插拔,`--solver`)
| | 算法 | 定位 |
|---|---|---|
| S0 | 最近节点+最短跳 | 朴素基线 |
| S1 | 加权 Dijkstra 两段 | 主力启发式 |
| S2 | K 最短路(Yen)+联合选择 | 强启发式 |
| S3 | LARAC 约束最短路 | 时延约束下最小代价 |
| S4 | 蚁群 ACO | 全局搜索(较慢) |
| S5 | MILP 精确 | 小规模最优参照(需 pulp) |
| S6 | 拆分卸载:等完成时间注水法 | 包拆成多个子包并行计算,min 最晚子包到达;仅 latency |
| S7 | 拆分卸载:按 τ 分段线性规划 | 同 S6 目标,显式建模子包共用链路(需 scipy);仅 latency |

包模型:`traffic.qos.<业务>.packet_bits` 给出业务包大小 D。时延 = 传播 + 传输(D/链路速率,直通转发取瓶颈) + 计算(固定开销 + x·D·ω/C),拆分后子包按比例 x 缩小传输与计算部分,整包时延取最晚子包。`algorithm.split.max_splits=1` 即不拆分的单节点基线。对比实验:`python experiments/run_split_compare.py`。

## 文档
- [算法说明](docs/算法说明.md)：公共时延模型、S0～S5 单节点算法、S6/S7 星上拆分算法
- [复现 HC 拆分实验](docs/复现HC拆分实验.md)：环境、参数、运行命令、预期结果
- [HC 拆分实验报告](docs/HC拆分实验报告.md)

## 运行
```bash
# 单次:场景3 + 能耗策略 + LARAC(星座始终全量,场景3为 1000 LEO + 6 GEO)
python main.py --scenario 3 --strategy energy --solver S3 --out results/run1

# 短时联调:缩短仿真时长即可
python main.py --scenario 1 --strategy latency --solver S1 --t-end 300

# 横向对比矩阵(场景×策略×算法 + 消融),全规模
python experiments/run_matrix.py --solvers S0,S1,S2,S3 --t-end 600
python experiments/run_matrix.py --quick               # 短时冒烟

# 单业务整时间片对照（依次只生成 LL/HR/HC/MC，任务时长等于 slot）
python experiments/run_single_biz.py --out results/single_biz

# 推荐高成功率配置：场景3完整星座，LL只投递到最近站点
python experiments/run_single_biz.py --scenario 3 --ll-dst-knn 1 --out results/single_biz_recommended

# 对结果中的拒绝任务做满资源反事实诊断
python experiments/analyze_rejections.py results/run1 --biz LL --scenario 1
```

星座按 config.yaml 全量构建(L1/L2 各 20×25 Walker),已取消缩比。

## 输出(`results/<out>/`)
- `kpi.json` —— 全部 KPI(主:`qos_rate_overall` / `qos_rate_by_biz`)
- `tasks.csv` —— 每任务最终结果:计算节点、时延、丢包、能耗、状态
- `generated_tasks.csv` —— 所有自动生成任务的完整输入参数和最终状态
- `routing_allocations.csv` —— 每次初始/迁移路由分配:前段路径、后段路径、算力节点
- `resource_allocations.csv` —— 逐任务、逐资源的带宽/算力/功率占用与释放事件
- `slot_stats.csv` —— 每片:接受/拒绝/并发/利用率
- `rejection_diagnostics.csv/json` —— 运行拒绝诊断脚本后生成的逐任务原因和汇总
- 对比实验另出 `compare.csv` / `compare.md`,每个组合的完整明细位于 `runs/<组合名>/`

三个明细 CSV 使用 UTF-8 BOM 编码。路径和多目的地等嵌套字段使用 JSON 字符串，
`resource_allocations.csv` 中 `allocation_delta>0` 表示占用资源，`<0` 表示释放资源。

## 三策略单业务压力实验及Word报告

正式对比采用 `S1`、10个30秒时间片、种子42。每组只启用一种业务，常规片为并发上限的60%，
第5片达到峰值。场景3峰值注入525个请求，实际并发硬上限仍是500。

```powershell
# 分别将strategy设为latency/balance/energy，scenario设为1/2/3。
python experiments/run_single_biz.py --scenario 2 --strategy balance --stress-concurrency --stress-slots 10 --steady-ratio 0.60 --out results/all_strategy_single_biz_20261016/balance/sc2
# 场景3增加 --peak-offered-ratio 1.05。
python -m pip install -r requirements-report.txt
python experiments/build_all_strategy_report.py
```

统一结果位于 `results/all_strategy_single_biz_20261016/`：
`all_metrics.csv`（36组）、`all_slot_metrics.csv`（360片）、`rejection_audit.csv`、
`algorithm_before_after.csv`、`audit_manifest.json`、`figures/`及`全场景全策略单业务测试.docx`。
每组另存 `effective_config.json`，报告构建时检查三策略任务输入和物理配置一致。

S1的balance使用预测平方利用率增量，energy使用计算加传输能耗；两者在主目标候选全部不可行时
补充最短传播路径候选，并继续执行原有硬约束校验。定向验证：`python -m unittest discover -s tests -v`。

## 模块 ↔ 设计文档
| 设计章节 | 文件 |
|---|---|
| §2 场景 | `network.select_nodes/build_snapshot` |
| §3 业务 | `tasks.py`, `config.traffic` |
| §4 节点算力 | `topology.py` |
| §5 链路+时变信道 | `network.py` |
| §6 计算+压缩+t_compute | `energy.py` |
| §7 能耗+能量硬约束 | `energy.py`, `resources.py` |
| §9 算法 | `solvers/` |
| §10 调度/抢占/迁移 | `simulator.py`, `resources.py` |
| §11 KPI | `metrics.py` |
