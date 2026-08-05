"""Step 1: 古気候の線形逆問題と時間相関誤差を最小例で確認する。"""
from __future__ import annotations

from math import sqrt

# ======== 設定 ========

PROXY_VALUE = 0.8
PROXY_ERROR_VARIANCE = 0.25
SPATIAL_CORRELATION = 0.60

N_YEARS = 3
ANNUAL_ERROR_STD = 0.50
RED_NOISE_RHO = 0.80


def update_two_region_field(
    proxy_value: float,
    proxy_error_variance: float,
    spatial_correlation: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """1地点のプロキシを使い、2地域の気温偏差を線形更新する。

    Parameters
    ----------
    proxy_value
        西地域で観測された合成プロキシ気温偏差。
    proxy_error_variance
        合成プロキシの誤差分散。
    spatial_correlation
        西地域と東地域の事前気温偏差の相関。

    Returns
    -------
    analysis
        更新後の西地域・東地域の気温偏差。
    gain
        西地域・東地域に対するKalman gain。
    posterior_std
        更新後の西地域・東地域の標準偏差。
    """
    if proxy_error_variance <= 0:
        raise ValueError("proxy_error_variance は正にしてください。")
    if not -1 < spatial_correlation < 1:
        raise ValueError("spatial_correlation は -1 より大きく 1 未満にしてください。")

    # 事前平均は両地域とも0、事前分散は両地域とも1とする。
    denominator = 1.0 + proxy_error_variance
    gain_west = 1.0 / denominator
    gain_east = spatial_correlation / denominator

    analysis_west = gain_west * proxy_value
    analysis_east = gain_east * proxy_value

    # B_a = B - K H B の対角成分を、この2地域の設定で直接計算する。
    variance_west = 1.0 - gain_west
    variance_east = 1.0 - gain_east * spatial_correlation

    analysis = (analysis_west, analysis_east)
    gain = (gain_west, gain_east)
    posterior_std = (sqrt(variance_west), sqrt(variance_east))
    return analysis, gain, posterior_std


def correlated_mean_standard_error(
    n_years: int,
    annual_error_std: float,
    rho: float,
) -> tuple[float, float]:
    """AR(1)相関を持つ年誤差について、期間平均の標準誤差を計算する。

    Parameters
    ----------
    n_years
        平均する年代数。
    annual_error_std
        各年代の誤差の周辺標準偏差。
    rho
        隣接年代間のAR(1)相関係数。

    Returns
    -------
    standard_error
        期間平均の標準誤差。
    effective_sample_size
        同じ標準誤差を与える独立観測数。
    """
    if n_years < 1:
        raise ValueError("n_years は1以上にしてください。")
    if annual_error_std <= 0:
        raise ValueError("annual_error_std は正にしてください。")
    if not -1 < rho < 1:
        raise ValueError("rho は -1 より大きく 1 未満にしてください。")

    correlation_sum = float(n_years)
    for lag in range(1, n_years):
        correlation_sum += 2.0 * (n_years - lag) * rho**lag

    variance = annual_error_std**2 * correlation_sum / n_years**2
    standard_error = sqrt(variance)
    effective_sample_size = annual_error_std**2 / variance
    return standard_error, effective_sample_size


def main() -> None:
    """既定設定で2つの演習結果を表示する。"""
    analysis, gain, posterior_std = update_two_region_field(
        proxy_value=PROXY_VALUE,
        proxy_error_variance=PROXY_ERROR_VARIANCE,
        spatial_correlation=SPATIAL_CORRELATION,
    )
    white_se, white_n_eff = correlated_mean_standard_error(
        n_years=N_YEARS,
        annual_error_std=ANNUAL_ERROR_STD,
        rho=0.0,
    )
    red_se, red_n_eff = correlated_mean_standard_error(
        n_years=N_YEARS,
        annual_error_std=ANNUAL_ERROR_STD,
        rho=RED_NOISE_RHO,
    )

    print("=== 演習1: 1地点の年輪から2地域の気温場を更新 ===")
    print(f"Kalman gain          : 西={gain[0]:.3f}, 東={gain[1]:.3f}")
    print(f"更新後の気温偏差    : 西={analysis[0]:.3f}, 東={analysis[1]:.3f} °C")
    print(f"更新後の標準偏差    : 西={posterior_std[0]:.3f}, 東={posterior_std[1]:.3f} °C")
    print()
    print("=== 演習2: 3年代平均の誤差 ===")
    print(f"白色誤差 rho=0.0    : 標準誤差={white_se:.3f} °C, 実効n={white_n_eff:.2f}")
    print(f"赤色誤差 rho={RED_NOISE_RHO:.1f}    : 標準誤差={red_se:.3f} °C, 実効n={red_n_eff:.2f}")


if __name__ == "__main__":
    main()
