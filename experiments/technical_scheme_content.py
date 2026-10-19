"""Code-grounded technical chapters for the reconstructed Chinese report."""
from __future__ import annotations
import math


def build_technology(r,cfg,network_counts):
    r.h1('1. 项目范围、证据与阅读约定')
    r.p('本方案描述星上产生业务、网络内选择计算节点并进行处理前/处理后路由的仿真系统。“智能路由”指利用时延、资源与能耗信息进行联合选路和计算放置，当前代码没有训练神经网络或强化学习模型。本文重构已有实验与代码，不新增仿真，不改变算法、硬件参数或已有原始CSV。')
    r.p('资料基准为results/mixed_3600_latency_20261016/全场景全策略单业务测试.docx的最新42页版本，以及项目根目录同名旧副本、各阶段CSV/JSON和当前源码。根目录旧副本只包含较早的混合实验，不能替代最新结果。技术部分以当前实现为准；历史实验以各自保存的数据、配置和报告为准，不用今天的config.yaml反推过去的全部条件。')
    r.t(['标记','含义','结论边界'],[
        ['当前实现','从本次检查的源码和配置读取','可说明现在如何计算，不证明旧结果由完全相同版本生成'],
        ['已实测','存在可追溯CSV/JSON及必要审计','只能支持相应场景、负载和统计口径'],
        ['推断/解释','结合目标函数、模型及结果推断原因','不是独立消融验证，不能等同于因果证明'],
        ['理论模型/待修正','表达设计目标或指出实现差异','不是已经运行过的新算法结果']])
    r.p('当前正式全规模三策略结果主要使用S1；S0–S5是六种求解器，不是六种业务或六种策略。旧results/compare.csv包含S0–S3的39行历史矩阵及消融，但缺少对应有效配置和详细运行目录，且场景/功率版本与当前定义不同，单列为历史档案，不并入当前参数下的算法性能排名。S4/S5没有本轮正式大型结果，不编造曲线或最优性差距。')
    r.h1('2. 三场景设计、星座与链路参数')
    r.h2('2.1 场景组成与算力节点权限')
    r.t(['场景','参与网络的节点','业务端点','可选计算节点','并发硬上限','t=0链路数'],[
        [1,'L1：500 LEO','LEO','LEO',200,network_counts[1]['links']],
        [2,'L1：500 LEO＋18 EDGE＋10 DC','LEO','LEO、EDGE、DC',250,network_counts[2]['links']],
        [3,'L1与L2各500 LEO＋6 GEO','LL仅LEO；其余LEO/GEO','LEO、GEO',500,network_counts[3]['links']]])
    r.p('config.yaml定义50个地面城市，其中18 EDGE、10 DC、22 ACCESS。场景2的compute_only仅启用28个EDGE/DC，ACCESS不参与；场景1与3不启用地面。地面节点既可作为候选计算点，也可成为转发中继，不能作为本轮业务源或目的。GEO在场景3参与路由与计算，但LL的源、目的禁止GEO；这不等于通用代码绝对禁止LL路径经过GEO，最终仍由20 ms约束筛除长路径。')
    r.p('并发上限作用于已经实际接纳、尚未完成的任务数，不是到达率，也不是预留算力份额。达到上限后，新请求即使尚有带宽与算力也会被scenario_concurrency_cap拒绝；因此必须将容量拒绝与no_feasible区分。t=0链路数是快照统计，不是全程固定数量。')
    r.h2('2.2 轨道几何、运行周期与连接规则')
    mu=3.986004418e14;radius=6378137.0
    r.t(['对象','数量/结构','高度km','倾角','相位/位置','圆轨道周期s'],[
        ['L1','20面×25颗＝500',550,'53°','phase=1；RAAN覆盖[0,180°)',f'{2*math.pi*math.sqrt((radius+550e3)**3/mu):.2f}'],
        ['L2','20面×25颗＝500',1100,'87°','phase=1；仅场景3启用',f'{2*math.pi*math.sqrt((radius+1100e3)**3/mu):.2f}'],
        ['GEO','6颗',35786,'0°','经度0、60、120、180、−120、−60°',f'{2*math.pi*math.sqrt((radius+35786e3)**3/mu):.2f}']])
    r.eq('圆轨道与相位', 'a=R_E+h；n=√(μ/a³)；T_orbit=2π/n；M(t)=M₀+nt', 'topology.py:build_shell, propagate_satellite')
    r.eq('Walker-Star初始位置', 'Ω_p=πp/P；M₀(p,s)=2πs/S+2πFp/(PS)；p=0…P−1，s=0…S−1', 'topology.py:build_shell')
    r.eq('旋转到地固坐标', 'x_ECEF=R_z(−ω_Et)R_z(Ω_p)R_x(i)[a cos M，a sin M，0]ᵀ', 'topology.py:propagate_satellite, eci_to_ecef')
    r.p('常数为R_E＝6378.137 km、μ＝3.986004418×10¹⁴ m³/s²、地球角速度7.2921159×10⁻⁵ rad/s、光速299792458 m/s。轨道是简化圆轨道模型，不包含J2摄动、姿态、太阳遮挡或真实星历；GEO按同一圆轨道传播公式构造。RAAN使用πp/P，不应改写成与当前代码不同的2πp/P。')
    r.p('同面卫星按环形连接；同壳层邻面按近距离候选连接；允许跨壳层近邻连接；每个可见LEO–GEO对可建链。近邻连接代码对有限候选尝试，并非遍历所有可见卫星形成全连接图；没有GEO–GEO直连构造。星地链路检查最低仰角；DC间互连、EDGE连接最近两个DC及最近EDGE。')
    r.p('视线检查使用closest_distance≥R_E−50 km的容差；这比严格的地球遮挡边界更宽松，是模型参数而非工程可见性保证。地面TER传播为欧氏弦长/c再乘1.5，不是实测光纤路由时延。network.py顶部仍留有旧场景注释，本文采用实际scenario_config/select_nodes与有效配置，不采用该旧注释。')
    r.h2('2.3 算力、功率与等效操作单位')
    r.t(['节点','等效TFLOPS','等效op/s','计算功率上限W','η J/op','CPU频率元数据'],[
        [k,cfg['compute_capacity_tflops'][k],f"{cfg['compute_capacity_tflops'][k]*1e12:.4e}",cfg['compute_power_budget_W'][k],
         f"{cfg['energy_compute_eta_J_per_cycle'][k]:.6e}",{'LEO':'1.0 GHz','GEO':'1.5 GHz','EDGE':'2.0 GHz','DC':'3.0 GHz'}[k]] for k in ('LEO','GEO','EDGE','DC')])
    r.eq('吞吐与功率联合标定','C_v=TFLOPS_v×10¹² op/s；η_v=P_v,max/C_v；P_v=η_v·Στ q_τ','models.py, topology.py, energy.py')
    r.p('以上是统一的低精度Tensor等效吞吐实验设计值，不是每种在轨卫星的实测规格，也不与FP32/FP64峰值直接混用。CSV保留cycle字段名仅为兼容，其语义是等效操作op，而不是处理器时钟周期。cpu_freq与κ仅作元数据，当前功率并不采用κf³计算。由于Pmax＝ηCmax，单节点功率利用率与算力利用率相同，两个约束在现标定下基本冗余；这不代表真实整星供电没有其他负载。')
    r.h2('2.4 链路速率、损失与能耗参数')
    mapping=[('isl_leo_leo','ISL_LEO_LEO'),('isl_leo_geo','ISL_LEO_GEO'),('sgl_leo','SGL_LEO'),('sgl_geo','SGL_GEO'),('ter','TER')]
    r.t(['链路类型','标称Mbps','每跳丢包率','jitter配置ms','β J/bit','最低仰角/适用'],[
        [label,cfg['link_params'][key]['bw_mbps'],f"{cfg['link_params'][key]['loss_rate']:.1e}",cfg['link_params'][key]['jitter_ms'],
         f"{cfg['energy_link_beta_J_per_bit'][label]:.2e}",str(cfg['link_params'][key].get('min_elevation_deg','—'))] for key,label in mapping])
    r.eq('传播时延','d_e=1000·‖x_u−x_v‖/c ms；d_TER=1.5×1000·‖x_u−x_v‖/c','network.py:_delay_ms, _ter')
    r.eq('星地与星间时变因子','g_SGL=max(0.4,min(1,(elev−elev_min)/(90−elev_min)))；g_ISL=max(0.7,min(1,1−dist/(5×10⁶)))','network.py:_sgl_factor, _isl_factor')
    r.eq('有效带宽与单位bit能量','B_e(t)=B_e,nom·g_e(t)；β_e(t)=β_e,nom/g_e(t)','network.py:_mk_link')
    r.p('信道因子按每片几何计算，片内冻结，丢包率与jitter配置不随g自动放大。当前jitter字段未加入QoS时延或随机抖动采样。地面、GEO星地参数作为通用能力保留，但当前三场景不存在“既启用GEO又启用地面”的组合，所以SGL_GEO不是本批正式结果中的活跃链路。')
    r.h1('3. 四类任务、每bit计算量与流式处理')
    r.h2('3.1 业务画像及端点生成')
    r.t(['业务','优先级','速率','原采样时长s','时延ms/损失预算','目的选择'],[
        ['LL低时延',1,'2–10 Mbps','60–300','20 / 1%','仅LEO，当前传播最近5颗中随机选1'],
        ['HR高可靠',2,'10–100 kbps','120–600','200 / 0.02%','传播最近10颗中随机选1'],
        ['HC大容量/重处理',3,'100–500 Mbps','600–1800','200 / 1%','全局卫星中随机选1'],
        ['MC多目的',4,'10–100 kbps','600–1800','200 / 1%','全局随机选3个不同目的']])
    r.p('源卫星在本场景允许端点池随机选择；近邻依据当前拓扑最短传播时延，而非地理直线距离或未来时间片可达性。业务优先级数值越小越高。LL/HR的burst与HC/MC的persist是生成标签，不表示片内按包随机突发或真实协议实现；事件实验通过random_arrivals_all_businesses使四类都可在片内随机进入。')
    r.h2('3.2 每bit操作、压缩与固定处理开销')
    r.t(['业务','处理逻辑','基础op/bit','每目的附加','3目的时等效op/bit','固定处理ms','Lref bit','压缩ρ'],[
        ['LL','低时延过滤/轻量编码',50,0,50,.05,12000,.10],
        ['HR','可靠编码处理',200,0,200,.10,8000,.33],
        ['HC','重内容编解码',1000,0,1000,1.00,1000000,.20],
        ['MC','聚合与复制',40,15,85,'0.10＋0.02×3＝0.16',12000,.05]])
    r.eq('任务计算与流量','ω_τ=ω_base+m_τω_dst；V_τ=r_τD_τ；O_τ=V_τω_τ；q_τ=r_τω_τ；r_out=ρ_τr_τ','tasks.py:gen_for_slot, models.py:Task')
    r.p('上式中m为目的数量，r以bit/s、D为持续秒数、V为原始输入bit、O为总操作数、q为op/s。处理逻辑是需求模型标签，代码并未执行真实压缩、视频编码或纠错算法，ρ和操作量是配置常数。HR的高可靠性由更严格的路径丢包预算体现，不模拟FEC重传带来的可靠性增益。')
    r.eq('流式处理块时延','t_comp(v,τ)=f_τ+[1000L_ref,τω_τ/C_v]·[1/(1−ũ_v)]；ũ_v=clip(1−C_rem/C_v,0,0.95)','energy.py:compute_latency_ms')
    r.eq('单bit变量项与摊销项','t_bit,var=ω_τ/C_v s/bit；t_block=f_τ+1000L_ref·t_bit,var·load_factor；t_bit,amort=t_block/L_ref','energy.py:compute_latency_ms；仅摊销解释')
    c=cfg['compute_capacity_tflops']['LEO']*1e12
    profiles=[('LL',50,12000,.05,2e6,10e6),('HR',200,8000,.1,1e4,1e5),('HC',1000,1000000,1.,1e8,5e8),('MC',85,12000,.16,1e4,1e5)]
    r.t(['业务','所需等效TFLOPS范围','空载LEO纯计算ps/bit','空载处理块ms','单任务峰值占400T比例'],[
        [b,f'{lo*w/1e12:.8g}–{hi*w/1e12:.8g}',f'{w/c*1e12:.6f}',f'{fixed+1000*L*w/c:.8f}',f'{100*hi*w/c:.6f}%'] for b,w,L,fixed,lo,hi in profiles])
    r.p('HC每任务最多0.5等效TFLOPS，只占单LEO 400 TFLOPS的0.125%；LL/HR/MC更小。因此当前重压更多体现在带宽而非算力饱和。纳秒以下的单bit变量项来自高度并行的等效吞吐，不应解释为串行芯片时钟周期。固定开销按块/帧记，不能再无条件乘每bit数；当前固定处理开销没有对应的额外固定能量项，时延与能耗属于简化、分别标定的模型。')
    r.h2('3.3 时长、数据量、QoS与截止语义')
    r.eq('3600秒窗口内截止','D_actual=min(D_sample,3600−a_τ)；e_τ=a_τ+D_actual≤3600；V_actual=r_τD_actual','tasks.py:end_time_sec；traffic.truncate_at_simulation_end')
    r.p('业务持续60–1800 s是会话寿命，不直接加到毫秒级数据块QoS里。单业务/固定30 s实验覆盖时长为30 s；原时长实验按业务区间抽样。新截止版本在生成时收短尾部会话并重算V和O，不改变速率q或每bit操作量；尾部实际时长可低于原采样下限。旧实验的ACTIVE_AT_END仅表示停止观察时仍在服，不能冒充完整完成，也不自动属于掉线。')
    r.h1('4. 联合路由、计算放置与公共约束')
    r.p('在快照G_t=(V_t,E_t)中，为每个任务选择一个计算节点v，以及前段P⁻：源s→v和各后段P⁺_j：v→目的d_j。前段承载原始速率r，后段各承载ρr。任务不可拆分到多个计算点；压缩仅在选中的v发生一次。共享后段链路按多个目的的副本累加，不是按共享树只传一份的Steiner多播模型。')
    r.eq('带宽需求聚合','b_τ,e=r_τ[1{e∈P⁻}+ρ_τΣ_j1{e∈P⁺_j}]；Σ_τ b_τ,e≤B_e','solvers/base.py:compute_bw_per_link；r与B统一Mbps')
    r.eq('计算与功率约束','Σ_τ q_τ1{v_τ=v}≤C_v；Σ_τ η_vq_τ1{v_τ=v}≤P_v,max；N_active≤N_cap','solvers/base.py:candidate_compute_nodes；event_simulation.py:reserve')
    r.eq('端到端QoS约束','L_τ,j=Σ_{e∈P⁻}d_e+t_comp(v,τ)+Σ_{e∈P⁺_j}d_e≤L_budget,τ，∀j','solvers/base.py:validate_and_build')
    r.eq('可靠性约束','loss_τ,j=1−Π_{e∈P⁻}(1−p_e)·Π_{e∈P⁺_j}(1−p_e)≤loss_budget,τ','solvers/base.py:path_survive, validate_and_build')
    r.p('每次成功决策必须通过公共校验：路径端点一致、两段可达、各目的时延/丢包达标、同一无向物理链路的聚合带宽够用、计算和功率预算够用。损失累计假设各跳独立；重复经过同一链路按路径出现次数计算。no_feasible表示该求解器未找出可行候选，不等价于数学上全局无解；只有独立传播下界超过预算时才可据此证明时延不可行。')
    r.eq('乐观时延下界诊断','LB_τ(t)=max_j shortest_propagation(s_τ,d_j;t)+f_τ；LB_τ>L_budget⇒当前拓扑无可行时延路径','experiments/analyze_rejections.py及报告审计')
    r.h1('5. S0–S5算法设计与实现边界')
    r.p('算法的公共输入为快照、任务、策略、algorithm参数；输出为Allocation（计算点、前后段路径、资源需求、QoS和能量），或None。求解器只提出分配，资源账本负责真正扣减。六者均按任务逐个处理当前残余资源，不是对整批业务同时全局优化。')
    r.h2('5.1 S0：最近计算节点与最少跳数基线')
    r.eq('S0节点排序与路由','v按h(s,v)升序枚举；h(s,v)=min_P |P|；P⁻、P⁺_j分别由带宽过滤后的BFS得到','solvers/s0_nearest.py:bfs, S0Nearest.solve')
    r.p('步骤：筛可用计算点→从源和各目的分别BFS→按源到计算点跳数排序→拼接两段路→返回首个公共校验通过的候选。边在BFS中只做alive与速率粗过滤，最终仍检验时延、丢包、压缩后速率和功率。它不按三策略目标重新排序，不能期待切换strategy就产生三套不同的优化选择；源卫星有算力时源计算通常位列第一。复杂度约为(1+m)次O(|V|+|E|)与候选路径恢复。')
    r.h2('5.2 S1：策略加权Dijkstra与两段联合评分')
    r.eq('S1候选决策','P⁻_v=SP_w(s,v)；P⁺_v,j=reverse(SP_w(d_j,v))；v*=first_feasible(sort_v J(v,P⁻_v,{P⁺_v,j}))','solvers/s1_dijkstra.py:rank_candidates, solve')
    r.p('从源跑一次单源最短路，从每个不同目的反向跑一次，复用距离与前驱以评估全部可用计算点。无向链路允许反向恢复。当前S1的三种J和边权见第6章。一般为(1+m)次Dijkstra约O((1+m)(|V|+|E|)log|V|)，再加候选路径恢复、聚合与校验；这是结构级估计，不把候选恢复和补充搜索当作零成本。')
    r.p('balance/energy首选候选全失败时，增加传播最短路径候选族，再按原目标排序并校验。该“可行性补充搜索”不放宽QoS或扩容，不保证枚举所有可行组合。latency_lazy_candidates是按距离下界分组、惰性恢复路径的加速，保留浮点近似并列时的原评分与候选次序；不是改变目标来制造指标差异。')
    r.h2('5.3 S2：Yen备选路径与有限组合精修')
    r.eq('S2理论K短路族','P_K(s,d)={P₁,…,P_K}；P_i为按边代价排序的无环备选；候选组合来自P_K(s,v)×Π_jP_K(v,d_j)','solvers/s2_ksp.py:yen_ksp；理论搜索空间')
    r.p('实现先验证全部S1排序的直接候选，任何可行即返回；只有全部失败，才精修前refine_cap＝3个计算点，K＝3。Yen使用根路径/偏离点、禁边与禁节点生成备选。组合不是完整K^(m+1)笛卡尔积：对每条前段，仅尝试各目的采用相同索引的后段组合，每个前段遇到首个可行组合就停止。')
    r.eq('S2精修中的实际比较量','J_refine(A)=E_total(A)（energy）；J_refine(A)=max_j L_τ,j（latency或balance）','solvers/s2_ksp.py:_best_combo')
    r.p('实现差异：balance精修并未沿用S1平方利用率增量；Yen备选池在balance下使用seg_cost的最大既有利用率排序，而单源搜索用增量边权，因此不能把该分支称为对同一代价的严格K最短路或精确联合最优。top_m_candidates不是此solve精修的实际限制参数，当前使用refine_cap。它的价值是增加候选可行性，不是保证优于当前含补充搜索的S1。')
    r.h2('5.4 S3：LARAC风格的时延约束路由')
    r.eq('拉格朗日聚合权与约束','min_P c(P) subject to d(P)≤D；w_λ(e)=c_e+λd_e；D=L_budget−t_comp','solvers/s3_larac.py:_agg_dijkstra, _larac_candidate')
    r.eq('S3乘子与上下候选更新','λ=[c(P_c)−c(P_d)]/[d(P_d)−d(P_c)]；d(P_λ)≤D⇒P_d←P_λ，否则P_c←P_λ','solvers/s3_larac.py:_larac_candidate')
    r.p('先走S1直接候选；latency分支不执行LARAC精修。其余策略仅对前3个候选，以λ＝0的低代价路和λ＝10¹⁵的近似低时延路初始化，最多20次迭代，最后仍走公共硬约束。energy的边代价β×bit可加；多目的时，搜索各段的聚合权，但_eval的时延取前段＋最坏后段，存在和聚合搜索目标不完全一致的情况。')
    r.eq('S3 balance的实际内部代价','c_e=u_e^k+10⁻⁶；k=4；_eval中的c(P)=max_{e∈P⁻}u_e+Σ_jmax_{e∈P⁺_j}u_e','solvers/s3_larac.py:_base_cost, _eval；solvers/base.py:seg_cost')
    r.p('因此balance分支的加权搜索代价与乘子更新采用的路径评价并不一致，不能直接套用经典可加代价LARAC的最优性或收敛结论。本文给出其设计公式和实际偏差，不宣称它在当前大型三策略试验中已被验证更优。')
    r.h2('5.5 S4：候选级信息素与KSP路径池蚁群')
    r.eq('候选选择概率','p_i=[τ_i^αh_i^β]/Σ_l[τ_l^αh_l^β]；h_i=1/(score_i+ε)','solvers/s4_aco.py:S4Aco.solve')
    r.eq('信息素更新','τ_i←(1−evap)τ_i+Σ_{成功选择i的蚂蚁a}Q/(J_a+ε)','solvers/s4_aco.py:S4Aco.solve')
    r.eq('S4的实际评价函数','J=E_total（energy）；J=max_e b_τ,e（balance）；J=max_j L_τ,j（latency）','solvers/s4_aco.py:_obj')
    r.p('默认top-M＝10、8只蚂蚁×8轮、α＝1、β＝2、evap＝0.3、Q＝1；路径池K＝3且惰性建立。候选节点由信息素引导选择，前后段在各池中均匀随机抽取；信息素不直接附着在物理链路上。初始解仅来自top-M内首个可行直接解，不包括当前S1的全部候选及可行性补充搜索，不能笼统承诺“不劣于S1”。')
    r.p('balance的_obj是单任务在某链路上的最大申请Mbps，未归一化容量、未加入既有负载，也不是S1的利用率平方增量，属于目标一致性待完善项。随机种子使用hash(task.id)，跨Python进程可能受哈希随机化影响；仅设置simulation.random_seed不能保证S4逐次一致，复现时还需固定PYTHONHASHSEED并记录版本。')
    r.h2('5.6 S5：小图单任务MILP参照模型')
    r.p('变量y_v∈{0,1}表示计算节点；x⁻_a及x⁺_j,a∈{0,1}表示有向前/后段弧。一个无向物理链路的双向流共同消耗其剩余带宽。候选计算点已预筛算力与计算功率，必须且只选择一个。')
    r.eq('S5节点选择与设计流守恒','Σ_vy_v=1；div x⁻(n)=1{n=s}−y_n；div x⁺_j(n)=y_n−1{n=d_j}','solvers/s5_milp.py；此处前段为设计应有约束')
    r.eq('S5带宽与QoS线性约束','r(x⁻_uv+x⁻_vu)+ρrΣ_j(x⁺_j,uv+x⁺_j,vu)≤B_rem,uv；d_pre+t_comp+d_post,j≤D_budget','solvers/s5_milp.py:S5Milp.solve')
    r.eq('S5可靠性线性化','Σ_a[−ln(1−p_a)]x⁻_a+Σ_a[−ln(1−p_a)]x⁺_j,a≤−ln(1−loss_budget)','solvers/s5_milp.py:arc_lloss')
    r.eq('S5三种目标','latency:min T_max，T_max≥L_j；energy:min η_vO+E_tx；balance:min L_max，L_max≥b_τ,e/B_e','solvers/s5_milp.py:S5Milp.solve')
    r.p('关键实现限制：前段代码对源节点固定div＝1，而不是1−y_s，选源计算会与全图流量守恒矛盾，等价于排除y_s＝1。上式明确写出设计模型与代码偏差，本文未擅自修复或重新测算。balance只控制本任务新增占用比例，未包含既有利用率，也不同于S1。没有显式消环约束，路径恢复可能因额外环/分支失败；“Optimal”仅是该MILP的状态，不自动等同于原联合问题最优。')
    r.p('需要PuLP/CBC；超过80个节点直接返回None，因此现有500/528/1006节点正式场景不能直接用默认S5运行。max_tasks_per_slot＝15在当前solve中未被读取，不能描述为已生效的限流。S5仍是当前残余资源上的单任务模型，不是跨时间片、跨全任务的全局调度。S2–S5这些问题仅在本文记录，未改动仿真代码或用理论值替代实测。')
    r.h2('5.7 求解器与策略目标一致性对照')
    r.t(['求解器','latency目标/选择','balance目标/选择','energy目标/选择','本轮当前参数正式结果'],[
        ['S0','最近跳数候选首可行','同左，不按策略重排','同左','无；旧版本有档案'],
        ['S1','前段＋各后段之和＋处理','聚合后平方利用率增量','计算＋前/后段传输J','有：本报告主要结果'],
        ['S2','直接S1；精修最坏目的时延','直接S1；精修却比较时延','直接S1；精修总J','无；旧版本有档案'],
        ['S3','直接候选，失败不精修','内部u^k与max-u评价不一致','LARAC风格约束代价','无；旧版本有档案'],
        ['S4','最坏目的时延','单任务最大申请Mbps','总J','未纳入正式全规模'],
        ['S5','MILP最坏目的时延','新增最大链路比例','MILP总J','默认80节点门限不适用']])
    r.h1('6. 三种策略的计算方案与统计口径')
    r.h2('6.1 最小时延：即时代价与最终时延并非同一个量')
    r.eq('S1时延策略','w_e=d_e；J_latency=d(P⁻)+Σ_jd(P⁺_j)+t_comp(v,τ)','solvers/base.py:edge_weight；solvers/s1_dijkstra.py:rank_candidates')
    r.eq('混合实验生命周期与服务加权指标','L_life,τ=max_{服务段k}max_jL_τ,j,k；L̄_life=mean_τL_life,τ；L̄_service=Σ_{τ,k}L_τ,kΔt_τ,k/Σ_{τ,k}Δt_τ,k','experiments/run_mixed_3600.py:extra_outputs')
    r.p('单目的、单片时，上述差异较小；MC即时评分对后段求和，而QoS取最坏目的，跨片统计再取生命周期最大。因此策略名不构成“所有最终时延统计必然最小”的保证。未接纳任务无实际服务时延，不能以0 ms加入均值。tasks.csv中的delay_ms是最后一次分配的值，不是混合实验的生命周期最坏时延；正确统计见lifecycle_tasks和mixed_metrics。')
    r.p('已包含：前段传播、固定块处理、参考块每bit计算及简化利用率放大、后段传播。不包含：链路串行发送r/带宽排队、显式随机排队、jitter、FEC/重传、路由求解等待、切换中断与迁移状态传输。处理公式中的1/(1−u)只是截断放大项，不是已经实现了完整队列。')
    r.h2('6.2 负载均衡：考虑本次申请后的凸增量')
    r.eq('S1链路与算力增量','u_e=1−B_rem/B；δ_e=b_τ,e/B；u_v=1−C_rem/C；δ_v=q_τ/C','solvers/s1_dijkstra.py:projected_balance_cost')
    r.eq('S1负载均衡联合目标','J_balance=Σ_e[(u_e+δ_e)²−u_e²]+[(u_v+δ_v)²−u_v²]=Σ_eδ_e(2u_e+δ_e)+δ_v(2u_v+δ_v)','solvers/s1_dijkstra.py:projected_balance_cost')
    r.eq('balance单段搜索边权','w_e=δ_e(2u_e+δ_e)；δ_e=r_req/B_e；候选最终评分前先聚合PRE/POST重叠边','solvers/base.py:edge_weight')
    r.p('此目标全为无量纲增量，避免历史负载全0时固定跳数ε主导低速业务，以及只比较旧利用率而忽略本次大流量请求。节点/链路当前等权求和，没有隐藏地增加带宽或降低QoS。它并非直接最小化CV、Jain或最大链路利用率，各指标不会保证同时最优。带宽利用率远高于算力利用率时，链路项更主导选择。')
    r.eq('资源离散度与公平性','CV=σ(u)/mean(u)；Jain=(Σ_i u_i)²/[nΣ_i u_i²]=1/(1+CV²)（mean>0）','event_simulation.py:UtilizationMonitor；报告汇总')
    r.p('瞬时指标含空闲资源；单业务按等长时间片平均，事件实验先在每个实际事件状态计算再按区间长度积分。CV均值的函数不等于Jain均值：E[1/(1+CV²)]≠1/(1+E[CV]²)。完全空闲时约定CV＝0、Jain贡献＝0。更多低负载节点/链路加入网络也会改变CV，跨场景不能只凭CV大小判断算法优劣。')
    r.p('本次重构还进行了报告层数值复核：非负n维利用率的CV≤√(n−1)。用保存的活动分配独立重建33组事件实验的节点CV/Jain，'
        '与原值在浮点误差内一致，均未超界；原值与复核方法在附录并列。这是额外统计验收，不是发现或修复了本批算法问题，也没有重跑仿真。')
    r.h2('6.3 能耗/功耗：实际服务段积分')
    r.eq('计算动态能量与功率','E_comp=η_vO_τ；P_comp=η_vq_τ；E_comp,k=η_vq_τΔt_k','energy.py；event_simulation.py:reserve；metrics.py:close_segment')
    r.eq('传输动态能量与功率','E_tx=V_τΣ_{e∈P⁻}β_e+ρV_τΣ_jΣ_{e∈P⁺_j}β_e；P_tx=r_τΣ_preβ_e+ρr_τΣ_jΣ_post,jβ_e','energy.py:path_tx_energy_J；event_simulation.py')
    r.eq('S1能耗目标与时间积分','J_energy=E_comp+E_tx；E_window=Σ_{τ,k}(P_comp,τ,k+P_tx,τ,k)Δt_τ,k','solvers/s1_dijkstra.py；metrics.py:close_segment')
    r.eq('归一化能耗和功率峰值','e_Gbit=10⁹E_window/Σ_{τ,k}r_τΔt_τ,k；P_avg=E_window/T；P_peak=max_tΣ_{活动段}(P_comp+P_tx)','experiments/run_mixed_strategies.py:result_metrics')
    r.p('跨片任务不能每片重复计整会话能量；分段只积实际生效秒数。拒绝任务没有服务能量，掉线前消耗仍须保留。分母是实际服务的原始输入量，不是压缩后输出量；MC按输入一次计分母，但各目的副本都计传输能量。能耗策略最小化总J；同一3600 s窗口平均W与总J同比例，但峰值W不保证最低。计算峰值W与传输峰值W通常不同时发生，不能直接把两项各自峰值相加。')
    r.p('当前功率仅含业务增量项，不含整星平台、终端射频固定功率、空闲服务器、制冷、太阳翼/电池SOC或能量补给。enforce_energy_budget实际上约束计算节点瞬时W，并未设置传输无线电功率上限或累计电池J预算；将其泛称为完整卫星能量管理是不准确的。')
    r.h2('6.4 指标解释、目标权衡与模型上限')
    r.p('成功/接纳率必须给出分母；QoS达标是在已服务路径上的约束，100%已接纳QoS不等于100%全部请求成功。高压下拒绝多可能只是负载超过并发硬上限；无掉线也不代表存在足够安全裕量。若最忙链路接近100%，本模型未显式排队，因此其毫秒级时延仍不能视为实际饱和系统保证。')
    r.p('算力节点放置按任务计数与按路由段计数不同；混合业务同一任务跨片可贡献多段。源端先压缩常能减少原始高码率搬运，可能同时有利于均衡和能耗；更高地面算力不必然抵消星地链路的传播、带宽与传输能量成本。后续实验会逐项给出数据，不以“设计目的”代替实际排名。')
    r.h1('7. 代码框架、运行流程与输入输出')
    r.fig(r.architecture,'代码模块与数据流','框架',[
        '入口配置进入Simulator，拓扑/网络构造当前快照，TaskGenerator生成需求，make_solver选择S0–S5，公共校验后才由ResourceLedger扣减资源。',
        'Metrics保留任务、每次分配与逐资源事件；experiments完成汇总、审计和文档。router.py是旧接口残留，当前Simulator未调用，不应作为本轮实际目标函数来源。'])
    r.t(['文件/目录','输入','输出/职责'],[
        ['main.py','CLI、config.yaml','构建Simulator、运行、导出CSV/JSON'],
        ['models.py','节点/链路/任务参数','Node、Link、Task、Allocation、Snapshot数据结构'],
        ['topology.py','壳层、GEO、城市、时刻','节点对象、ECI/ECEF位置；不决定全部可见链路'],
        ['network.py','全部节点、场景、几何、信道','Snapshot：节点、链路、有效带宽/β、计算类型权限'],
        ['tasks.py','traffic、端点池、快照、种子','四类Task及duration_adjustments'],
        ['energy.py','Task、Node、Snapshot、路径','块处理时延、计算W/J与路径传输J'],
        ['solvers/__init__.py、base.py、s0…s5','快照、需求、策略','候选路径/计算点、公共硬约束、Allocation或None'],
        ['resources.py','成功Allocation、释放/重算事件','更新链路Mbps、节点op/s与W并发出长表事件'],
        ['simulator.py','cfg、场景/策略/算法覆盖项','传统片驱动调度；可转入事件引擎'],
        ['event_simulation.py','事件开关、输出目录、Task','真实片内到达/结束、逐片重算、服务段积分'],
        ['metrics.py','任务/路由/资源/服务结束事件','原始结果表、KPI；非算法选择模块'],
        ['experiments/','冻结配置、原始CSV/JSON','各阶段批量入口、指标、审计、图表、Word/PDF'],
        ['tests/','小图构造与保存样本','策略目标、事件积分、截止与随机输入回归检查']])
    r.h2('7.1 两种引擎、片内与跨片语义')
    r.eq('事件顺序与并发平均','到达/完成按t排序，同一时刻完成先于到达；N̄=Σ_τ service_seconds_τ/T','event_simulation.py:run_event_simulation')
    r.fig(r.timeline,'动态拓扑时间片中的业务事件与重算','框架',[
        '拓扑在片首更新，先重算已存在的存续任务，再处理片内按时序到达的新任务，任务完成即释放。新到达不追溯改变过去服务，下一片再对存续业务重算。',
        '片内几何静止不等于资源静止：到达与结束改变占用，从而影响后来任务的可用资源和处理负载项；已经建立的路由在片内不主动重优化。'])
    r.p('传统片驱动默认仅片边界记账，适合任务片首到达且恰好持续30 s的单业务压力实验。原时长混合实验开启event_driven_within_slot，才能处理真正随机到达/结束并正确计能量。主config.yaml默认scale.enabled=true、arrival_at_slot_start=true，不能只运行主入口就认为复现了全规模事件实验；正式批量入口使用冻结覆盖配置且关闭缩放。')
    r.p('事件引擎不抢占：即使主config.yaml enable_preemption=true，也不会启用资源抢占；单业务片首统一重算分支同样不走旧_schedule_task的抢占回路。旧抢占实现只缩减HC/MC带宽而未同步改业务速率/计算需求，不能在本报告当成已验证的端到端降级机制。')
    r.h2('7.2 输出CSV、主键与接口约定')
    r.t(['文件','粒度/主键','关键字段与注意事项'],[
        ['generated_tasks.csv','每生成任务1行','task_id、biz、源/目的JSON、速率、时长、op/bit、输入bit、总op、最终状态/拒绝原因；包含所有拒绝'],
        ['routing_allocations.csv','每次初始/重算/迁移1行','allocation_id、路径JSON、compute_node、带宽JSON、算力/功率、QoS、服务区间与能量'],
        ['resource_allocations.csv','每资源变化1行','event_seq/t、资源类型/ID、allocation_delta、remain_before/after；正占用、负释放'],
        ['tasks.csv','每任务摘要','最后一次路径QoS与累计任务能量；混合生命周期最坏时延要另查lifecycle'],
        ['lifecycle_tasks.csv','每任务生命周期1行','接纳、完成/掉线/在服、服务秒数、生命周期最坏QoS、累计J、重算/迁移'],
        ['slot_stats.csv','每片1行','active是片末；事件版active_mean与active_max为时间平均/峰值'],
        ['event_timeline.csv','每观察事件1行','真实时刻active、各业务active、资源均值/CV/峰值、计算功率'],
        ['duration_adjustments.csv','每生成任务1行','sampled_duration、实际duration、clipped、两种结束时刻'],
        ['effective_config.json / kpi.json','每运行1份','冻结配置及KPI；缺少冻结配置的老实验需标记复现限制'],
        ['mixed_metrics / all_metrics等','总体＋分业务/策略汇总','统计定义依阶段；全网资源指标不能无依据分摊给某一业务']])
    r.p('独立业务实验对接联合键为(batch_id,scenario,strategy,task_id)，因为不同策略也重新抽业务，同名task_id不表示同一个任务。allocation_id同样只在单次运行内唯一。JSON字段应由JSON解析器读取而非按逗号切割；CSV采用UTF-8 BOM。资源单位Mbps、op/s、W；部分旧列名仍写cycle/s，接口需按语义转换而不是再乘CPU频率。批内配对实验只有确认输入哈希一致后才可按task_id求交集。')
    r.h2('7.3 最小运行与复现实验入口')
    r.code('python main.py --scenario 1 --strategy latency --solver S1 --no-scale --out results/manual_check\npython experiments/run_single_biz.py --scenario 1 --no-scale --stress-concurrency --out results/new_single\npython experiments/run_randomized_batch.py --prepare\n# 对打印的新批次目录分别执行九组--scenario/--strategy，再--summarize\npython experiments/build_technical_scheme_report.py')
    r.p('numpy与PyYAML为核心依赖；PuLP仅S5需要；绘图和Word构建依赖matplotlib、python-docx。Windows Word/PyMuPDF用于本报告PDF渲染验收，不属于仿真运行依赖。不要直接运行旧报告生成器覆盖本次新技术方案；本报告独立命名，所有旧Word与原始实验结果保持不变。')
