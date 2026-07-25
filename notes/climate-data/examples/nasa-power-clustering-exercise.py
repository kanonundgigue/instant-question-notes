"""NASA POWER年月別データによる10地点クラスタリングの段階演習。"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler


# ======== 設定 ========

N_CLUSTERS = 2
N_COMPONENTS = 2
N_INIT = 50
RANDOM_STATE = 42
START_YEAR = 1991
END_YEAR = 2020
OVERWRITE = False
MAKE_PLOTS = True
FIGURE_DPI = 180


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_INPUT = (
    _SCRIPT_DIR.parent
    / "data"
    / "nasa-power-10-locations-monthly-1991-2020.csv"
)
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "nasa-power-clustering"


@dataclass(frozen=True)
class ClusterResult:
    """クラスタリング結果と途中計算をまとめる。"""

    scaled: np.ndarray
    scores: np.ndarray
    pca: PCA
    labels: np.ndarray
    silhouette: float
    ari: float


def require_files(paths: list[Path], label: str) -> None:
    """必要な入力ファイルが存在するか確認する。"""
    missing = [path for path in paths if not path.exists()]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"{label}が見つかりません: {joined}")


def run_or_skip(
    output_path: Path,
    overwrite: bool,
    label: str,
    action: Callable[[], None],
) -> None:
    """出力済みならスキップし、それ以外は処理を実行する。"""
    if output_path.exists() and not overwrite:
        print(f"skip: {label} ({output_path})")
        return
    action()
    print(f"saved: {label} ({output_path})")


def configure_matplotlib() -> None:
    """教材図で共通して使うMatplotlib設定を適用する。"""
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": FIGURE_DPI,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "legend.frameon": False,
        }
    )


def load_monthly_data(input_path: Path) -> pd.DataFrame:
    """年月別CSVを読み、期間、重複、欠測を検査する。"""
    require_files([input_path], "演習用CSV")
    data = pd.read_csv(input_path, parse_dates=["date"])
    required_columns = {
        "station",
        "known_group",
        "date",
        "temp_c",
        "precip_mm",
    }
    missing_columns = required_columns.difference(data.columns)
    if missing_columns:
        joined = ", ".join(sorted(missing_columns))
        raise ValueError(f"CSVに必要な列がありません: {joined}")
    if data.duplicated(["station", "date"]).any():
        raise ValueError("同じ地点・年月の行が重複しています。")
    if data[["temp_c", "precip_mm"]].isna().any().any():
        raise ValueError("気温または降水量に欠測があります。")

    coverage = data.groupby("station")["date"].agg(["min", "max", "nunique"])
    expected_months = (END_YEAR - START_YEAR + 1) * 12
    if not (coverage["nunique"] == expected_months).all():
        raise ValueError("360か月そろっていない地点があります。")
    if coverage["min"].nunique() != 1 or coverage["max"].nunique() != 1:
        raise ValueError("地点間で解析期間が一致していません。")
    return data.sort_values(["station", "date"]).reset_index(drop=True)


def make_climatology(data: pd.DataFrame) -> pd.DataFrame:
    """年月別データから地点・暦月ごとの30年平均を作る。"""
    monthly_data = data.copy()
    monthly_data["month"] = monthly_data["date"].dt.month
    return (
        monthly_data.groupby(
            ["station", "month"],
            as_index=False,
        )
        .agg(
            temp_c=("temp_c", "mean"),
            precip_mm=("precip_mm", "mean"),
        )
        .sort_values(["station", "month"])
        .reset_index(drop=True)
    )


def make_monthly_tables(
    data: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """地点×月の気温表と降水量表を作る。"""
    temperature = data.pivot(index="station", columns="month", values="temp_c")
    precipitation = data.pivot(
        index="station",
        columns="month",
        values="precip_mm",
    )
    return temperature, precipitation


def make_known_group(
    data: pd.DataFrame,
    station_names: pd.Index,
) -> pd.Series:
    """答え合わせ専用の既知グループを地点順に並べる。"""
    known_group = (
        data[["station", "known_group"]]
        .drop_duplicates()
        .set_index("station")
        .loc[station_names, "known_group"]
    )
    return known_group


def make_monthly_features(
    temperature: pd.DataFrame,
    precipitation: pd.DataFrame,
) -> pd.DataFrame:
    """月別気温12変数と対数変換降水量12変数を結合する。"""
    temperature_features = temperature.copy()
    precipitation_features = np.log1p(precipitation)
    temperature_features.columns = [
        f"T{int(month):02d}" for month in temperature.columns
    ]
    precipitation_features.columns = [
        f"logP{int(month):02d}" for month in precipitation.columns
    ]
    return temperature_features.join(precipitation_features)


def make_summary_features(
    temperature: pd.DataFrame,
    precipitation: pd.DataFrame,
) -> pd.DataFrame:
    """年平均・年較差・年降水量・降水変動係数の4変数を作る。"""
    return pd.DataFrame(
        {
            "annual_temp": temperature.mean(axis=1),
            "temp_range": temperature.max(axis=1) - temperature.min(axis=1),
            "log_annual_precip": np.log1p(precipitation.sum(axis=1)),
            "precip_cv": precipitation.std(axis=1) / precipitation.mean(axis=1),
        }
    )


def normalize_labels_by_temperature(
    labels: np.ndarray,
    temperature: pd.DataFrame,
) -> np.ndarray:
    """年平均気温が高い群をクラスタ0へそろえる。"""
    annual_temperature = temperature.mean(axis=1).to_numpy()
    cluster_means = {
        label: annual_temperature[labels == label].mean()
        for label in np.unique(labels)
    }
    warm_label = max(cluster_means, key=cluster_means.get)
    return np.where(labels == warm_label, 0, 1)


def fit_clusters(
    features: pd.DataFrame,
    temperature: pd.DataFrame,
    known_group: pd.Series,
) -> ClusterResult:
    """標準化、PCA、k-means、内部・外部評価を実行する。"""
    scaled = StandardScaler().fit_transform(features)
    pca = PCA(n_components=N_COMPONENTS)
    scores = pca.fit_transform(scaled)
    raw_labels = KMeans(
        n_clusters=N_CLUSTERS,
        n_init=N_INIT,
        random_state=RANDOM_STATE,
    ).fit_predict(scores)
    labels = normalize_labels_by_temperature(raw_labels, temperature)
    known_numeric = (known_group == "Mid-latitude").astype(int).to_numpy()
    return ClusterResult(
        scaled=scaled,
        scores=scores,
        pca=pca,
        labels=labels,
        silhouette=float(silhouette_score(scores, labels)),
        ari=float(adjusted_rand_score(known_numeric, labels)),
    )


def plot_input_cycles(
    temperature: pd.DataFrame,
    precipitation: pd.DataFrame,
    output_path: Path,
) -> None:
    """全地点の月別気温・降水量を同じ軸で描く。"""
    months = np.arange(1, 13)
    colors = plt.get_cmap("tab10")(np.linspace(0, 1, len(temperature)))
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(10, 7.2),
        sharex=True,
        constrained_layout=True,
    )
    for color, station in zip(colors, temperature.index, strict=True):
        axes[0].plot(
            months,
            temperature.loc[station],
            marker="o",
            linewidth=1.6,
            color=color,
            label=station,
        )
        axes[1].plot(
            months,
            precipitation.loc[station],
            marker="o",
            linewidth=1.6,
            color=color,
            label=station,
        )
    axes[0].set_title("Monthly temperature by location")
    axes[0].set_ylabel("Temperature (°C)")
    axes[1].set_title("Monthly precipitation by location")
    axes[1].set_xlabel("Month")
    axes[1].set_ylabel("Precipitation (mm/month)")
    axes[1].set_xticks(months)
    axes[0].legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.34),
        ncol=5,
        fontsize=8,
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_standardized_heatmap(
    features: pd.DataFrame,
    scaled: np.ndarray,
    output_path: Path,
) -> None:
    """標準化後の24特徴量を地点×特徴量のヒートマップで描く。"""
    limit = float(np.abs(scaled).max())
    fig, ax = plt.subplots(figsize=(12, 5.4), constrained_layout=True)
    image = ax.imshow(
        scaled,
        aspect="auto",
        cmap="RdBu_r",
        vmin=-limit,
        vmax=limit,
    )
    ax.set_title("Standardized monthly climate features")
    ax.set_xlabel("Feature")
    ax.set_ylabel("Location")
    ax.set_xticks(np.arange(features.shape[1]))
    ax.set_xticklabels(features.columns, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(np.arange(features.shape[0]))
    ax.set_yticklabels(features.index)
    colorbar = fig.colorbar(image, ax=ax, shrink=0.9)
    colorbar.set_label("Standardized value")
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_pca_before_clustering(
    features: pd.DataFrame,
    scaled: np.ndarray,
    output_path: Path,
) -> None:
    """主成分の寄与率と、色分け前のPC1・PC2得点を描く。"""
    full_pca = PCA().fit(scaled)
    scores = full_pca.transform(scaled)
    component_numbers = np.arange(1, len(full_pca.explained_variance_ratio_) + 1)
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(11, 4.8),
        constrained_layout=True,
    )
    axes[0].bar(
        component_numbers,
        full_pca.explained_variance_ratio_ * 100,
        color="#6f7f8f",
    )
    axes[0].plot(
        component_numbers,
        np.cumsum(full_pca.explained_variance_ratio_) * 100,
        marker="o",
        color="#c75b39",
        label="Cumulative",
    )
    axes[0].set_title("Explained variance by principal component")
    axes[0].set_xlabel("Principal component")
    axes[0].set_ylabel("Explained variance (%)")
    axes[0].set_xticks(component_numbers)
    axes[0].set_ylim(0, 105)
    axes[0].legend()

    axes[1].scatter(
        scores[:, 0],
        scores[:, 1],
        s=70,
        color="#6f7f8f",
        edgecolor="white",
    )
    for index, station in enumerate(features.index):
        axes[1].annotate(
            station,
            (scores[index, 0], scores[index, 1]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8,
        )
    axes[1].set_title("PCA scores before assigning clusters")
    axes[1].set_xlabel(
        f"PC1 ({full_pca.explained_variance_ratio_[0] * 100:.1f}%)"
    )
    axes[1].set_ylabel(
        f"PC2 ({full_pca.explained_variance_ratio_[1] * 100:.1f}%)"
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_cluster_result(
    temperature: pd.DataFrame,
    precipitation: pd.DataFrame,
    result: ClusterResult,
    output_path: Path,
) -> None:
    """PC得点の2群と、各群の平均季節変化を描く。"""
    colors = np.where(result.labels == 0, "#c75b39", "#31688e")
    fig = plt.figure(figsize=(11, 7.2), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, width_ratios=[1.15, 1])
    ax_scatter = fig.add_subplot(grid[:, 0])
    ax_temp = fig.add_subplot(grid[0, 1])
    ax_precip = fig.add_subplot(grid[1, 1])

    ax_scatter.scatter(
        result.scores[:, 0],
        result.scores[:, 1],
        c=colors,
        s=75,
        edgecolor="white",
    )
    for index, station in enumerate(temperature.index):
        ax_scatter.annotate(
            station,
            (result.scores[index, 0], result.scores[index, 1]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=9,
        )
    ax_scatter.set_title("PCA scores and k-means clusters")
    ax_scatter.set_xlabel(
        f"PC1 ({result.pca.explained_variance_ratio_[0] * 100:.1f}%)"
    )
    ax_scatter.set_ylabel(
        f"PC2 ({result.pca.explained_variance_ratio_[1] * 100:.1f}%)"
    )

    months = np.arange(1, 13)
    cluster_styles = [
        (0, "#c75b39", "Warm year-round cluster"),
        (1, "#31688e", "Cool-season cluster"),
    ]
    for label, color, name in cluster_styles:
        members = temperature.index[result.labels == label]
        ax_temp.plot(
            months,
            temperature.loc[members].mean(axis=0),
            marker="o",
            color=color,
            label=name,
        )
        ax_precip.plot(
            months,
            precipitation.loc[members].mean(axis=0),
            marker="o",
            color=color,
            label=name,
        )
    ax_temp.set_title("Cluster-mean monthly temperature")
    ax_temp.set_ylabel("Temperature (°C)")
    ax_precip.set_title("Cluster-mean monthly precipitation")
    ax_precip.set_xlabel("Month")
    ax_precip.set_ylabel("Precipitation (mm/month)")
    for axis in (ax_temp, ax_precip):
        axis.set_xticks(months)
    ax_temp.legend(fontsize=8)
    fig.suptitle("NASA POWER monthly climate, 1991–2020")
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_feature_sensitivity(
    monthly_result: ClusterResult,
    summary_result: ClusterResult,
    station_names: pd.Index,
    output_path: Path,
) -> None:
    """月別24変数と要約4変数のクラスタ結果を並べて描く。"""
    label_offsets = {
        "London": (6, -14),
        "New York": (6, 8),
        "Paris": (6, 6),
    }
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(11, 4.8),
        constrained_layout=True,
    )
    panels = [
        ("24 monthly features", monthly_result),
        ("4 summary features", summary_result),
    ]
    for ax, (title, result) in zip(axes, panels, strict=True):
        colors = np.where(result.labels == 0, "#c75b39", "#31688e")
        ax.scatter(
            result.scores[:, 0],
            result.scores[:, 1],
            c=colors,
            s=70,
            edgecolor="white",
        )
        for index, station in enumerate(station_names):
            offset = label_offsets.get(station, (5, 5))
            ax.annotate(
                station,
                (result.scores[index, 0], result.scores[index, 1]),
                xytext=offset,
                textcoords="offset points",
                fontsize=8,
            )
        ax.set_title(
            f"{title}\nSilhouette={result.silhouette:.3f}, ARI={result.ari:.3f}"
        )
        ax.set_xlabel("PC1")
        ax.set_ylabel("PC2")
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def save_assignments(
    temperature: pd.DataFrame,
    precipitation: pd.DataFrame,
    known_group: pd.Series,
    result: ClusterResult,
    output_path: Path,
) -> None:
    """地点別クラスタと要約値をCSVへ保存する。"""
    cluster_name = np.where(
        result.labels == 0,
        "Warm year-round",
        "Cool-season",
    )
    assignments = pd.DataFrame(
        {
            "station": temperature.index,
            "known_group": known_group.to_numpy(),
            "cluster": result.labels,
            "cluster_name": cluster_name,
            "pc1": result.scores[:, 0],
            "pc2": result.scores[:, 1],
            "annual_mean_temp_c": temperature.mean(axis=1).to_numpy(),
            "annual_precip_mm": precipitation.sum(axis=1).to_numpy(),
        }
    )
    assignments.round(3).to_csv(output_path, index=False)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description="NASA POWER月別気候値のクラスタリング演習",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help="演習用CSVのパス",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="図と結果CSVの出力先",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """CSV読込から可視化・クラスタリング・感度比較まで実行する。"""
    args = parse_args()
    overwrite = OVERWRITE or args.overwrite
    args.output_dir.mkdir(parents=True, exist_ok=True)
    configure_matplotlib()

    raw_data = load_monthly_data(args.input)
    climatology = make_climatology(raw_data)
    temperature, precipitation = make_monthly_tables(climatology)
    known_group = make_known_group(raw_data, temperature.index)
    monthly_features = make_monthly_features(temperature, precipitation)
    summary_features = make_summary_features(temperature, precipitation)
    monthly_result = fit_clusters(
        monthly_features,
        temperature,
        known_group,
    )
    summary_result = fit_clusters(
        summary_features,
        temperature,
        known_group,
    )

    assignments_path = args.output_dir / "cluster-assignments.csv"
    run_or_skip(
        assignments_path,
        overwrite,
        "地点別クラスタCSV",
        lambda: save_assignments(
            temperature,
            precipitation,
            known_group,
            monthly_result,
            assignments_path,
        ),
    )

    if not MAKE_PLOTS:
        return

    plot_jobs: list[tuple[Path, str, Callable[[], None]]] = [
        (
            args.output_dir / "01-input-seasonal-cycles.png",
            "入力データの季節変化",
            lambda: plot_input_cycles(
                temperature,
                precipitation,
                args.output_dir / "01-input-seasonal-cycles.png",
            ),
        ),
        (
            args.output_dir / "02-standardized-feature-heatmap.png",
            "標準化特徴量ヒートマップ",
            lambda: plot_standardized_heatmap(
                monthly_features,
                monthly_result.scaled,
                args.output_dir / "02-standardized-feature-heatmap.png",
            ),
        ),
        (
            args.output_dir / "03-pca-before-clustering.png",
            "クラスタ付与前のPCA",
            lambda: plot_pca_before_clustering(
                monthly_features,
                monthly_result.scaled,
                args.output_dir / "03-pca-before-clustering.png",
            ),
        ),
        (
            args.output_dir / "04-kmeans-clusters.png",
            "k-meansクラスタ結果",
            lambda: plot_cluster_result(
                temperature,
                precipitation,
                monthly_result,
                args.output_dir / "04-kmeans-clusters.png",
            ),
        ),
        (
            args.output_dir / "05-feature-sensitivity.png",
            "特徴量構成の感度比較",
            lambda: plot_feature_sensitivity(
                monthly_result,
                summary_result,
                temperature.index,
                args.output_dir / "05-feature-sensitivity.png",
            ),
        ),
    ]
    for output_path, label, action in plot_jobs:
        run_or_skip(output_path, overwrite, label, action)

    print(
        "月別24変数: "
        f"silhouette={monthly_result.silhouette:.3f}, "
        f"ARI={monthly_result.ari:.3f}"
    )
    print(
        "要約4変数: "
        f"silhouette={summary_result.silhouette:.3f}, "
        f"ARI={summary_result.ari:.3f}"
    )


if __name__ == "__main__":
    main()
