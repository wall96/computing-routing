"""Explicit, code-audited Chinese field definitions for the CSV user guide."""
from __future__ import annotations

# Multiple spellings on a line share a definition unless file-specific overrides apply.
# Units are storage units, not an instruction to multiply displayed percentages twice.
SPEC = r"""
scenario|编号|运行场景编号。现行场景1=500 LEO；2=500 LEO+18 EDGE+10 DC；3=双层各500 LEO+6 GEO。历史目录可能使用旧场景，须核对配置。
strategy|文本|优化目标：latency最小时延、balance负载均衡、energy能耗优化；不是求解器编号。
solver|文本|求解器S0～S5。正式三策略对照主要使用S1；目标策略与求解器应分别筛选。
biz,biz_only|文本|业务LL/HR/HC/MC；汇总中的ALL表示四类总体，不是第五类业务。
task_id|文本|一次运行内的任务唯一编号，例如T-LL-000001。不同运行会重用编号，跨目录连接必须加run_id。
src|文本|业务源节点ID；terminal模式为地面随机终端UT-<task_id>-S，satellite模式为卫星。历史目录多为卫星源。
dst_json|JSON数组|目的节点ID列表；MC可有多个目的。用json.loads解析，不能按逗号手工拆分。
src_coord_json|JSON数组/空|terminal模式下源终端[纬度,经度]（度）；其它模式为空。
dst_coords_json|JSON嵌套数组/空|terminal模式下各目的终端[纬度,经度]，顺序与dst_json一致；其它模式为空。
dst|分号文本|目的节点列表用分号连接的显示形式，见tasks.csv；与dst_json的JSON编码不同。
priority|整数|业务优先级，数字越小越优先；现行LL/HR/HC/MC依次为1/2/3/4。
mode|文本|任务生成模式burst或persist等标签；不能单凭该字段判断仿真是否启用片内精确事件引擎。
rate_mbps|Mbps|原始输入数据速率，1 Mbps=10^6 bit/s；tasks.csv显示值已保留三位小数，精确值取generated_tasks.csv。
delay_budget_ms|ms|端到端时延硬约束。多目的任务要求每个目的满足，等价于最坏目的时延不超预算。
loss_budget|0～1|端到端累计丢包率预算。例如0.01为1%，不是0.01%。
processing_logic|文本|处理逻辑标签，如low_latency_filter；用于描述业务处理类型，实际计算需求由数值字段决定。
cycles_per_bit,cycles_per_bit_base|op/bit|每个原始输入bit的基础等效操作量。字段保留cycles旧名，不应解读为CPU时钟周期或每秒频率。
cycles_per_bit_per_dst|op/(bit·目的)|每增加一个目的附加的每bit等效操作量。MC当前15，其余通常为0。
cycles_per_bit_effective|op/bit|有效操作量=基础op/bit+目的数量×每目的附加op/bit；MC三目的为40+3×15=85。
fixed_processing_ms|ms|每次参考数据处理的基础固定开销，不随任务总持续秒数相乘。
fixed_processing_ms_per_dst|ms/目的|每个目的附加的固定处理开销。
fixed_processing_ms_total|ms|总固定处理项=基础固定开销+目的数×每目的固定开销。
Lref_bits|bit|计算处理时延的参考数据块大小；不是任务总数据量，不能用它计算任务总能耗。
arrival_t|s|任务在仿真时间轴上的实际到达时刻，不是程序墙钟时间。
duration_s|s|本次生成任务的实际持续时间。截断版已按3600 s边界缩短，尾部任务可小于原时长下限。
t_end,requested_t_end|s|实际任务预定结束时刻=arrival_t+duration_s。requested_t_end在生命周期表中并非截断前的采样结束时间。
compression_rho,rho|0～1|处理后输出/原始输入比例；0.1表示输出剩10%、数据缩减90%。
cycles_total|op|整个实际任务所需等效操作量=data_volume_bits×cycles_per_bit_effective；不是瞬时算力。
data_volume_bits|bit|整个实际任务的原始输入量=rate_mbps×10^6×duration_s。包含后来被拒绝的需求。
compute_rate_cycles_s,compute_rate_used|op/s|任务持续占用的处理速率=rate_mbps×10^6×有效op/bit。换算TFLOPS等效值需除10^12。
generated_slot|整数|生成任务所属拓扑时间片序号，从0开始；精确事件模式同片内到达时刻仍可不同。
scheduled_t|s|记录的调度时刻。传统片批处理通常为片首；精确事件模式为实际到达时刻。
final_status|文本|任务最终状态：传统版ALLOCATED/DROPPED/REJECTED；事件版可为COMPLETED/ACTIVE_AT_END/DROPPED/REJECTED。
reject_reason|文本/空|初始拒绝原因scenario_concurrency_cap或no_feasible；接纳后掉线通常不在此字段记初始拒绝原因。
compute_node|文本/空|本次分配选定的计算节点ID；tasks.csv只展示最后一次成功分配的计算节点。
compute_node_type,node_type|文本/空|计算节点类型LEO/GEO/EDGE/DC等；L1和L2节点均属于LEO。历史可能出现ACCESS。
delay_ms|ms|tasks.csv中最后一次成功分配的最坏目的端到端时延；不是跨时间片生命周期最大值。
loss|0～1|tasks.csv中最后一次分配的最坏目的累计丢包率；拒绝任务为空。
t_compute_ms,compute_latency_ms|ms|该次分配的处理时延项，含固定处理项和参考块处理项，以及当前节点占用率修正。
e_compute_J,e_tx_J,e_total_J|J|tasks.csv中的任务计算/传输/合计能耗，按各字段前缀区分；事件版为该任务累计实际服务能量，传统版为最后一次分配对应估算。
status|文本|终态或显示状态；传统tasks.csv成功为OK、失败REJECT:原因，事件版另有COMPLETED/ACTIVE_AT_END/DROPPED。
allocation_id|文本|一次路由分配/服务段ID，例如A-00000001；同一任务跨片会有多个ID，跨运行仍须加run_id。
event_type|文本|路由分配类型INITIAL初始分配、REROUTE跨片重算、MIGRATION计算节点改变的重算等。
t_alloc|s|该次路由分配生效时刻；事件版服务段区间起点。
path_pre_json|JSON数组|源→计算节点的前处理路径，节点按经过顺序排列。源端计算时通常为仅含源节点的列表，跳数为0。
path_post_json|JSON嵌套数组|计算节点→各目的的后处理路径列表，外层顺序与dst_json一致；不是一棵去重后的多播树。
bw_per_link_json|JSON对象数组|该次分配每条无向链路的合计带宽。每项含u、v、bw_mbps，已叠加前段及各目的后段的重复链路需求。
power_used_W|W|此任务在计算节点占用的增量计算功率η×compute_rate_used；不含传输功率、平台静态功率。
max_delay_ms|ms|该次分配对所有目的取最大值的端到端时延；不直接表示任务持续多片时的最大值。
max_loss|0～1|该次分配对所有目的取最大的累计路径丢包率；汇总表中为所统计任务集合的最大值。
delivered_data_bits|bit|该服务段压缩后输出量=输入速率×ρ×实服秒；MC不额外乘目的数，实际各复制路径耗能已计入传输能耗。
energy_compute_J|J|计算增量能耗。事件版为实际服务区间积分Ση×算力占用×服务秒；传统路由行为其任务估算。
energy_tx_J|J|传输增量能耗，按前段原始bit和各后段压缩bit分别乘链路每bit能耗累加；MC各目的传输均计入。
energy_total_J,total_energy_J|J|计算能耗+传输能耗；是否为窗口内积分要看目录/引擎版本，不能把传统跨片路由估算直接求和。
service_t_end|s|该路由服务段结束时刻，可能为任务完成、拓扑换片、观察窗口停止时刻。
service_duration_s|s|实际服务段持续秒数=service_t_end−t_alloc，路由段能量按此时长计算。
event_seq|整数|同一次运行的资源变更流水号，严格递增；时间相同时按此序号处理。
t,t_sec|s|仿真时间，含义依文件而定：事件表为事件时刻，时间片表为片首，诊断表为被检查拓扑时刻。
event|文本|事件动作标签；资源表为占用/释放，事件时间线为到达/完成前后和拓扑切换观察点，两个枚举域不同。
resource_type|文本|BANDWIDTH带宽、COMPUTE处理速率、POWER计算功率预算；不是三种都以相同单位相加。
resource_id|文本|资源主键：链路u<->v，算力/功率为节点ID；完整主键需同时包含resource_type和运行/拓扑快照。
node_id|文本/空|算力或功率资源所属节点ID；带宽行为空。
link_u,link_v|文本/空|带宽资源链路两端节点ID；算力和功率行为空。
link_type|文本/空|链路类型，如ISL_LEO_LEO、星地链路等，具体以本次导出值为准。
path_roles|分号文本|链路在该分配中的角色PRE和/或POST:0等，后者下标从0开始并对应目的列表；节点资源为COMPUTE。
allocation_delta|混合单位|本次资源变动：正值占用、负值释放。通常等于remain_before−remain_after；释放上限钳制和浮点误差需容差。
unit|文本|本行资源数值的单位：Mbps、cycle/s、W。cycle/s在当前模型语义为等效op/s，不是CPU频率。
total_capacity|见unit|该资源在本拓扑快照中的总容量；带宽由链路模型计算，可能随快照变化。
remain_before,remain_after|见unit|此次变更前/后的剩余资源，不是已占用量；利用率=1−remain_after/total_capacity。
accepted|个|已成功分配的任务数；时间片表仅计本片新到达且初次接纳数，汇总表按该运行统计，传统summary会排除最终掉线任务。
rejected|个|初始接纳失败的请求数；时间片表为本片新增拒绝数，不含已接纳后的重算失败。
active|个|实际在服数；传统slot_stats为本片调度后的快照，事件slot_stats为片末完成处理后的在服数；时间线为该观察点状态。
finished|个|本片正常完成并释放的任务数，不等于新接纳数，也不包含重路由失败掉线。
rerouted|次|本片存量业务重新求解并成功分配的次数；不能与accepted相加后当作新增任务数。
reroute_failed|个|本片存量业务重算失败的数量，代表已接纳后的掉线，不是初始拒绝。
link_util_mean,node_util_mean|0～1|全体有容量链路/计算节点利用率的算术均值，包含空闲资源；事件slot_stats再按片内时间加权。
link_util_max,node_util_max|0～1|单条链路/单个算力节点的最大利用率；事件slot_stats为本片所有事件状态中的最大值，不是均值的最大值。
generated|个|全部生成请求数，含拒绝；时间片表为本片生成数量，汇总为所选业务/运行总量。
active_mean|个|事件时间片内∫在服数dt/片长；可能为小数，不能用片末active替代。
active_max|个|事件时间片内实际在服峰值，包含片内到达和结束之间的状态。
link_util_cv_mean,node_util_cv_mean|无量纲|本片内瞬时资源利用率CV的时间平均；不是由全片平均利用率再计算的CV。
compute_power_mean_W|W|本片内全网计算增量功率的时间平均，不含传输。
compute_power_max_W|W|本片内全网同时计算增量功率之和的峰值。
solve_total_ms|ms|本片全部求解调用墙钟耗时之和，包含新接纳和跨片重算；不是业务网络时延。
allocation_segments|段|任务/业务实际获得的路由服务段数量；一任务可有多段，不等于任务数。
reroutes|次|任务或汇总成功重算次数。事件版为每任务max(0,分配段数−1)，包含迁移重算。
migrations|次|重算时计算节点改变的次数，是重算次数的子集；未单列计入迁移传输时延或状态复制能耗。
service_seconds,observed_service_seconds|任务·s|任务实际在服秒或所选集合的任务秒之和；多个任务并行时可大于3600。
last_service_end|s/空|任务最后一个成功服务段的结束时刻；完全拒绝任务为空。
lifetime_max_delay_ms|ms|该任务在所有成功服务段中的最坏目的时延最大值；拒绝任务导出0为占位，不应纳入时延平均。
lifetime_max_loss|0～1|该任务跨全部已服务段的最坏目的丢包率最大值；拒绝任务0为占位。
input_bits_served|bit|该任务实际已服务的原始输入量=输入速率×service_seconds；掉线任务只计掉线前。
slot|整数|时间片索引，从0开始；时刻通常为t_start+slot×slot_sec，不能直接当作秒。
compute_power_W|W|时间线观察点全网计算增量功率总和；不含传输或平台基础功耗。
link_util_cv,node_util_cv|无量纲|该状态/时间片截面资源利用率标准差/均值；含空闲资源，均值近0时按实现约定记0。
sampled_duration_s|s|业务原持续时间分布抽样结果，尚未按仿真终点截断。
sampled_t_end|s|截断前抽样结束时刻=arrival_t+sampled_duration_s。
duration_clipped|0/1|是否因hard_end_sec而缩短持续时间；1表示实际duration_s小于sampled_duration_s。
hard_end_sec|s/空|生成器应用的业务强制截止时刻，正式有限窗口试验为3600 s。
t_start|s|到达计划中对应时间片的起始时刻。
is_pulse_slot|布尔|该片是否包含额外的突发到达任务；有脉冲不代表整片始终满并发。
arrived|个|本次生成/到达任务总数，包含初始拒绝。
preemptions|次|资源账本记录的抢占次数，历史实现可能仅降低带宽；正式精确事件实验禁用抢占，通常为0。
request_success_rate,request_accept_rate,initial_accept_rate|0～1|初始成功分配数/全部生成数。单片正式实验接纳与QoS成功一致；混合实验初始成功不保证跨片不掉线。
accepted_qos_rate|0～1|已接纳任务中时延和丢包约束均通过的比例；不是相对全部生成请求的成功率。
rejected_cap|个|因达到场景最大并发硬上限而初始拒绝的数量；不能直接解释为带宽或算力不足。
rejected_no_feasible|个|初始请求未找到满足资源、时延、丢包约束的候选数；启发式无候选不等于证明全局无解。
dropped|个|初始已接纳，后续跨片重算失去可行分配的任务数；与初始拒绝互斥。
min_active,avg_active,max_active|个|单业务传统等长时间片在服快照的最小/算术均值/最大；avg不是随机到达事件试验的时间积分。
delay_mean_ms,avg_delay_ms|ms|成功分配任务的端到端时延算术均值。单业务30 s每任务仅一段，不需要跨片合并。
delay_p95_ms,p95_delay_ms|ms|上述任务时延样本的95分位数，报告脚本通常使用numpy线性分位数；不是95%成功率。
delay_max_ms|ms|所统计成功分配任务的最大端到端时延。
compute_delay_mean_ms,avg_compute_delay_ms,avg_compute_ms|ms|所统计成功分配任务的处理时延项算术均值。
delay_margin_min_ms|ms|时延预算−所统计任务最大时延；越接近0越接近硬约束。
energy_mean_J,avg_energy_per_task_J,avg_energy_per_accepted_task_J,avg_energy_J,avg_E_per_task_J|J/任务|所统计总能耗/成功服务任务数。传统跨片summary采用最终存续任务的最后分配估算，事件版采用窗口累计/初始接纳数。
energy_J_per_input_Gbit|J/Gbit|窗口业务总增量能耗/实际服务原始输入Gbit；1 Gbit=10^9 bit，非GB；MC输入分母只计一次。
peak_link_util_pct,peak_node_util_pct|%|整个运行中最忙单链路/单计算节点利用率峰值，已乘100；不是全网总容量利用率。
mean_link_cv,mean_node_cv|无量纲|瞬时/片截面利用率CV再按时间平均。相同拓扑内较小通常更均匀，不能脱离均值或网络规模单独比较。
mean_link_jain,mean_node_jain|0～1|瞬时Jain公平指数时间平均，越接近1越均匀；全空闲时实现约定贡献0。
peak_compute_power_W|W|全网同时计算增量功率总和的运行峰值；不是各节点各时刻峰值相加。
compute_at_source_pct|%|单业务成功任务在源节点计算的比例；该实验每任务一段，因此也等于分配段比例。
ground_compute_pct|%|单业务成功任务在EDGE/DC处理的比例；不是地面站容量利用率。
avg_pre_hops,avg_post_hops|跳|前段平均跳数/每任务各目的后段跳数的均值再对任务平均；不包含节点自身处理为网络跳。
solve_p99_ms,solve_p50_ms|ms|单次求解调用耗时99/50分位，包括失败调用。不是服务端到端时延；受机器负载影响。
fallback_attempts|次|S1策略候选失败后进入补充可行性搜索的尝试次数，不是任务总数。
fallback_accepted|次|补充搜索恢复成功次数；与fallback_attempts一起衡量候选搜索覆盖性。
input_sha256|SHA-256文本|输入任务去除final_status/reject_reason后的指纹；用于判断是否同一业务输入，不是输出文件哈希。
common_accepted_tasks|个|同场景、同业务三策略共同成功接纳的任务数，仅在已验证输入一致的配对实验使用。
common_energy_mean_J|J/任务|该策略在三策略共同成功任务集合上的任务平均能量，便于避免不同接纳集合偏差。
common_delay_mean_ms|ms|该策略在三策略共同成功任务集合上的时延平均。
link_util_std,node_util_std|0～1|全体链路/计算节点利用率的总体标准差，与利用率同量纲，包含空闲项。
link_jain,node_jain|0～1|利用率公平指数(Σu)^2/(nΣu²)，全空闲时约定0；不是1减CV。
total_compute_power_W|W|单业务当前时间片所有任务计算功率总和，不包含传输和平台静态功率。
energy_J|J|该行对应的总增量能量：all_slot_metrics为本片单业务服务段能量之和，旧矩阵compare为运行summary能量。
ever_allocated|个|曾经至少成功分配一次的任务数，包括后续可能掉线的任务。
final_accepted|个|传统summary中最终未标为掉线的成功分配任务数；并非观察终点仍同时运行的任务数。
final_qos_rate,qos_overall|0～1|全部生成请求中最终保留且最新分配满足时延/丢包约束的比例；不是仅已接纳任务的QoS通过率。
slot_sec|s|拓扑时间片长度。
duration_sec|s|单业务compare中每个任务设置的持续时长，通常等于slot_sec；不是整个仿真总时长。
base_arrival_per_slot|任务/片|配置中的原始业务到达强度，压力/固定数量模式可被覆盖；不等于实测生成数量。
stress_mode|布尔|是否开启单业务并发压力生成机制，配置背景与高峰以达到并发目标。
scenario_concurrency_cap,concurrency_cap|个|本场景实际在服业务硬上限，现行场景1/2/3为200/250/500。
peak_offered_ratio|倍数|压力实验峰值投放数量相对并发上限的目标比例，超过1可产生硬上限拒绝。
avg_generated_concurrency|个/片|单业务整片任务实验每片生成数的平均；只在到达片首、持续整片时才等价于投放并发。
max_generated_concurrency|个/片|单业务整片任务实验每片生成数的最大值，是需求侧数量，不等于实际接纳峰值。
avg_generated_cap_ratio|0～数倍|平均每片生成数/并发上限；允许大于1。
avg_active_concurrency|个|单业务各片实际在服快照数的算术平均。
avg_active_cap_ratio|0～1|上述平均实际在服数/并发上限。
max_concurrency,max_conc|个|该运行实际在服数的最大记录值。传统按片快照，事件版含片内峰值。
tag|文本|旧算法矩阵实验标签，可表示求解器、策略或消融设置；需联合scenario/strategy/solver解释。
compress_save|0～1|1−成功服务任务压缩比例ρ的均值；只反映输出缩减，不等于端到端实际带宽或总能量节省比例。
node_dist|Python字典文本|旧矩阵按计算节点类型统计的成功任务数，如{'LEO': 100}；不是标准JSON，只能安全用ast.literal_eval解析。
slots|整数|本次运行的拓扑时间片数量，仿真总长需结合slot_sec，不是任务数。
steady_offered,peak_offered|个/片|单业务压力测试背景/高峰片计划生成任务数，实际接纳可因上限和可行性而更少。
avg_generated,max_generated|个/片|单业务压力测试各片生成数量的平均/最大值；不是随机到达混合试验的时间平均在服数。
compute_node_distribution|文本|按LEO/GEO/EDGE/DC等节点类型统计成功任务分布，历史格式通常以分号和冒号编码；不是JSON或逐节点利用率。
success_rate|0～1|单业务摘要为成功接纳数/生成数；技术报告统一表对不同阶段另有定义，见其专表。
geo_source_tasks,geo_destination_tasks|个|生成任务中源为GEO/至少一个目的为GEO的任务数量；后者不是目的节点去重数，LL现行应为0。
success_pct|%|较早星地单业务试验成功接纳占生成的百分数，已乘100。
initially_accepted|个或0/1|生命周期表为单任务是否曾成功分配的0/1；汇总表为曾成功分配的任务数量，包含后续掉线。
completed|个|在观察窗口内达到实际预定结束时刻并正常完成的任务数。
active_at_end|个|观察停止时仍在正常服务、预定结束在窗口之后的任务数；不算掉线，也不能算完整完成。
final_survived|个|completed+active_at_end；窗口截断版期末在服为0时才等于完整完成数量。
final_survival_rate|0～1|final_survived/generated，分母包含初始拒绝。
time_avg_active|个|全部实服任务秒/观察窗口长度T=∫active(t)dt/T；分业务可加总为ALL。
lifetime_delay_mean_ms|ms|每个曾接纳任务先取全部服务段的最大目的时延，再在任务间等权平均，包含后来掉线前已服务的任务。
lifetime_delay_p95_ms|ms|上述生命周期最坏时延任务样本的95分位，不是对路由段直接取P95。
lifetime_delay_max_ms|ms|全部曾接纳任务生命周期最坏时延中的最大值。
service_weighted_delay_ms|ms|Σ(路由段最坏目的时延×该段服务秒)/Σ服务秒；长服务段权重大，与任务等权均值不同。
energy_per_initially_accepted_J|J/任务|全部窗口实际能量/初始曾接纳任务数，包含后来掉线任务已发生的能量；不是每完整完成任务耗能。
observed_input_Gbit|Gbit|实际已服务的原始输入bit总和/10^9，不含拒绝需求及掉线后未服务部分。
source_compute_segment_pct|%|compute_node等于src的分配段数/全部分配段数×100，不按任务数或服务秒加权。
ground_compute_segment_pct|%|计算节点类型为EDGE/DC的分配段数/全部分配段数×100，不是地面算力占用率。
peak_concurrency|个/空|实际在服峰值；早期mixed_metrics只ALL有值，后续增强版分业务也计算各自峰值。
variant|文本|业务时间/到达方案标记，如fixed30和variable_random；不等于策略，同variant仍须区分目录和批次。
lifetime_loss_max|0～1|所选曾接纳任务生命周期最坏目的累计丢包率的最大值。
compute_delay_service_weighted_ms|ms|Σ(段处理时延×段服务秒)/Σ段服务秒，与tasks.csv最后一次处理时延不同。
time_at_cap_s|s|实际active等于并发硬上限的累计事件区间长度；只在总体ALL行定义。
time_at_cap_pct|%|time_at_cap_s/T×100，正式T=3600时为time_at_cap_s/36。
time_above_90pct_cap_s|s|实际并发≥90%硬上限的累计区间长度，虽字段名above，代码含等于。
first_cap_t|s/空|第一次观测到实际在服数达到并发上限的时刻；从未达到留空，不应补0。
mean_compute_power_W|W|窗口计算总能量/T，包括无业务时段；分业务项可相加。
mean_tx_power_W|W|窗口传输增量能量/T，是模型平均传输动态功率，不是射频设备铭牌功率。
mean_total_power_W|W|窗口总动态能量/T=mean_compute_power_W+mean_tx_power_W。
peak_tx_power_W|W|同一时刻全部生效服务段传输增量功率之和的最大值，段功率由段传输能量/段秒计算。
peak_total_power_W|W|同一时刻计算+传输增量功率的总和峰值；不能用两个独立峰值直接相加。
initially_accepted_survival_rate|0～1|final_survived/initially_accepted，用于衡量初始接纳后的持续服务能力。
wall_seconds|s|运行整组仿真的墙钟耗时，与业务时延、仿真3600秒均不同。
pre_pulse_time_avg_active|个|脉冲前[0,1800)内实际active积分/1800，反映突发前背景在服量。
post_pulse_time_avg_active|个|脉冲结束后[1830,3600]内实际active积分/1770，不包括1800～1830脉冲窗口。
name|文本|到达机制名称，当前稀疏峰值方案为sparse_peak。
schedule_seed|整数|随机背景任务分配到各时间片的种子，不等于业务属性或片内时间种子。
nominal_background_fraction|0～1|用Little定律估算背景任务总量的名义并发占比，当前0.2；不是最终实测平均并发保证。
weighted_mean_duration_s|s|按业务生成比例3:2:3:1对各业务时长分布均值加权，用于估算背景投放量。
background_tasks,pulse_tasks|个|全窗口背景生成任务总数/额外脉冲任务总数；二者之和为生成总量，不是同时在服数。
pulse_start_s,pulse_end_s|s|突发到达窗口起止时刻，当前1800～1830 s；突发任务可在该窗口后继续执行。
offered_peak|个|仅按所有请求到达/结束时间预测的需求侧峰值，未经过路由、QoS和并发上限筛选。
offered_time_avg_active|个|全部请求的窗口内持续任务秒/T，即需求侧平均并发；不等于实际服务平均并发。
ratio_scope|文本|业务数量比例保证的范围，whole_horizon表示整个3600 s满足3:2:3:1，而非每一片均满足。
pulse_sizing|文本|脉冲数量标定方法；当前按9任务为一组，选择需求预测峰值首次达到105%上限的组数。
reason|文本|审计中的原始初始拒绝原因，如scenario_concurrency_cap/no_feasible。
optimistic_delay_lower_bound_ms,delay_lower_bound_ms|ms/空|当时拓扑的乐观传播时延下界（部分脚本加固定处理项）。高于预算可证该时刻时延不可行，低于预算不保证资源/QoS可行。
classification,cause|文本|对拒绝/掉线原因的解释性结论，应与数值下界、预算、原始状态联合使用，不能替代原始日志。
diagnosed_reason|文本|在同片满资源反事实重算得到的分类，RESOURCE_CONTENTION/DELAY_BUDGET/LOSS_BUDGET等；受求解器候选覆盖和诊断配置影响。
kind|文本|混合失败记录类型，如DROPPED或初始拒绝；应先区分接纳前失败与接纳后失败。
old_accepted,new_accepted|个|算法修改前/后的成功接纳任务数；必须在相同场景、业务、策略及试验条件下解释。
old_no_feasible,new_no_feasible|个|算法修改前/后的初始no_feasible拒绝数量。
old_energy_J,new_energy_J|J/任务|注意旧字段名省略了per_task：实际为修改前/后平均每个成功任务能耗，绝不是整次运行总能耗。
old_peak_link_pct,new_peak_link_pct|%|算法修改前/后最忙单链路利用率的全程峰值，已乘100。
common_tasks|个|两策略在同一任务的至少一个共同服务区间有交集的任务数；只适用于相同输入的配对运行。
common_service_seconds|任务·s|两策略对同一任务实际服务区间交集的累计任务秒，不是双方服务秒取较小总和。
common_input_Gbit|Gbit|共同服务区间内原始输入量/10^9，两策略在此使用完全一致分母。
latency_common_energy_J,strategy_common_energy_J|J|latency基线/当前strategy在共同任务、共同服务区间内的能量；按各段功率乘交集秒重算。
common_energy_reduction_pct|%|100×(1−strategy共同能量/latency共同能量)；正值表示相对基线节能，负值表示增耗，基线0时为空。
latency_common_energy_J_per_Gbit,strategy_common_energy_J_per_Gbit|J/Gbit|基线/当前策略共同服务能量除共同原始输入Gbit。
latency_common_delay_ms,strategy_common_delay_ms|ms|基线/当前策略在共同服务区间内按服务秒加权的路由最坏目的时延。
batch|文本|A/B批次标记；必须加入跨批次关联和分组键。
comparison|文本|比较设计标签。unpaired表示输入不同的非配对比较，结果差异同时受工作负载和算法影响。
batch_A_sha256,batch_B_sha256|SHA-256文本|旧A批/新B批该场景该策略的业务输入指纹，不是输出CSV字节哈希。
differs_from_previous|PASS标记|新业务输入指纹与旧批次不同的检查结果。
three_strategies_different|PASS标记|同场景三策略分别使用不同业务输入的检查结果。
unchanged_physics_and_algorithms|PASS标记|除随机种子/输入到达计划/输出位置等允许项外，物理及算法配置一致的检查结果，不等于性能相等。
latency_generated,strategy_generated|个|独立输入latency基线/当前策略各自生成任务数，两者可以不同。
latency_input_Gbit,strategy_input_Gbit|Gbit|独立输入latency基线/当前策略各自实际已服务原始输入量。
latency_energy_J_per_Gbit,strategy_energy_J_per_Gbit|J/Gbit|独立输入latency基线/当前策略各自单位实服原始输入能量。
normalized_energy_difference_pct|%|100×(当前策略J/Gbit÷latency J/Gbit−1)；负值表示归一化能量较低，与common_energy_reduction_pct符号相反，不是同任务因果节能率。
latency_delay_ms,strategy_delay_ms|ms|非配对两运行各自生命周期最坏时延的任务均值，输入难度不同，不应解读为同任务时延改善。
offered_input_Gbit|Gbit|所有生成请求原始输入总需求/10^9，包括拒绝和后来未服务部分，与observed_input_Gbit不同。
offered_service_seconds|任务·s|全部请求实际duration_s之和，有限窗口生成已截断；不扣拒绝或掉线。
mean_rate_mbps|Mbps|生成任务原始输入速率的任务等权均值，不按持续时长加权。
mean_actual_duration_s|s|生成任务实际持续时间的算术均值，使用截止截断后的duration_s。
distinct_sources|个|所选请求的不同源节点数量，不是源端计算任务数。
distinct_destination_sets|组|去重后的目的列表/集合编码数量，不是不同目的节点总数；列表顺序不同也可能视为不同编码。
phase|文本|技术方案报告实验阶段E1～E8，需结合experiment_catalog或报告章节了解条件。
metric_rows|行|该阶段统一指标表的行数，含ALL和分业务行，不等于独立仿真运行数量。
energy_and_status|文本|该阶段使用的能量及终态统计口径说明。
number|整数|文档内图号或公式组编号，依所在文件区分编号域。
title|文本|对应图的标题。
path|路径文本|来源文件或图片路径，通常相对项目code目录解析；不是仿真路由路径。
source|文本|图/公式使用的来源CSV、配置或代码说明，用于追溯。
analysis|文本|报告对应图的分析说明，不是额外观测数据。
label|文本|公式组名称或用途标签。
formula|文本|可读公式定义，Word中另可有原生数学对象。
field|文本|被说明的统一指标英文列名。
definition|文本|该列计算口径、样本范围和含义说明。
bytes|byte|来源文件字节大小，不是业务数据量。
sha256|SHA-256文本|来源文件原始字节的SHA-256，用于完整性追溯；与input_sha256不同。
raw_node_cv,reviewed_node_cv|无量纲|原事件导出的节点CV时间平均/独立按活动路由重建的复核值。本批复核浮点一致，不代表原值有错。
raw_node_jain,reviewed_node_jain|0～1|原导出/独立重建的节点Jain时间平均，本批验证一致。
theoretical_cv_upper_bound|无量纲|n个非负利用率的CV上界sqrt(n−1)，n为该场景纳入统计的计算节点数。
raw_exceeds_theoretical_bound|布尔|原节点CV是否超过上述理论上界；False表示未超过，不是算法性能优劣结论。
node_peak_matches_original|PASS标记|独立重建的单节点峰值利用率与原始导出一致的检查结果。
method|文本|节点负载矩统计复核方法，包括活动分配求和、纳入空闲节点等。
"""

