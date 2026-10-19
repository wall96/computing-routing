"""M3 任务生成器：4 类业务到达过程。"""
from __future__ import annotations
import random
from typing import List, Dict
from models import Task, BizType


def _sample_in_range(rng: random.Random, lo, hi) -> float:
    if isinstance(lo, list):
        return rng.uniform(lo[0], lo[1])
    return rng.uniform(lo, hi)


def _rate_mbps_for_biz(rng: random.Random, biz_cfg: dict) -> float:
    """从配置里取速率范围，不论 mbps 还是 kbps，统一返回 mbps。"""
    if "rate_mbps" in biz_cfg:
        lo, hi = biz_cfg["rate_mbps"]
        return rng.uniform(lo, hi)
    if "rate_kbps" in biz_cfg:
        lo, hi = biz_cfg["rate_kbps"]
        return rng.uniform(lo, hi) / 1000.0
    raise ValueError("biz config 缺少 rate_mbps / rate_kbps")


def _duration_s(rng: random.Random, biz_cfg: dict) -> float:
    lo, hi = biz_cfg["duration_s"]
    return rng.uniform(lo, hi)


def _pick_distinct(rng: random.Random, pool: List[str], k: int, excludes: List[str]) -> List[str]:
    cand = [x for x in pool if x not in excludes]
    if k >= len(cand):
        return list(cand)
    return rng.sample(cand, k)


class TaskGenerator:
    def __init__(self, traffic_cfg: dict, access_node_ids: List[str], seed: int = 42):
        self.cfg = traffic_cfg
        self.access = list(access_node_ids)
        self.rng = random.Random(seed)
        self._counter = 0

    def _new_id(self, biz: str) -> str:
        self._counter += 1
        return f"T-{biz}-{self._counter:06d}"

    def gen_for_slot(self, t_now: float, slot_dur: float) -> List[Task]:
        """生成本时间片的新到达任务。"""
        out: List[Task] = []
        rates = self.cfg.get("arrival_rate_per_slot", {})
        qos_cfg: Dict[str, dict] = self.cfg["qos"]
        for biz_str, lam in rates.items():
            n = self._poisson(lam)
            for _ in range(n):
                biz_cfg = qos_cfg[biz_str]
                src = self.rng.choice(self.access)
                if biz_str == "MC":
                    k_dst = biz_cfg.get("k_dst", 3)
                    dst_set = _pick_distinct(self.rng, self.access, k_dst, [src])
                    if not dst_set:
                        continue
                else:
                    # 单连接：结果回到自己
                    dst_set = [src]

                rate_mbps = _rate_mbps_for_biz(self.rng, biz_cfg)
                duration = _duration_s(self.rng, biz_cfg)
                # 突发任务在时间片内随机时刻到达；常驻任务在时间片起点到达
                if biz_cfg["mode"] == "burst":
                    arrival = t_now + self.rng.uniform(0, slot_dur)
                else:
                    arrival = t_now

                # 衍生
                cycles_per_bit = int(biz_cfg["cycles_per_bit"])
                # 业务总数据量（bits） = 速率 × 持续时长
                data_bits = rate_mbps * 1e6 * duration
                cycles_total = int(data_bits * cycles_per_bit)
                # 持续算力需求（TFLOPS）：每秒 cycles 个 ≈ rate_bps × cycles_per_bit
                # 这里用一种粗略换算：1 TFLOPS ≈ 1 TFLO/s；CPU 周期不是 FLOP，但近似按 1:1 画像。
                compute_rate_tflops = rate_mbps * 1e6 * cycles_per_bit / 1e12

                # HR 强制 ρ=1（不压缩，保证准确性）；其余按配置
                rho = float(biz_cfg.get("compression_rho", 1.0))
                if biz_str == "HR":
                    rho = 1.0
                rho = max(min(rho, 1.0), 1e-3)

                t = Task(
                    id=self._new_id(biz_str),
                    src=src,
                    dst_set=dst_set,
                    biz=BizType(biz_str),
                    priority=int(biz_cfg["priority"]),
                    mode=biz_cfg["mode"],
                    rate_mbps=rate_mbps,
                    delay_budget_ms=float(biz_cfg["delay_ms"]),
                    loss_budget=float(biz_cfg["loss"]),
                    cycles_per_bit=cycles_per_bit,
                    arrival_t=arrival,
                    duration_s=duration,
                    cycles_total=cycles_total,
                    data_volume_bits=data_bits,
                    compute_rate_tflops=compute_rate_tflops,
                    compression_rho=rho,
                )
                out.append(t)
        # 按到达时间排序
        out.sort(key=lambda x: (x.priority, x.arrival_t))
        return out

    def _poisson(self, lam: float) -> int:
        # 简单 Knuth 算法
        L = pow(2.718281828, -lam)
        k = 0
        p = 1.0
        while p > L:
            k += 1
            p *= self.rng.random()
        return k - 1
