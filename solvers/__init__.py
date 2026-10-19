"""Solver 注册表:name -> Solver 实例。"""
from __future__ import annotations
from solvers.base import Solver
from solvers.s0_nearest import S0Nearest
from solvers.s1_dijkstra import S1Dijkstra
from solvers.s2_ksp import S2KSP
from solvers.s3_larac import S3Larac
from solvers.s4_aco import S4Aco
from solvers.s5_milp import S5Milp

_REGISTRY = {
    "S0": S0Nearest,
    "S1": S1Dijkstra,
    "S2": S2KSP,
    "S3": S3Larac,
    "S4": S4Aco,
    "S5": S5Milp,
}


def make_solver(name: str, cfg: dict) -> Solver:
    if name not in _REGISTRY:
        raise ValueError(f"未知 solver {name};可选 {list(_REGISTRY)}")
    return _REGISTRY[name](cfg)


def all_solver_names():
    return list(_REGISTRY)