DEFINITIONS = {}
for line in SPEC.strip().splitlines():
    names, unit, meaning = line.split('|', 2)
    for name in names.split(','):
        DEFINITIONS[name] = (unit, meaning)

# Avoid slash-pair ambiguity when a reader looks up only one specific column.
DEFINITIONS.update({
    'e_compute_J': ('J', '任务计算增量能耗；事件版累计全部实际服务段，传统版为最后分配的任务估算；tasks.csv已按显示格式舍入。'),
    'e_tx_J': ('J', '任务传输增量能耗；事件版累计全部实际服务段，传统版为最后分配的任务估算；含MC各目的复制传输。'),
    'e_total_J': ('J', '任务合计增量能耗=e_compute_J+e_tx_J；事件版为窗口实际累计，传统版为最后分配估算；显示格式有舍入。'),
    'remain_before': ('见unit', '此次变更前剩余容量，不是已占用量；变更类型及单位见resource_type和unit。'),
    'remain_after': ('见unit', '此次变更后剩余容量；该状态利用率=1−remain_after/total_capacity，不是allocation_delta/total_capacity。'),
    'min_active': ('个', '单业务传统整片试验各片在服快照的最小值；不与随机事件实验末尾归零直接比较。'),
    'avg_active': ('个', '单业务传统等长时间片在服快照的算术平均；此类任务片首到达且整片持续，不是一般事件任务片末快照平均。'),
    'max_active': ('个', '单业务传统整片试验各片在服快照的最大值。'),
    'link_util_mean': ('0～1', '全体有容量链路利用率算术均值，含空闲链路；事件slot_stats再按片内时间加权。'),
    'node_util_mean': ('0～1', '全体有算力容量节点利用率算术均值，含空闲节点；事件slot_stats再按片内时间加权。'),
    'link_util_max': ('0～1', '该状态最忙单链路利用率；事件slot_stats为片内全部观察状态中的单链路峰值。'),
    'node_util_max': ('0～1', '该状态最忙单算力节点利用率；事件slot_stats为片内全部观察状态中的单节点峰值。'),
    'old_energy_J': ('J/任务', '改进前平均每个成功任务的能耗，实际读取旧avg_energy_per_task_J；字段名省略per_task，不是运行总J。'),
    'new_energy_J': ('J/任务', '改进后平均每个成功任务的能耗，实际读取新energy_mean_J；字段名省略per_task，不是运行总J。'),
})

