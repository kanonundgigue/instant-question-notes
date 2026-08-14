"""気象予報モデルの合成データからPareto frontを抽出する。"""
from __future__ import annotations

from typing import TypedDict


class ForecastModel(TypedDict):
    """演習で使う予報モデルの評価値。"""

    name: str
    rmse_k: float
    runtime_min: int


# 学習用の合成値であり、実在する予報モデルの性能ではない。
MODELS: list[ForecastModel] = [
    {"name": "A: 高解像度", "rmse_k": 1.4, "runtime_min": 40},
    {"name": "B: 標準", "rmse_k": 1.8, "runtime_min": 18},
    {"name": "C: 軽量", "rmse_k": 2.3, "runtime_min": 7},
    {"name": "D: 旧版", "rmse_k": 2.1, "runtime_min": 22},
    {"name": "E: 実験版", "rmse_k": 1.6, "runtime_min": 32},
    {"name": "F: 簡易版", "rmse_k": 2.5, "runtime_min": 12},
]


def dominates(
    comparison_model: ForecastModel,
    target_model: ForecastModel,
) -> bool:
    """比較モデルが判定対象モデルを支配するときTrueを返す。"""
    no_worse = (
        comparison_model["rmse_k"] <= target_model["rmse_k"]
        and comparison_model["runtime_min"] <= target_model["runtime_min"]
    )
    strictly_better = (
        comparison_model["rmse_k"] < target_model["rmse_k"]
        or comparison_model["runtime_min"] < target_model["runtime_min"]
    )
    return no_worse and strictly_better


def find_pareto_front(models: list[ForecastModel]) -> list[ForecastModel]:
    """どのモデルにも支配されないモデルだけを返す。"""
    pareto_front: list[ForecastModel] = []
    for candidate in models:
        is_dominated = any(
            dominates(other, candidate)
            for other in models
            if other is not candidate
        )
        if not is_dominated:
            pareto_front.append(candidate)
    return pareto_front


def main() -> None:
    """Pareto frontに残るモデルと評価値を表示する。"""
    print("Pareto frontに残る予報モデル")
    for model in find_pareto_front(MODELS):
        print(
            f"{model['name']}: "
            f"RMSE={model['rmse_k']:.1f} K, "
            f"計算時間={model['runtime_min']}分"
        )


if __name__ == "__main__":
    main()
