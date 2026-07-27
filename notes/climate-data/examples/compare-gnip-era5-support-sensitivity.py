"""Step 5: ERA5空間支持の違いによる寄与推定・クラスタ感度を比較する。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score


# ======== 設定 ========

OVERWRITE = False
FIGURE_DPI = 180
VARIANTS = (
    "nearest",
    "temperature_three_by_three",
    "precipitation_three_by_three",
    "three_by_three",
    "lapse_adjusted",
)


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_ANALYSIS_DIR = (
    _PROJECT_ROOT / "outputs" / "gnip-isotope-clustering" / "analysis"
)
_DEFAULT_DIAGNOSTICS_FILE = (
    _PROJECT_ROOT
    / "outputs"
    / "gnip-isotope-clustering"
    / "data"
    / "era5_gnip_support_diagnostics.csv"
)
_DEFAULT_OUTPUT_DIR = _DEFAULT_ANALYSIS_DIR / "support_comparison"
_SUMMARY_FILENAME = "support_sensitivity_summary.csv"
_STATION_FILENAME = "support_sensitivity_by_station.csv"
_FIGURE_FILENAME = "support_sensitivity.png"


def require_files(paths: Sequence[Path], label: str) -> None:
    """必要な入力ファイルが存在するか確認する。"""
    missing = [path for path in paths if not path.is_file()]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"{label}が見つかりません: {joined}")


def run_or_skip(
    output_paths: Sequence[Path],
    overwrite: bool,
    label: str,
    action: Callable[[], None],
) -> None:
    """出力一式があれば省略し、部分出力は上書きせず停止する。"""
    existing = [path.exists() for path in output_paths]
    if all(existing) and not overwrite:
        print(f"skip: {label}（出力一式が既に存在します）")
        return
    if any(existing) and not overwrite:
        present = ", ".join(
            str(path) for path, exists in zip(output_paths, existing) if exists
        )
        raise FileExistsError(
            "出力が一部だけ存在します。--overwriteを使うか"
            f"別の出力先を指定してください: {present}"
        )
    action()
    for path in output_paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"{label}の出力が作成されませんでした: {path}")


def recommended_assignments(variant_dir: Path) -> pd.DataFrame:
    """各解析の推奨Kに対応する地点別クラスタを返す。"""
    evaluation_path = variant_dir / "cluster_evaluation.csv"
    assignment_path = variant_dir / "cluster_assignments.csv"
    require_files(
        [evaluation_path, assignment_path],
        f"{variant_dir.name}のクラスタ出力",
    )
    evaluation = pd.read_csv(evaluation_path)
    recommended = evaluation.loc[evaluation["recommended"].astype(bool)]
    if len(recommended) != 1:
        raise ValueError(f"{variant_dir}: 推奨Kが一意ではありません。")
    n_clusters = int(recommended["n_clusters"].iloc[0])
    assignments = pd.read_csv(assignment_path)
    selected = assignments.loc[
        assignments["n_clusters"] == n_clusters,
        ["station_id", "cluster"],
    ].copy()
    selected["recommended_k"] = n_clusters
    return selected


def contribution_features(variant_dir: Path, variant: str) -> pd.DataFrame:
    """地点別の総変動・暦月偏差寄与率を横持ちへ変換する。"""
    path = variant_dir / "station_contributions.csv"
    require_files([path], f"{variant}の寄与出力")
    contributions = pd.read_csv(path)
    pivot = contributions.pivot(
        index=["station_id", "latitude", "longitude"],
        columns="analysis_mode",
        values=["temperature_fraction", "precipitation_fraction", "r2_full"],
    )
    pivot.columns = [
        f"{variant}_{mode}_{statistic}"
        for statistic, mode in pivot.columns
    ]
    return pivot.reset_index()


def compare_supports(
    analysis_dir: Path,
    diagnostics_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """5種類の支持設定を地点別・全体指標で比較する。"""
    feature_tables: list[pd.DataFrame] = []
    assignment_tables: dict[str, pd.DataFrame] = {}
    for variant in VARIANTS:
        variant_dir = analysis_dir / variant
        assignment_tables[variant] = recommended_assignments(variant_dir)
        feature_tables.append(contribution_features(variant_dir, variant))

    station_table = feature_tables[0]
    for table in feature_tables[1:]:
        station_table = station_table.merge(
            table,
            on=["station_id", "latitude", "longitude"],
            validate="one_to_one",
        )
    for variant, assignments in assignment_tables.items():
        station_table = station_table.merge(
            assignments.rename(
                columns={
                    "cluster": f"{variant}_cluster",
                    "recommended_k": f"{variant}_recommended_k",
                }
            ),
            on="station_id",
            validate="one_to_one",
        )
    require_files([diagnostics_path], "ERA5とGNIPの代表性診断")
    diagnostics = pd.read_csv(diagnostics_path)
    warning_columns = [
        column for column in diagnostics if column.endswith("_warning")
    ]
    if warning_columns:
        for column in warning_columns:
            if diagnostics[column].dtype == object:
                diagnostics[column] = (
                    diagnostics[column].astype(str).str.lower().eq("true")
                )
        diagnostics["any_support_warning"] = diagnostics[
            warning_columns
        ].fillna(False).astype(bool).any(axis=1)
    station_table = station_table.merge(
        diagnostics,
        on="station_id",
        validate="one_to_one",
    )

    summary_records: list[dict[str, object]] = []
    primary = assignment_tables["nearest"][["station_id", "cluster"]]
    for variant in VARIANTS[1:]:
        comparison = primary.merge(
            assignment_tables[variant][["station_id", "cluster"]],
            on="station_id",
            suffixes=("_nearest", f"_{variant}"),
            validate="one_to_one",
        )
        for mode in ("total", "calendar_anomaly"):
            temperature_difference = (
                station_table[
                    f"{variant}_{mode}_temperature_fraction"
                ]
                - station_table[
                    f"nearest_{mode}_temperature_fraction"
                ]
            )
            precipitation_difference = (
                station_table[
                    f"{variant}_{mode}_precipitation_fraction"
                ]
                - station_table[
                    f"nearest_{mode}_precipitation_fraction"
                ]
            )
            summary_records.append(
                {
                    "comparison": f"nearest_vs_{variant}",
                    "analysis_mode": mode,
                    "n_stations": len(comparison),
                    "adjusted_rand_index": adjusted_rand_score(
                        comparison["cluster_nearest"],
                        comparison[f"cluster_{variant}"],
                    ),
                    "mean_abs_temperature_fraction_difference": float(
                        temperature_difference.abs().mean()
                    ),
                    "max_abs_temperature_fraction_difference": float(
                        temperature_difference.abs().max()
                    ),
                    "mean_abs_precipitation_fraction_difference": float(
                        precipitation_difference.abs().mean()
                    ),
                    "max_abs_precipitation_fraction_difference": float(
                        precipitation_difference.abs().max()
                    ),
                    "n_any_support_warning": int(
                        station_table["any_support_warning"].sum()
                    )
                    if "any_support_warning" in station_table
                    else 0,
                    "mean_abs_temperature_fraction_difference_warned": float(
                        temperature_difference.loc[
                            station_table["any_support_warning"]
                        ].abs().mean()
                    )
                    if "any_support_warning" in station_table
                    and station_table["any_support_warning"].any()
                    else np.nan,
                }
            )
    return station_table, pd.DataFrame(summary_records)


def save_figure(stations: pd.DataFrame, output_path: Path) -> None:
    """支持変更による地点別気温寄与率の変化を図示する。"""
    fig, axes = plt.subplots(
        2,
        2,
        figsize=(12.0, 9.0),
        constrained_layout=True,
    )
    panels = (
        (
            "temperature_three_by_three",
            "total",
            "T 3×3 minus nearest: total",
        ),
        (
            "precipitation_three_by_three",
            "total",
            "P 3×3 minus nearest: total",
        ),
        (
            "three_by_three",
            "calendar_anomaly",
            "T+P 3×3 minus nearest: calendar anomaly",
        ),
        ("lapse_adjusted", "total", "lapse minus nearest: total"),
        (
            "lapse_adjusted",
            "calendar_anomaly",
            "lapse minus nearest: calendar anomaly",
        ),
    )
    maximum = 0.0
    differences: list[pd.Series] = []
    for variant, mode, _ in panels:
        difference = (
            stations[f"{variant}_{mode}_temperature_fraction"]
            - stations[f"nearest_{mode}_temperature_fraction"]
        )
        differences.append(difference)
        maximum = max(maximum, float(difference.abs().max()))
    maximum = max(maximum, 0.05)
    for ax, (variant, mode, title), difference in zip(
        axes.flat,
        panels,
        differences,
    ):
        scatter = ax.scatter(
            stations["longitude"],
            stations["latitude"],
            c=difference,
            cmap="RdBu_r",
            vmin=-maximum,
            vmax=maximum,
            s=48,
            edgecolor="black",
            linewidth=0.4,
        )
        ax.axvline(-140.0, color="#777777", linewidth=0.5, linestyle=":")
        ax.set_title(title)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.grid(alpha=0.2)
    fig.colorbar(
        scatter,
        ax=axes,
        label="Change in temperature contribution fraction",
        shrink=0.85,
    )
    fig.savefig(output_path, dpi=FIGURE_DPI)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description="ERA5最近傍・3×3平均・標高補正の寄与クラスタ感度を比較する"
    )
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=_DEFAULT_ANALYSIS_DIR,
        help="3種類の解析ディレクトリを含む親ディレクトリ",
    )
    parser.add_argument(
        "--diagnostics-file",
        type=Path,
        default=_DEFAULT_DIAGNOSTICS_FILE,
        help="ERA5とGNIPの代表性診断CSV",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="比較表と図の出力先",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """空間支持の感度比較を実行する。"""
    args = parse_args()
    output_paths = [
        args.output_dir / _SUMMARY_FILENAME,
        args.output_dir / _STATION_FILENAME,
        args.output_dir / _FIGURE_FILENAME,
    ]

    def action() -> None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stations, summary = compare_supports(
            args.analysis_dir,
            args.diagnostics_file,
        )
        summary.to_csv(output_paths[0], index=False, float_format="%.8f")
        stations.to_csv(output_paths[1], index=False, float_format="%.8f")
        save_figure(stations, output_paths[2])
        print(
            f"比較地点数={len(stations)}, "
            f"比較ケース数={len(summary)}"
        )

    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "ERA5空間支持の感度比較",
        action,
    )


if __name__ == "__main__":
    main()