# Enumerate business-specific fields; every concrete name still gets a dictionary row.
for biz in ('LL', 'HR', 'HC', 'MC'):
    for prefix, unit, meaning in (
        ('active_', '个', '该观察点实际在服的{b}任务数。'),
        ('active_mean_', '个', '本片{b}实际在服数的时间平均；四业务之和等于active_mean。'),
        ('background_', '个/片', '该片随机背景计划中{b}新到达请求数，不包括额外脉冲。'),
        ('arrivals_', '个/片', '该片全部{b}新到达请求数=背景+脉冲，不等于在服数。'),
        ('qos_', '0～1', '全部生成的{b}请求中最终保留且最新分配QoS通过的比例。'),
    ):
        DEFINITIONS[prefix + biz] = (unit, meaning.format(b=biz))
for kind in ('LEO', 'GEO', 'EDGE', 'DC'):
    DEFINITIONS[kind + '_pct'] = ('%', f'较早单业务试验中成功任务选择{kind}作为计算节点的比例，已乘100；不是节点资源利用率。')

INFO = {
    'generated_tasks.csv': ('任务输入全集', '每个生成请求一行，包括全部拒绝请求。', 'task_id', '检查生成量、业务比例、端点、时长与原始算力需求。', 'metrics.py:Metrics.export'),
    'tasks.csv': ('任务结果速览', '每个曾成功分配任务一行，再附初始拒绝任务；同一运行正常为每任务一行。', 'task_id', '快速查看终态、最后计算节点与最后路由时延；生命周期分析不要只用本表。', 'metrics.py:Metrics.export'),
    'routing_allocations.csv': ('路径与算力路由服务段', '每次初始/重算/迁移成功分配一行。', 'allocation_id；task_id是一对多外键', '还原具体源→计算点→各目的路径、链路带宽和各段能量。', 'metrics.py:record_alloc/close_segment'),
    'resource_allocations.csv': ('带宽、算力、功率变更流水', '每次分配或释放的每条链路/每个计算资源一行。', 'event_seq；allocation_id外键', '审计资源占用/释放，重建同一拓扑快照内的资源状态。宜流式读取。', 'resources.py:ResourceLedger._emit；metrics.py'),
    'slot_stats.csv': ('时间片统计', '每个拓扑时间片一行，传统与事件版含义不同。', 't', '绘制并发与利用率时序，事件版使用active_mean和active_max。', 'metrics.py:record_slot；event_simulation.py'),
    'lifecycle_tasks.csv': ('任务生命周期汇总', '每个生成任务一行，含拒绝。仅相应事件实验导出。', 'task_id', '优先用于跨片完成/掉线、任务最坏时延与累计实际能耗分析。', 'experiments/run_mixed_3600.py:extra_outputs'),
    'event_timeline.csv': ('片内精确事件状态', '每个观察点一行，同一时间可能有多行前后状态。', '文件行序+t；无独立event_seq', '积分得到平均并发/计算功率；追踪到达、完成、换片后的状态。', 'event_simulation.py:observe'),
    'duration_adjustments.csv': ('原时长与3600 s截断审计', '每个生成请求一行；仅相应有限窗口实验导出。', 'task_id', '确认全部实际任务结束≤3600，并对照未截断的采样时长。', 'experiments/run_bounded_mixed.py；run_randomized_batch.py'),
    'arrival_schedule.csv': ('分片到达计划', '每个时间片一行，按四业务给出背景与全部到达数。', 'slot', '检查突发注入位置及各片请求量；实际精确时间仍取generated_tasks。', 'experiments/run_mixed_3600.py及批次运行器'),
    'compare.csv': ('算法/单业务比较入口（多版本）', '旧矩阵每个实验设置一行；单业务运行器每个业务一行。', '目录+scenario+strategy+solver+biz_only/tag', '须按真实表头识别矩阵版、普通单业务版或压力单业务版，不可盲目拼接。', 'experiments/run_matrix.py；run_single_biz.py'),
    'single_biz_redefined_summary_20261016.csv': ('重新定义三场景的单业务低负载摘要', '场景×业务，每组合一行，共12行。', 'scenario+biz', '查看并发压力提升前的单业务基准；不是最新混合策略结果。', '早期单业务汇总流程及原始运行文件'),
    'concurrency_stress_summary_20261016.csv': ('单业务并发压力摘要', '场景×业务，共12行。', 'scenario+biz', '核对曾达到并发上限及平均并发约束，查看latency压力基准。', '单业务压力汇总流程及原始运行文件'),
    'single_biz_satellite_dc_summary_20261016.csv': ('较早星地/DC单业务摘要', '场景×业务的历史结果。', 'scenario+biz', '保留实验演进记录；其场景定义早于后来的三场景重定义，不与新结果直接混用。', '早期星地/DC实验汇总'),
    'all_metrics.csv': ('单业务三策略完整指标', '场景×单业务×策略，共36行。', 'scenario+biz+strategy', '单业务三策略比较的正式入口；配对字段仅在相同输入中使用。', 'experiments/build_all_strategy_report.py:collect'),
    'all_slot_metrics.csv': ('单业务三策略分片资源指标', '每运行每时间片一行，共360行。', 'scenario+biz+strategy+t_sec', '绘制单业务压力试验的链路/节点公平性与功率曲线。', 'experiments/build_all_strategy_report.py:collect'),
    'algorithm_before_after.csv': ('算法改进前后对照', '场景×业务×非latency策略，共24行。', 'scenario+biz+strategy', '核对补充搜索/算法修改后可行性、每任务能量和链路热点变化。', 'experiments/build_all_strategy_report.py'),
    'rejection_audit.csv': ('单业务初始拒绝审计', '每个初始拒绝请求一行。', 'scenario+biz+strategy+task_id', '区分并发硬上限拒绝和传播下界超预算，不证明下界内一定可行。', 'experiments/build_all_strategy_report.py:collect'),
    'rejection_diagnostics.csv': ('旧拒绝原因反事实诊断', '选定业务中每个初始拒绝请求一行。', '运行目录+task_id', '以同片满资源快照重新尝试/放宽约束分类；需匹配当时配置和算法版本。', 'experiments/analyze_rejections.py'),
    'mixed_failure_diagnostics.csv': ('混合实验拒绝/掉线诊断', '每个被检查的初始拒绝或后续掉线一行。', 'scenario+variant+task_id+kind+t_sec', '核对失败时刻传播下界是否超过预算；只覆盖文件中实际检查的失败。', '混合实验报告构建/诊断流程'),
    'arrival_profile_summary.csv': ('低背景+短峰到达机制摘要', '每场景一行，共3行。', 'scenario', '对照需求侧投放和实际服务并发，说明脉冲前后背景负载。', 'experiments/revise_mixed_3600_report.py'),
    'common_service_comparison.csv': ('同输入共同服务区间配对比较', '场景×非latency策略×ALL/四业务，共30行。', 'scenario+strategy+biz', '只在已验证相同任务输入时比较重叠服务区间能量和加权时延。', 'experiments/run_mixed_strategies.py:common_comparison'),
    'cross_batch_comparison.csv': ('A/B不同随机业务跨批次比较', '场景×策略×ALL/四业务，共45行。', 'scenario+strategy+biz', '查看同策略面对不同业务批次的变化，不按task_id跨批次配对。', 'experiments/run_randomized_batch.py:cross_batch'),
    'batch_input_audit.csv': ('独立业务输入指纹审计', '场景×策略，共9行。', 'scenario+strategy', '验证新旧输入不同、三策略输入不同且物理/算法配置未变。', 'experiments/run_randomized_batch.py:summarize_batch'),
    'unpaired_strategy_comparison.csv': ('独立输入三策略非配对对照', '场景×balance/energy，共6行。', 'scenario+strategy', '查看各自J/Gbit和时延，差值不能称同任务因果收益。', 'experiments/run_randomized_batch.py:summarize_batch'),
    'workload_summary.csv': ('需求工作负载摘要', '场景×策略×ALL/四业务，共45行。', 'scenario+strategy+biz', '比较生成输入难度、数据量、时长和源目的多样性，解释非配对差异。', 'experiments/run_randomized_batch.py:cross_batch'),
    'report_data.csv': ('技术方案报告统一指标底表', 'E1～E8共225个指标行，含ALL及分业务，非225次独立仿真。', 'phase+scenario+strategy+biz', '复现技术方案图表；按阶段和业务筛选，保留空值，不作为新的原始仿真数据。', 'experiments/build_technical_scheme_report.py'),
    'metric_dictionary.csv': ('技术方案统一指标字典', 'report_data的每个字段一行。', 'field', '查询统一别名、单位与跨阶段口径。不是模拟器指标观测值。', 'experiments/build_technical_scheme_report.py'),
    'experiment_catalog.csv': ('技术方案实验阶段索引', 'E1～E8每阶段一行。', 'phase', '定位实验比较设计与指标表行数。', 'experiments/build_technical_scheme_report.py'),
    'figure_index.csv': ('技术方案图表索引', '每幅图一行，共37行。', 'number', '定位图文件、来源和对应分析；不能用其行数当实验数量。', 'experiments/build_technical_scheme_report.py'),
    'formula_index.csv': ('技术方案公式索引', '每组公式一行，共43行。', 'number', '查询算法/模型公式及代码来源。', 'experiments/build_technical_scheme_report.py'),
    'source_file_index.csv': ('报告来源文件指纹', '每个被追踪的来源文件一行。', 'path', '核验报告依据的文件版本，配合sha256检查文件完整性。', 'experiments/build_technical_scheme_report.py'),
    'node_cv_recalculation.csv': ('节点负载矩独立复核', '被复核的混合总体运行一行，共33行。', 'phase+scenario+strategy', '核对原始CV/Jain与活动分配重建值；本批一致，不是新一轮实验。', 'experiments/build_technical_scheme_report.py:review_node_statistics'),
}

