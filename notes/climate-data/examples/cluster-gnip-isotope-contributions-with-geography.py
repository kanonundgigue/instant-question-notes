"""Step 2: GNIP同位体寄与と緯度・経度・標高から地点を分類する。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler


# ======== 設定 ========

MIN_CLUSTERS = 2
MAX_CLUSTERS = 5
CONTRIBUTION_BLOCK_WEIGHT = 1.0
GEOGRAPHY_BLOCK_WEIGHT = 0.5
GEOGRAPHY_WEIGHT_SENSITIVITY = (0.0, 0.25, 0.5, 1.0)
OVERWRITE = False
MAKE_PLOTS = True
FIGURE_DPI = 180
CLUSTER_COLORS = {
    1: "#31688e",
    2: "#d95f02",
    3: "#1b9e77",
    4: "#7570b3",
    5: "#e7298a",
}
CONTRIBUTION_STATISTICS = (
    "temperature_fraction",
    "shared_fraction",
    "r2_full",
    "cv_r2_full",
)
ANALYSIS_MODES = ("total", "calendar_anomaly")
GEOGRAPHY_COLUMNS = (
    "latitude",
    "longitude_sin",
    "longitude_cos",
    "elevation_km",
)


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_ANALYSIS_DIR = (
    _PROJECT_ROOT
    / "outputs"
    / "gnip-isotope-clustering"
    / "analysis"
    / "nasa_power"
)
_DEFAULT_STATION_FILE = (
    _PROJECT_ROOT
    / "outputs"
    / "gnip-isotope-clustering"
    / "data"
    / "gnip_monthly_nasa_power.csv"
)
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT
    / "outputs"
    / "gnip-isotope-clustering"
    / "analysis"
    / "nasa_power_with_geography"
)
_FEATURES_FILENAME = "geographic_feature_table.csv"
_ASSIGNMENTS_FILENAME = "cluster_assignments.csv"
_EVALUATION_FILENAME = "cluster_evaluation.csv"
_CONSENSUS_FILENAME = "consensus_matrices.csv"
_SENSITIVITY_FILENAME = "geography_weight_sensitivity.csv"
_HEATMAP_FILENAME = "contribution_geography_cluster_heatmap.png"
_DIAGNOSTICS_FILENAME = "cluster_number_diagnostics.png"
_MAP_FILENAME = "contribution_geography_cluster_map.png"
_SENSITIVITY_FIGURE_FILENAME = "geography_weight_sensitivity.png"


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


def configure_matplotlib() -> None:
    """記事用図のMatplotlib設定を適用する。"""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 10,
            "axes.labelsize": 11,
            "axes.titlesize": 11,
            "figure.dpi": 100,
            "savefig.dpi": FIGURE_DPI,
            "savefig.bbox": "tight",
            "axes.grid": False,
            "legend.frameon": False,
        }
    )


def contribution_feature_names() -> list[str]:
    """総変動と暦月偏差を展開した寄与特徴名を返す。"""
    return [
        f"{mode}_{statistic}"
        for mode in ANALYSIS_MODES
        for statistic in CONTRIBUTION_STATISTICS
    ]


def build_contribution_features(contributions: pd.DataFrame) -> pd.DataFrame:
    """地点・解析モード別寄与表を地点別の横持ち特徴へ変換する。"""
    required_columns = {
        "station_id",
        "analysis_mode",
        *CONTRIBUTION_STATISTICS,
    }
    missing = sorted(required_columns - set(contributions.columns))
    if missing:
        raise ValueError(f"寄与表に必要な列がありません: {missing}")
    pivot = contributions.pivot(
        index="station_id",
        columns="analysis_mode",
        values=list(CONTRIBUTION_STATISTICS),
    )
    pivot.columns = [
        f"{mode}_{statistic}" for statistic, mode in pivot.columns
    ]
    feature_names = contribution_feature_names()
    pivot = pivot.reset_index()
    finite = np.isfinite(pivot[feature_names]).all(axis=1)
    if (~finite).any():
        rejected = pivot.loc[~finite, "station_id"].tolist()
        print(f"warning: 有限の寄与特徴がそろわない地点を除外: {rejected}")
    return pivot.loc[finite, ["station_id", *feature_names]].reset_index(
        drop=True
    )


def load_station_metadata(path: Path) -> pd.DataFrame:
    """月別表から地点名・緯度・経度・標高を一地点一行で取り出す。"""
    stations = pd.read_csv(path)
    required = {
        "station_id",
        "site_name",
        "latitude",
        "longitude",
        "elevation_m",
    }
    missing = sorted(required - set(stations.columns))
    if missing:
        raise ValueError(f"地点表に必要な列がありません: {missing}")
    numeric = ["latitude", "longitude", "elevation_m"]
    stations[numeric] = stations[numeric].apply(
        pd.to_numeric,
        errors="coerce",
    )
    if stations[numeric].isna().any().any():
        raise ValueError("緯度・経度・標高に欠測があります。")
    invariant_counts = stations.groupby("station_id")[
        ["site_name", "latitude", "longitude"]
    ].nunique(dropna=False)
    if (invariant_counts > 1).any().any():
        raise ValueError("同一地点IDに複数の地点名・緯度・経度があります。")
    metadata = (
        stations.groupby("station_id", as_index=False)
        .agg(
            site_name=("site_name", "first"),
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
            elevation_m=(
                "elevation_m",
                lambda values: float(np.median(values.unique())),
            ),
            elevation_min_m=("elevation_m", "min"),
            elevation_max_m=("elevation_m", "max"),
        )
        .reset_index(drop=True)
    )
    metadata["elevation_range_m"] = (
        metadata["elevation_max_m"] - metadata["elevation_min_m"]
    )
    if not metadata["latitude"].between(-90.0, 90.0).all():
        raise ValueError("緯度が-90〜90度の範囲外です。")
    if not metadata["longitude"].between(-180.0, 180.0).all():
        raise ValueError("経度が-180〜180度の範囲外です。")
    longitude_radians = np.deg2rad(metadata["longitude"])
    metadata["longitude_sin"] = np.sin(longitude_radians)
    metadata["longitude_cos"] = np.cos(longitude_radians)
    metadata["elevation_km"] = metadata["elevation_m"] / 1_000.0
    return metadata.reset_index(drop=True)


def build_feature_table(
    contribution_features: pd.DataFrame,
    metadata: pd.DataFrame,
) -> pd.DataFrame:
    """寄与特徴と地理特徴を地点IDで一対一結合する。"""
    features = contribution_features.merge(
        metadata,
        on="station_id",
        validate="one_to_one",
    )
    if len(features) != len(contribution_features):
        raise ValueError("地理情報がそろわない寄与解析地点があります。")
    return features


def make_bootstrap_contribution_cube(
    bootstrap: pd.DataFrame,
    station_ids: Sequence[str],
) -> np.ndarray:
    """寄与ブートストラップを反復×地点×寄与特徴へ変換する。"""
    required_columns = {
        "station_id",
        "replicate",
        "analysis_mode",
        *CONTRIBUTION_STATISTICS,
    }
    missing = sorted(required_columns - set(bootstrap.columns))
    if missing:
        raise ValueError(f"ブートストラップ表に必要な列がありません: {missing}")
    feature_names = contribution_feature_names()
    records: list[np.ndarray] = []
    for replicate in sorted(bootstrap["replicate"].unique()):
        subset = bootstrap.loc[
            (bootstrap["replicate"] == replicate)
            & bootstrap["station_id"].isin(station_ids)
        ]
        pivot = subset.pivot(
            index="station_id",
            columns="analysis_mode",
            values=list(CONTRIBUTION_STATISTICS),
        )
        pivot.columns = [
            f"{mode}_{statistic}" for statistic, mode in pivot.columns
        ]
        pivot = pivot.reindex(station_ids)
        values = pivot[feature_names].to_numpy(dtype=float)
        if np.isfinite(values).all():
            records.append(values)
    if not records:
        raise ValueError("クラスタに使えるブートストラップ反復がありません。")
    return np.stack(records)


def weighted_feature_matrix(
    contribution_values: np.ndarray,
    geography_values: np.ndarray,
    geography_weight: float,
) -> np.ndarray:
    """二つの特徴ブロックを標準化し、ブロック次元と重みを調整する。"""
    if geography_weight < 0.0:
        raise ValueError("地理ブロック重みは0以上にしてください。")
    contribution_standardized = StandardScaler().fit_transform(
        contribution_values
    )
    contribution_block = (
        CONTRIBUTION_BLOCK_WEIGHT
        * contribution_standardized
        / np.sqrt(contribution_standardized.shape[1])
    )
    if geography_weight == 0.0:
        return contribution_block
    geography_standardized = StandardScaler().fit_transform(geography_values)
    geography_block = (
        geography_weight
        * geography_standardized
        / np.sqrt(geography_standardized.shape[1])
    )
    return np.column_stack([contribution_block, geography_block])


def ward_labels(features: np.ndarray, n_clusters: int) -> np.ndarray:
    """標準化・重み付き特徴へWard法を適用する。"""
    hierarchy = linkage(features, method="ward", metric="euclidean")
    return fcluster(hierarchy, t=n_clusters, criterion="maxclust").astype(int)


def consensus_cluster(
    point_contributions: np.ndarray,
    bootstrap_contributions: np.ndarray,
    geography_values: np.ndarray,
    station_ids: Sequence[str],
    geography_weight: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """寄与不確実性と固定地理特徴からWardコンセンサス分類を求める。"""
    point_matrix = weighted_feature_matrix(
        point_contributions,
        geography_values,
        geography_weight,
    )
    maximum_clusters = min(MAX_CLUSTERS, len(station_ids) // 2)
    if maximum_clusters < MIN_CLUSTERS:
        raise ValueError("コンセンサスクラスタに使える地点が少なすぎます。")
    assignments: list[dict[str, object]] = []
    evaluations: list[dict[str, object]] = []
    consensus_records: list[dict[str, object]] = []
    for n_clusters in range(MIN_CLUSTERS, maximum_clusters + 1):
        consensus = np.zeros((len(station_ids), len(station_ids)), dtype=float)
        for replicate in bootstrap_contributions:
            matrix = weighted_feature_matrix(
                replicate,
                geography_values,
                geography_weight,
            )
            labels = ward_labels(matrix, n_clusters)
            consensus += labels[:, None] == labels[None, :]
        consensus /= bootstrap_contributions.shape[0]
        consensus_profiles = StandardScaler().fit_transform(consensus)
        final_labels = ward_labels(consensus_profiles, n_clusters)
        silhouette = silhouette_score(point_matrix, final_labels)
        upper = np.triu_indices(len(station_ids), k=1)
        pac = float(
            np.mean(
                (consensus[upper] > 0.1)
                & (consensus[upper] < 0.9)
            )
        )
        confidences: list[float] = []
        for index, station_id in enumerate(station_ids):
            same_cluster = final_labels == final_labels[index]
            same_cluster[index] = False
            confidence = (
                float(consensus[index, same_cluster].mean())
                if same_cluster.any()
                else 1.0
            )
            confidences.append(confidence)
            assignments.append(
                {
                    "station_id": station_id,
                    "n_clusters": n_clusters,
                    "cluster": int(final_labels[index]),
                    "membership_confidence": confidence,
                }
            )
        evaluations.append(
            {
                "n_clusters": n_clusters,
                "n_bootstrap_used": bootstrap_contributions.shape[0],
                "silhouette": silhouette,
                "pac_0p1_0p9": pac,
                "mean_membership_confidence": float(np.mean(confidences)),
                "minimum_cluster_size": int(
                    pd.Series(final_labels).value_counts().min()
                ),
            }
        )
        rows, columns = np.triu_indices(len(station_ids))
        for row, column in zip(rows, columns):
            consensus_records.append(
                {
                    "n_clusters": n_clusters,
                    "station_id_1": station_ids[row],
                    "station_id_2": station_ids[column],
                    "coassignment_probability": consensus[row, column],
                }
            )
    evaluation = pd.DataFrame(evaluations)
    evaluation["consensus_score"] = (
        evaluation["mean_membership_confidence"]
        - evaluation["pac_0p1_0p9"]
    )
    evaluation["recommended"] = False
    best_index = evaluation.sort_values(
        ["consensus_score", "silhouette", "n_clusters"],
        ascending=[False, False, True],
    ).index[0]
    evaluation.loc[best_index, "recommended"] = True
    return (
        pd.DataFrame(assignments),
        evaluation,
        pd.DataFrame(consensus_records),
    )


def recommended_labels(
    assignments: pd.DataFrame,
    evaluation: pd.DataFrame,
) -> pd.DataFrame:
    """推奨Kの地点別ラベルだけを返す。"""
    selected = evaluation.loc[evaluation["recommended"].astype(bool)]
    if len(selected) != 1:
        raise ValueError("推奨Kが一意ではありません。")
    recommended_k = int(selected["n_clusters"].iloc[0])
    return assignments.loc[
        assignments["n_clusters"] == recommended_k,
        ["station_id", "cluster", "membership_confidence"],
    ].copy()


def evaluate_geography_weights(
    point_contributions: np.ndarray,
    bootstrap_contributions: np.ndarray,
    geography_values: np.ndarray,
    station_ids: Sequence[str],
) -> pd.DataFrame:
    """地理ブロック重みごとに推奨Kと寄与のみ分類からのARIを比較する。"""
    records: list[dict[str, object]] = []
    baseline_labels: pd.DataFrame | None = None
    for weight in GEOGRAPHY_WEIGHT_SENSITIVITY:
        assignments, evaluation, _ = consensus_cluster(
            point_contributions,
            bootstrap_contributions,
            geography_values,
            station_ids,
            weight,
        )
        labels = recommended_labels(assignments, evaluation)
        if baseline_labels is None:
            baseline_labels = labels[["station_id", "cluster"]].rename(
                columns={"cluster": "baseline_cluster"}
            )
        comparison = baseline_labels.merge(
            labels[["station_id", "cluster"]],
            on="station_id",
            validate="one_to_one",
        )
        recommended = evaluation.loc[evaluation["recommended"].astype(bool)].iloc[
            0
        ]
        records.append(
            {
                "geography_weight": weight,
                "recommended_k": int(recommended["n_clusters"]),
                "adjusted_rand_index_vs_contribution_only": (
                    adjusted_rand_score(
                        comparison["baseline_cluster"],
                        comparison["cluster"],
                    )
                ),
                "silhouette": float(recommended["silhouette"]),
                "mean_membership_confidence": float(
                    recommended["mean_membership_confidence"]
                ),
                "pac_0p1_0p9": float(recommended["pac_0p1_0p9"]),
                "minimum_cluster_size": int(recommended["minimum_cluster_size"]),
            }
        )
    return pd.DataFrame(records)


def compact_station_labels(
    station_ids: Sequence[str],
    station_labels: dict[str, str],
    maximum_length: int = 24,
) -> list[str]:
    """図中で識別できる範囲に地点名を短縮する。"""
    labels: list[str] = []
    for station_id in station_ids:
        label = station_labels.get(station_id, station_id)
        label = label.split("(", 1)[0].strip()
        if len(label) > maximum_length:
            label = f"{label[: maximum_length - 1].rstrip()}…"
        labels.append(label)
    return labels


def save_heatmap(
    features: pd.DataFrame,
    assignments: pd.DataFrame,
    evaluation: pd.DataFrame,
    output_path: Path,
) -> None:
    """推奨分類順に寄与特徴と地理特徴の標準化値を描く。"""
    selected = recommended_labels(assignments, evaluation)
    ordered = features.merge(
        selected[["station_id", "cluster"]],
        on="station_id",
        validate="one_to_one",
    ).sort_values(["cluster", "station_id"])
    feature_columns = [*contribution_feature_names(), *GEOGRAPHY_COLUMNS]
    standardized = StandardScaler().fit_transform(ordered[feature_columns])
    labels = compact_station_labels(
        ordered["station_id"].tolist(),
        dict(
            ordered[["station_id", "site_name"]].itertuples(
                index=False,
                name=None,
            )
        ),
    )
    fig, ax = plt.subplots(
        figsize=(12.0, max(5.5, 0.27 * len(ordered))),
        constrained_layout=True,
    )
    image = ax.imshow(
        standardized,
        aspect="auto",
        cmap="RdBu_r",
        vmin=-2.5,
        vmax=2.5,
    )
    ax.set_xticks(np.arange(len(feature_columns)))
    ax.set_xticklabels(feature_columns, rotation=42, ha="right")
    ax.set_yticks(np.arange(len(ordered)))
    ax.set_yticklabels(
        [
            f"C{cluster} {label}"
            for cluster, label in zip(ordered["cluster"], labels)
        ]
    )
    ax.axvline(len(contribution_feature_names()) - 0.5, color="black", lw=1.0)
    ax.set_title(
        "(a) Contribution and geography features in the selected clustering",
        loc="left",
        fontweight="bold",
    )
    fig.colorbar(
        image,
        ax=ax,
        label="Standardized feature",
        fraction=0.03,
        pad=0.02,
    )
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")


def save_diagnostics(
    evaluation: pd.DataFrame,
    output_path: Path,
) -> None:
    """候補クラスタ数ごとの分離度と安定性を描く。"""
    panels = (
        ("silhouette", "(a) Silhouette coefficient", True),
        (
            "mean_membership_confidence",
            "(b) Mean membership confidence",
            True,
        ),
        ("pac_0p1_0p9", "(c) Ambiguous-pair fraction (PAC)", False),
    )
    recommended = evaluation["recommended"].astype(bool)
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(11.0, 3.6),
        constrained_layout=True,
    )
    for ax, (column, title, higher_is_better) in zip(axes, panels):
        ax.plot(
            evaluation["n_clusters"],
            evaluation[column],
            color="0.45",
            marker="o",
            linewidth=1.3,
        )
        ax.scatter(
            evaluation.loc[recommended, "n_clusters"],
            evaluation.loc[recommended, column],
            s=90,
            facecolors="none",
            edgecolors="#c44e52",
            linewidth=2.0,
            zorder=3,
        )
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_xlabel("Number of clusters (K)")
        ax.set_ylabel("Higher is better" if higher_is_better else "Lower is better")
        ax.set_xticks(evaluation["n_clusters"])
        ax.grid(axis="y", alpha=0.25)
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")


def save_map(
    features: pd.DataFrame,
    assignments: pd.DataFrame,
    evaluation: pd.DataFrame,
    output_path: Path,
) -> None:
    """推奨分類を北米地図へ描く。"""
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
    except ImportError as error:
        raise RuntimeError("地図の描画にはcartopyが必要です。") from error
    selected = recommended_labels(assignments, evaluation)
    locations = features.merge(
        selected[["station_id", "cluster"]],
        on="station_id",
        validate="one_to_one",
    ).sort_values(["cluster", "station_id"])
    data_crs = ccrs.PlateCarree()
    projection = ccrs.LambertConformal(
        central_longitude=-100,
        central_latitude=45,
    )
    fig = plt.figure(figsize=(11.0, 6.2), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=projection)
    ax.set_extent([-130, -55, 20, 75], crs=data_crs)
    ax.add_feature(cfeature.LAND, facecolor="#f2f2f2", zorder=0)
    ax.add_feature(cfeature.OCEAN, facecolor="#eaf2f8", zorder=0)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.55, zorder=1)
    ax.add_feature(cfeature.LAKES, facecolor="#eaf2f8", alpha=0.8, zorder=1)
    ax.gridlines(
        linewidth=0.35,
        color="0.45",
        alpha=0.45,
        linestyle=":",
        zorder=1,
    )
    for cluster, group in locations.groupby("cluster", sort=True):
        ax.scatter(
            group["longitude"],
            group["latitude"],
            transform=data_crs,
            s=70,
            color=CLUSTER_COLORS[int(cluster)],
            edgecolor="white",
            linewidth=0.8,
            label=f"Cluster {int(cluster)} (n={len(group)})",
            zorder=3,
        )
    ax.set_title(
        "(a) GNIP clusters from isotope contributions and geography",
        loc="left",
        fontweight="bold",
    )
    ax.legend(loc="lower left", title="Consensus cluster")
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")


def save_sensitivity_figure(
    sensitivity: pd.DataFrame,
    output_path: Path,
) -> None:
    """地理重みと寄与のみ分類からの一致度を描く。"""
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(9.0, 3.8),
        constrained_layout=True,
    )
    axes[0].plot(
        sensitivity["geography_weight"],
        sensitivity["adjusted_rand_index_vs_contribution_only"],
        color="#31688e",
        marker="o",
        linewidth=1.5,
    )
    axes[0].set_title(
        "(a) Agreement with contribution-only clustering",
        loc="left",
        fontweight="bold",
    )
    axes[0].set_xlabel("Geography block weight")
    axes[0].set_ylabel("Adjusted Rand index")
    axes[0].set_ylim(-0.05, 1.05)
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].plot(
        sensitivity["geography_weight"],
        sensitivity["mean_membership_confidence"],
        color="#d95f02",
        marker="o",
        linewidth=1.5,
        label="Membership confidence",
    )
    axes[1].plot(
        sensitivity["geography_weight"],
        1.0 - sensitivity["pac_0p1_0p9"],
        color="0.45",
        marker="s",
        linewidth=1.2,
        label="1 − PAC",
    )
    axes[1].set_title(
        "(b) Stability diagnostics",
        loc="left",
        fontweight="bold",
    )
    axes[1].set_xlabel("Geography block weight")
    axes[1].set_ylabel("Higher is better")
    axes[1].set_ylim(-0.05, 1.05)
    axes[1].grid(axis="y", alpha=0.25)
    axes[1].legend(loc="best")
    fig.savefig(output_path)
    plt.close(fig)
    print(f"Saved: {output_path}")


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "GNIP同位体寄与のブートストラップ結果に緯度・経度・標高を加え、"
            "Wardコンセンサスクラスタと地理重み感度を計算する"
        )
    )
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=_DEFAULT_ANALYSIS_DIR,
        help="寄与解析のstation_contributions.csvなどを含むディレクトリ",
    )
    parser.add_argument(
        "--station-file",
        type=Path,
        default=_DEFAULT_STATION_FILE,
        help="地点名・緯度・経度・標高を含む月別CSV",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="地理特徴付きクラスタ表と図の出力先",
    )
    parser.add_argument(
        "--geography-weight",
        type=float,
        default=GEOGRAPHY_BLOCK_WEIGHT,
        help="主解析における地理特徴ブロックの重み",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="PNG図を作成しない",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """地理特徴付きGNIP同位体寄与クラスタリングを実行する。"""
    args = parse_args()
    if args.geography_weight < 0.0:
        raise ValueError("--geography-weightは0以上にしてください。")
    contribution_path = args.analysis_dir / "station_contributions.csv"
    bootstrap_path = args.analysis_dir / "station_contribution_bootstrap.csv"
    require_files(
        [contribution_path, bootstrap_path, args.station_file],
        "地理特徴付きクラスタリング入力",
    )
    output_paths = [
        args.output_dir / _FEATURES_FILENAME,
        args.output_dir / _ASSIGNMENTS_FILENAME,
        args.output_dir / _EVALUATION_FILENAME,
        args.output_dir / _CONSENSUS_FILENAME,
        args.output_dir / _SENSITIVITY_FILENAME,
    ]
    make_plots = MAKE_PLOTS and not args.no_plots
    if make_plots:
        output_paths.extend(
            [
                args.output_dir / _HEATMAP_FILENAME,
                args.output_dir / _DIAGNOSTICS_FILENAME,
                args.output_dir / _MAP_FILENAME,
                args.output_dir / _SENSITIVITY_FIGURE_FILENAME,
            ]
        )

    def action() -> None:
        contributions = pd.read_csv(contribution_path)
        bootstrap = pd.read_csv(bootstrap_path)
        metadata = load_station_metadata(args.station_file)
        contribution_features = build_contribution_features(contributions)
        features = build_feature_table(contribution_features, metadata)
        station_ids = features["station_id"].tolist()
        contribution_names = contribution_feature_names()
        point_contributions = features[contribution_names].to_numpy(dtype=float)
        geography_values = features[list(GEOGRAPHY_COLUMNS)].to_numpy(
            dtype=float
        )
        bootstrap_cube = make_bootstrap_contribution_cube(
            bootstrap,
            station_ids,
        )
        assignments, evaluation, consensus = consensus_cluster(
            point_contributions,
            bootstrap_cube,
            geography_values,
            station_ids,
            args.geography_weight,
        )
        sensitivity = evaluate_geography_weights(
            point_contributions,
            bootstrap_cube,
            geography_values,
            station_ids,
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        features.to_csv(output_paths[0], index=False, float_format="%.8f")
        assignments.to_csv(output_paths[1], index=False, float_format="%.8f")
        evaluation.to_csv(output_paths[2], index=False, float_format="%.8f")
        consensus.to_csv(output_paths[3], index=False, float_format="%.8f")
        sensitivity.to_csv(output_paths[4], index=False, float_format="%.8f")
        if make_plots:
            configure_matplotlib()
            save_heatmap(features, assignments, evaluation, output_paths[5])
            save_diagnostics(evaluation, output_paths[6])
            save_map(features, assignments, evaluation, output_paths[7])
            save_sensitivity_figure(sensitivity, output_paths[8])
        recommended = evaluation.loc[evaluation["recommended"].astype(bool)]
        print(
            f"解析地点数={len(features)}, "
            f"年ブートストラップ={bootstrap_cube.shape[0]}, "
            f"地理重み={args.geography_weight:.2f}, "
            f"推奨K={int(recommended['n_clusters'].iloc[0])}"
        )

    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "地理特徴付きGNIP同位体寄与クラスタリング",
        action,
    )


if __name__ == "__main__":
    main()