MIXED_NAMES = ('mixed_metrics.csv', 'mixed_all_metrics.csv', 'strategy_summary.csv',
               'new_strategies_metrics.csv', 'two_batch_metrics.csv',
               'mixed_all_metrics_partial.csv', 'strategy_summary_partial.csv',
               'new_strategies_metrics_partial.csv')
for name in MIXED_NAMES:
    grain = '单次运行ALL+四业务，共5行。' if name == 'mixed_metrics.csv' else '多运行ALL+四业务汇总；完整九组通常45行。'
    use = '混合业务全指标入口，优先biz=ALL看全网，再按四业务分解；不可把ALL与分业务一起累加。'
    if name.startswith('strategy_summary'):
        grain = '只保留biz=ALL，每场景×策略一行，完整九组9行。'
    elif name.startswith('new_strategies_metrics'):
        grain = '仅balance和energy的ALL及分业务，完整六组30行。'
        use += ' 文件名沿用历史；有限窗口批次中九策略场景组合实际都重新运行，不代表latency复用旧结果。'
    elif name == 'two_batch_metrics.csv':
        grain = 'A、B两批合并，共90指标行。'
        use += ' 必须把batch加入分组键；A/B不是同输入重复试验。'
    if '_partial' in name:
        use += ' 这是当时运行中的不完整预览，正式比较优先使用同目录无_partial文件。'
    INFO[name] = ('混合业务指标汇总', grain, '目录/批次+scenario+strategy/variant+biz', use, 'run_mixed_3600.py:extra_outputs及各混合批次运行器')
for name in ('mixed_all_slot_metrics.csv', 'mixed_all_slot_metrics_partial.csv'):
    INFO[name] = ('混合业务跨运行时间片表', '每运行每时间片一行；完整九组×120片通常1080行。',
                  '目录/批次+scenario+strategy/variant+t',
                  'slot_stats加场景与策略/方案标签。_partial只含当时已完成组，不能当正式全量。',
                  'experiments/run_mixed_strategies.py及混合批次运行器')
INFO['common_service_comparison_partial.csv'] = (*INFO['common_service_comparison.csv'][:3],
    INFO['common_service_comparison.csv'][3] + ' 本文件是不完整过程预览，优先用无_partial正式版本。', INFO['common_service_comparison.csv'][4])


def definition(filename, field, report_dictionary):
    """No generic fallback: an undocumented actual field fails the build."""
    if filename == 'report_data.csv':
        return report_dictionary[field]
    if filename in ('metric_dictionary.csv',) and field == 'unit':
        return ('文本', '统一指标的数值存储单位/比例范围，不是资源类型枚举。')
    if filename == 'cross_batch_comparison.csv':
        for prefix, label in (('batch_A_', '旧A批'), ('batch_B_', '新B批'), ('change_pct_', '跨批变化')):
            if field.startswith(prefix):
                base = field[len(prefix):]
                unit, meaning = DEFINITIONS[base]
                if prefix == 'change_pct_':
                    return ('%', f'{base}的100×(B/A−1)。A=0或指标缺失时留空；比例指标也是相对变化，不是百分点差。原指标：{meaning}')
                return (unit, f'{label}的{base}。{meaning}')
    if filename in ('event_timeline.csv',) and field == 'event':
        return ('文本', 'TOPOLOGY_REROUTED、BEFORE_ARRIVAL、ARRIVAL、BEFORE_FINISH、FINISH、SLOT_END等观察点；同刻行序保留状态先后。')
    if filename in ('resource_allocations.csv',) and field == 'event':
        return ('文本', 'ALLOCATE初始占用、REROUTE_ALLOCATE重算占用、RELEASE_FINISHED完成释放、RELEASE_SLOT_END换片释放、RELEASE_HORIZON终点释放；历史还可有REAPPLY/PREEMPT_RELEASE等。')
    if filename == 'lifecycle_tasks.csv' and field == 'initially_accepted':
        return ('0/1', '该任务是否至少成功分配一次；1包含后续COMPLETED、ACTIVE_AT_END或DROPPED，0为初始拒绝。')
    if filename in ('all_metrics.csv', 'single_biz_redefined_summary_20261016.csv', 'concurrency_stress_summary_20261016.csv', 'single_biz_satellite_dc_summary_20261016.csv') and field == 'accepted':
        return ('个', '该单业务实验成功分配任务数；当前这些整片30 s正式组没有跨片掉线，等于初始接纳数。')
    if filename == 'compare.csv' and field == 'accepted':
        return ('个', '仅旧矩阵版出现：最终未被标记掉线的成功服务任务数，来自传统Metrics.summary；与ever_allocated不同。')
    if filename == 'all_slot_metrics.csv' and field == 'active':
        return ('个', '本片调度后实际接纳快照数量；此实验所有任务从片首持续30 s，等于该片服务并发。')
    if filename == 'all_slot_metrics.csv' and field == 'energy_J':
        return ('J', '该片全部成功任务的增量总能耗；整片30 s无重算试验可逐片相加为运行总量。')
    if filename in ('single_biz_redefined_summary_20261016.csv', 'concurrency_stress_summary_20261016.csv') and field == 'max_delay_ms':
        return ('ms', '该场景该单业务成功分配任务中端到端时延的最大值；任务内先取最坏目的。')
    if filename == 'lifecycle_tasks.csv' and field == 'status':
        return ('文本', 'COMPLETED完整完成、ACTIVE_AT_END期末仍在服、DROPPED接纳后掉线、REJECTED初始拒绝。')
    return DEFINITIONS[field]
