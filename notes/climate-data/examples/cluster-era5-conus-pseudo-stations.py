"""Step 2: ERA5擬似観測を空間補完し、米国本土の気候クラスタを評価する。"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.colors import BoundaryNorm, ListedColormap
from pyproj import Transformer
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import (
    ConstantKernel,
    Matern,
    WhiteKernel,
)
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    adjusted_rand_score,
    mean_absolute_error,
    mean_squared_error,
    normalized_mutual_info_score,
    silhouette_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


# ======== 設定 ========

START_YEAR = 2001
END_YEAR = 2020
MIN_CLUSTERS = 2
MAX_CLUSTERS = 5
MIN_PCA_COMPONENTS = 2
MAX_PCA_COMPONENTS = 6
PCA_VARIANCE_THRESHOLD = 0.90
DEFAULT_DRAWS = 40
RANDOM_STATE = 42
RIDGE_ALPHA = 1.0
MIN_CLUSTER_FRACTION = 0.05
STABILITY_TOLERANCE = 0.02
SPATIAL_CV_BLOCKS = 5
OVERWRITE = False
MAKE_PLOTS = True
FIGURE_DPI = 180


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_DATA_DIR = (
    _PROJECT_ROOT
    / "outputs"
    / "era5-conus-clustering"
    / "data"
    / "processed"
)
_DEFAULT_OBSERVED = _DEFAULT_DATA_DIR / "pseudo_observed_monthly.csv"
_DEFAULT_TRUTH = _DEFAULT_DATA_DIR / "pseudo_truth_monthly.csv"
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT / "outputs" / "era5-conus-clustering" / "analysis"
)


@dataclass(frozen=True)
class SpatialPrediction:
    """1変数・1暦月の空間予測を保持する。"""

    mean: np.ndarray
    draws: np.ndarray
    standard_deviation: np.ndarray


@dataclass(frozen=True)
class FeatureSpace:
    """ブロック別PCA後の特徴空間を保持する。"""

    scores: np.ndarray
    temperature_components: int
    precipitation_components: int
    temperature_variance: float
    precipitation_variance: float


@dataclass(frozen=True)
class CandidateResult:
    """1つの候補クラスタ数に対する評価結果を保持する。"""

    n_clusters: int
    labels: np.ndarray
    coassignment: np.ndarray
    membership_confidence: np.ndarray
    mean_stability_ari: float
    stability_ari_std: float
    silhouette: float
    minimum_cluster_size: int
    oracle_ari: float | None
    oracle_nmi: float | None


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
    """代表出力が存在する場合は処理をスキップする。"""
    if output_path.exists() and not overwrite:
        print(f"skip: {label} ({output_path})")
        return
    action()
    print(f"saved: {label} ({output_path})")


def configure_matplotlib() -> None:
    """記事用図で共通して使うMatplotlib設定を適用する。"""
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


def load_monthly_data(path: Path, label: str) -> pd.DataFrame:
    """擬似観測CSVを読み、列、期間、地点数、重複を検査する。"""
    require_files([path], label)
    data = pd.read_csv(path, parse_dates=["date"])
    required_columns = {
        "point_id",
        "network",
        "pair_id",
        "date",
        "latitude",
        "longitude",
        "elevation_m",
        "temp_c",
        "precip_mm",
    }
    missing_columns = required_columns.difference(data.columns)
    if missing_columns:
        joined = ", ".join(sorted(missing_columns))
        raise ValueError(f"{label}に必要な列がありません: {joined}")
    data["point_id"] = data["point_id"].astype(str)
    data["pair_id"] = data["pair_id"].astype(str)
    data["network"] = data["network"].astype(str)
    if data.duplicated(["point_id", "date"]).any():
        raise ValueError(f"{label}に地点・年月の重複があります。")
    expected_months = (END_YEAR - START_YEAR + 1) * 12
    expected_dates = pd.date_range(
        f"{START_YEAR}-01-01",
        f"{END_YEAR}-12-01",
        freq="MS",
    )
    coverage = data.groupby("point_id")["date"].nunique()
    if not (coverage == expected_months).all():
        raise ValueError(
            f"{label}で{expected_months}か月そろっていない地点があります。"
        )
    invalid_date_sets = [
        point_id
        for point_id, group in data.groupby("point_id", sort=False)
        if not pd.DatetimeIndex(group["date"].sort_values()).equals(
            expected_dates
        )
    ]
    if invalid_date_sets:
        raise ValueError(
            f"{label}に2001年1月〜2020年12月以外の年月があります。"
        )
    if data["point_id"].nunique() != 200:
        raise ValueError(f"{label}の地点数が200ではありません。")
    if set(data["network"].unique()) != {"temperature", "precipitation"}:
        raise ValueError(f"{label}のnetwork列が想定外です。")
    network_counts = (
        data[["point_id", "network"]]
        .drop_duplicates()["network"]
        .value_counts()
    )
    if not (network_counts == 100).all():
        raise ValueError(f"{label}の各観測網が100地点ではありません。")
    return data.sort_values(["point_id", "date"]).reset_index(drop=True)


def validate_observation_mask(data: pd.DataFrame) -> None:
    """観測網ごとに片方の変数だけが公開されているか確認する。"""
    temperature_rows = data["network"] == "temperature"
    precipitation_rows = data["network"] == "precipitation"
    if data.loc[temperature_rows, "temp_c"].isna().any():
        raise ValueError("気温観測網のtemp_cに欠測があります。")
    if data.loc[temperature_rows, "precip_mm"].notna().any():
        raise ValueError("気温観測網のprecip_mmが隠されていません。")
    if data.loc[precipitation_rows, "precip_mm"].isna().any():
        raise ValueError("降水観測網のprecip_mmに欠測があります。")
    if data.loc[precipitation_rows, "temp_c"].notna().any():
        raise ValueError("降水観測網のtemp_cが隠されていません。")


def validate_truth_alignment(
    observed: pd.DataFrame,
    truth: pd.DataFrame,
) -> None:
    """解析用データと完全値の地点、年月、メタデータが一致するか確認する。"""
    key_columns = ["point_id", "date"]
    metadata_columns = [
        "point_id",
        "network",
        "pair_id",
        "latitude",
        "longitude",
        "elevation_m",
    ]
    observed_keys = observed[key_columns].sort_values(key_columns)
    truth_keys = truth[key_columns].sort_values(key_columns)
    if not observed_keys.reset_index(drop=True).equals(
        truth_keys.reset_index(drop=True)
    ):
        raise ValueError("擬似観測CSVと完全値CSVの地点・年月が一致しません。")
    observed_metadata = (
        observed[metadata_columns]
        .drop_duplicates()
        .sort_values("point_id")
        .reset_index(drop=True)
    )
    truth_metadata = (
        truth[metadata_columns]
        .drop_duplicates()
        .sort_values("point_id")
        .reset_index(drop=True)
    )
    if not observed_metadata.equals(truth_metadata):
        raise ValueError("擬似観測CSVと完全値CSVの地点情報が一致しません。")


def make_metadata(data: pd.DataFrame) -> pd.DataFrame:
    """月別表から地点メタデータを1地点1行で抽出する。"""
    columns = [
        "point_id",
        "network",
        "pair_id",
        "latitude",
        "longitude",
        "elevation_m",
    ]
    metadata = data[columns].drop_duplicates()
    if metadata["point_id"].duplicated().any():
        raise ValueError("同一point_idに複数の地点メタデータがあります。")
    return metadata.sort_values("point_id").reset_index(drop=True)


def project_coordinates(metadata: pd.DataFrame) -> np.ndarray:
    """経緯度をCONUS Albers等積座標のkmへ変換する。"""
    transformer = Transformer.from_crs(
        "EPSG:4326",
        "EPSG:5070",
        always_xy=True,
    )
    x_m, y_m = transformer.transform(
        metadata["longitude"].to_numpy(dtype=float),
        metadata["latitude"].to_numpy(dtype=float),
    )
    x_km = np.asarray(x_m, dtype=float) / 1000.0
    y_km = np.asarray(y_m, dtype=float) / 1000.0
    return np.column_stack([x_km, y_km])


def make_climatology(data: pd.DataFrame) -> pd.DataFrame:
    """20年間の年月別データから地点・暦月別平均を作る。"""
    working = data.copy()
    working["month"] = working["date"].dt.month
    group_columns = [
        "point_id",
        "network",
        "pair_id",
        "latitude",
        "longitude",
        "elevation_m",
        "month",
    ]
    return (
        working.groupby(group_columns, as_index=False, dropna=False)
        .agg(
            temp_c=("temp_c", "mean"),
            precip_mm=("precip_mm", "mean"),
        )
        .sort_values(["point_id", "month"])
        .reset_index(drop=True)
    )


def make_spatial_trend() -> object:
    """座標と標高の大域傾向を推定する回帰器を作る。"""
    return make_pipeline(
        StandardScaler(),
        Ridge(alpha=RIDGE_ALPHA),
    )


def fit_spatial_model(
    training_features: np.ndarray,
    training_values: np.ndarray,
    target_features: np.ndarray,
    n_draws: int,
    random_state: int,
) -> SpatialPrediction:
    """標高回帰と残差ガウス過程で全対象地点を予測する。"""
    trend = make_spatial_trend()
    trend.fit(training_features, training_values)
    training_trend = trend.predict(training_features)
    target_trend = trend.predict(target_features)
    residuals = training_values - training_trend

    coordinate_scaler = StandardScaler()
    training_coordinates = coordinate_scaler.fit_transform(
        training_features[:, :2]
    )
    target_coordinates = coordinate_scaler.transform(target_features[:, :2])
    residual_variance = max(float(np.var(residuals)), 1.0e-6)
    kernel = (
        ConstantKernel(
            constant_value=residual_variance,
            constant_value_bounds=(1.0e-4, 1.0e4),
        )
        * Matern(
            length_scale=np.ones(2),
            length_scale_bounds=(0.1, 20.0),
            nu=1.5,
        )
        + WhiteKernel(
            noise_level=max(residual_variance * 0.05, 1.0e-5),
            noise_level_bounds=(1.0e-6, 1.0e2),
        )
    )
    gaussian_process = GaussianProcessRegressor(
        kernel=kernel,
        normalize_y=False,
        n_restarts_optimizer=0,
        random_state=random_state,
    )
    gaussian_process.fit(training_coordinates, residuals)
    residual_mean, residual_standard_deviation = gaussian_process.predict(
        target_coordinates,
        return_std=True,
    )
    residual_draws = gaussian_process.sample_y(
        target_coordinates,
        n_samples=n_draws,
        random_state=random_state,
    )
    return SpatialPrediction(
        mean=target_trend + residual_mean,
        draws=target_trend[:, np.newaxis] + residual_draws,
        standard_deviation=residual_standard_deviation,
    )


def fit_nearest_model(
    training_coordinates: np.ndarray,
    training_values: np.ndarray,
    target_coordinates: np.ndarray,
    n_draws: int,
    random_state: int,
) -> SpatialPrediction:
    """最近傍値を中心に近傍間のばらつきから予測標本を作る。"""
    distances = cdist(target_coordinates, training_coordinates)
    nearest_indices = np.argmin(distances, axis=1)
    prediction_mean = training_values[nearest_indices]
    neighbor_count = min(4, len(training_values))
    neighbor_indices = np.argpartition(
        distances,
        kth=neighbor_count - 1,
        axis=1,
    )[:, :neighbor_count]
    neighbor_values = training_values[neighbor_indices]
    neighbor_distances = np.take_along_axis(
        distances,
        neighbor_indices,
        axis=1,
    )
    weights = 1.0 / np.maximum(neighbor_distances, 1.0) ** 2
    squared_deviation = (
        neighbor_values - prediction_mean[:, np.newaxis]
    ) ** 2
    prediction_standard_deviation = np.sqrt(
        np.sum(weights * squared_deviation, axis=1)
        / np.sum(weights, axis=1)
    )
    random_generator = np.random.default_rng(random_state)
    draws = (
        prediction_mean[:, np.newaxis]
        + prediction_standard_deviation[:, np.newaxis]
        * random_generator.normal(
            size=(len(target_coordinates), n_draws)
        )
    )
    return SpatialPrediction(
        mean=prediction_mean,
        draws=draws,
        standard_deviation=prediction_standard_deviation,
    )


def fit_regression_model(
    training_features: np.ndarray,
    training_values: np.ndarray,
    target_features: np.ndarray,
    n_draws: int,
    random_state: int,
) -> SpatialPrediction:
    """座標・標高回帰と残差標準偏差から予測標本を作る。"""
    trend = make_spatial_trend()
    trend.fit(training_features, training_values)
    training_prediction = trend.predict(training_features)
    prediction_mean = trend.predict(target_features)
    residual_standard_deviation = max(
        float(np.std(training_values - training_prediction, ddof=1)),
        1.0e-6,
    )
    standard_deviation = np.full(
        len(target_features),
        residual_standard_deviation,
        dtype=float,
    )
    random_generator = np.random.default_rng(random_state)
    draws = (
        prediction_mean[:, np.newaxis]
        + residual_standard_deviation
        * random_generator.normal(
            size=(len(target_features), n_draws)
        )
    )
    return SpatialPrediction(
        mean=prediction_mean,
        draws=draws,
        standard_deviation=standard_deviation,
    )


def choose_interpolation_methods(
    spatial_cv_metrics: pd.DataFrame,
) -> dict[str, str]:
    """空間交差検証RMSEが最小の補完法を変数ごとに選ぶ。"""
    if spatial_cv_metrics.empty:
        return {
            "temp_c": "regression_gp",
            "precip_mm": "regression_gp",
        }
    methods: dict[str, str] = {}
    for variable in ("temp_c", "precip_mm"):
        candidates = spatial_cv_metrics.loc[
            spatial_cv_metrics["variable"] == variable
        ]
        if candidates.empty:
            raise ValueError(f"{variable}の空間CV結果がありません。")
        best_index = candidates["rmse"].idxmin()
        methods[variable] = str(candidates.loc[best_index, "method"])
    return methods


def evaluate_spatial_cross_validation(
    observed_climatology: pd.DataFrame,
    metadata: pd.DataFrame,
    random_state: int,
) -> pd.DataFrame:
    """地域をまとめて外し、空間補完と基準手法の誤差を評価する。"""
    coordinates = project_coordinates(metadata)
    features = np.column_stack(
        [
            coordinates,
            metadata["elevation_m"].to_numpy(dtype=float),
        ]
    )
    point_order = metadata["point_id"].tolist()
    prediction_rows: list[dict[str, str | int | float]] = []

    for variable_index, variable in enumerate(["temp_c", "precip_mm"]):
        network = (
            "temperature" if variable == "temp_c" else "precipitation"
        )
        network_mask = (metadata["network"] == network).to_numpy()
        network_indices = np.flatnonzero(network_mask)
        block_labels = KMeans(
            n_clusters=SPATIAL_CV_BLOCKS,
            n_init=20,
            random_state=random_state + variable_index,
        ).fit_predict(coordinates[network_mask])

        for month in range(1, 13):
            monthly = (
                observed_climatology.loc[
                    observed_climatology["month"] == month
                ]
                .set_index("point_id")
                .loc[point_order]
            )
            raw_values = monthly[variable].to_numpy(dtype=float)
            for block in range(SPATIAL_CV_BLOCKS):
                test_local = block_labels == block
                train_local = ~test_local
                train_indices = network_indices[train_local]
                test_indices = network_indices[test_local]
                train_values = raw_values[train_indices]
                model_values = (
                    np.log1p(train_values)
                    if variable == "precip_mm"
                    else train_values
                )

                trend = make_spatial_trend()
                trend.fit(features[train_indices], model_values)
                regression_prediction = trend.predict(features[test_indices])
                spatial_prediction = fit_spatial_model(
                    features[train_indices],
                    model_values,
                    features[test_indices],
                    n_draws=2,
                    random_state=(
                        random_state
                        + variable_index * 100
                        + month * 10
                        + block
                    ),
                ).mean
                distances = cdist(
                    coordinates[test_indices],
                    coordinates[train_indices],
                )
                nearest_indices = np.argmin(distances, axis=1)
                nearest_prediction = model_values[nearest_indices]

                predictions = {
                    "nearest": nearest_prediction,
                    "regression": regression_prediction,
                    "regression_gp": spatial_prediction,
                }
                truth_values = raw_values[test_indices]
                for method, prediction in predictions.items():
                    if variable == "precip_mm":
                        prediction = np.maximum(
                            np.expm1(prediction),
                            0.0,
                        )
                    for point_index, truth_value, predicted_value in zip(
                        test_indices,
                        truth_values,
                        prediction,
                        strict=True,
                    ):
                        prediction_rows.append(
                            {
                                "variable": variable,
                                "method": method,
                                "month": month,
                                "point_id": point_order[point_index],
                                "truth": float(truth_value),
                                "prediction": float(predicted_value),
                            }
                        )

    predictions = pd.DataFrame(prediction_rows)
    metric_rows: list[dict[str, str | int | float]] = []
    for (variable, method), group in predictions.groupby(
        ["variable", "method"],
        sort=True,
    ):
        truth_values = group["truth"].to_numpy(dtype=float)
        predicted_values = group["prediction"].to_numpy(dtype=float)
        metric_rows.append(
            {
                "variable": str(variable),
                "method": str(method),
                "n_values": len(group),
                "mae": float(
                    mean_absolute_error(truth_values, predicted_values)
                ),
                "rmse": float(
                    mean_squared_error(truth_values, predicted_values) ** 0.5
                ),
                "bias": float(np.mean(predicted_values - truth_values)),
                "correlation": float(
                    np.corrcoef(truth_values, predicted_values)[0, 1]
                ),
            }
        )
    return pd.DataFrame(metric_rows)


def reconstruct_climatology(
    observed_climatology: pd.DataFrame,
    metadata: pd.DataFrame,
    n_draws: int,
    random_state: int,
    interpolation_methods: dict[str, str],
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """気温・降水量の月別気候場を全200地点で再構成する。"""
    coordinates = project_coordinates(metadata)
    features = np.column_stack(
        [
            coordinates,
            metadata["elevation_m"].to_numpy(dtype=float),
        ]
    )
    n_points = len(metadata)
    temperature_mean = np.empty((n_points, 12), dtype=float)
    precipitation_mean = np.empty((n_points, 12), dtype=float)
    temperature_standard_deviation = np.empty((n_points, 12), dtype=float)
    precipitation_standard_deviation = np.empty((n_points, 12), dtype=float)
    temperature_draws = np.empty((n_draws, n_points, 12), dtype=float)
    precipitation_draws = np.empty((n_draws, n_points, 12), dtype=float)

    point_order = metadata["point_id"].tolist()
    for month in range(1, 13):
        monthly = (
            observed_climatology.loc[
                observed_climatology["month"] == month
            ]
            .set_index("point_id")
            .loc[point_order]
        )
        for variable_index, variable in enumerate(["temp_c", "precip_mm"]):
            observed_mask = monthly[variable].notna().to_numpy()
            values = monthly.loc[observed_mask, variable].to_numpy(dtype=float)
            if variable == "precip_mm":
                values = np.log1p(values)
            method = interpolation_methods[variable]
            model_random_state = (
                random_state + month * 10 + variable_index
            )
            if method == "nearest":
                prediction = fit_nearest_model(
                    coordinates[observed_mask],
                    values,
                    coordinates,
                    n_draws=n_draws,
                    random_state=model_random_state,
                )
            elif method == "regression":
                prediction = fit_regression_model(
                    features[observed_mask],
                    values,
                    features,
                    n_draws=n_draws,
                    random_state=model_random_state,
                )
            elif method == "regression_gp":
                prediction = fit_spatial_model(
                    features[observed_mask],
                    values,
                    features,
                    n_draws=n_draws,
                    random_state=model_random_state,
                )
            else:
                raise ValueError(f"未対応の補完法です: {method}")
            if variable == "temp_c":
                temperature_mean[:, month - 1] = prediction.mean
                temperature_standard_deviation[:, month - 1] = (
                    prediction.standard_deviation
                )
                temperature_draws[:, :, month - 1] = prediction.draws.T
            else:
                original_scale_draws = np.maximum(
                    np.expm1(prediction.draws.T),
                    0.0,
                )
                if method == "nearest":
                    precipitation_mean[:, month - 1] = np.maximum(
                        np.expm1(prediction.mean),
                        0.0,
                    )
                else:
                    precipitation_mean[:, month - 1] = np.mean(
                        original_scale_draws,
                        axis=0,
                    )
                precipitation_standard_deviation[:, month - 1] = np.std(
                    original_scale_draws,
                    axis=0,
                    ddof=1,
                )
                precipitation_draws[:, :, month - 1] = original_scale_draws

    rows: list[dict[str, str | int | float]] = []
    for point_index, metadata_row in metadata.iterrows():
        for month in range(1, 13):
            rows.append(
                {
                    "point_id": str(metadata_row["point_id"]),
                    "network": str(metadata_row["network"]),
                    "pair_id": str(metadata_row["pair_id"]),
                    "latitude": float(metadata_row["latitude"]),
                    "longitude": float(metadata_row["longitude"]),
                    "elevation_m": float(metadata_row["elevation_m"]),
                    "month": month,
                    "temp_c": temperature_mean[point_index, month - 1],
                    "temp_interpolation_method": interpolation_methods[
                        "temp_c"
                    ],
                    "temp_prediction_sd": temperature_standard_deviation[
                        point_index,
                        month - 1,
                    ],
                    "precip_mm": precipitation_mean[
                        point_index,
                        month - 1,
                    ],
                    "precip_interpolation_method": interpolation_methods[
                        "precip_mm"
                    ],
                    "precip_prediction_sd": precipitation_standard_deviation[
                        point_index,
                        month - 1,
                    ],
                }
            )
    return pd.DataFrame(rows), temperature_draws, precipitation_draws


def choose_component_count(pca: PCA) -> int:
    """累積寄与率と上下限から採用する主成分数を決める。"""
    cumulative = np.cumsum(pca.explained_variance_ratio_)
    reached = int(np.searchsorted(cumulative, PCA_VARIANCE_THRESHOLD) + 1)
    return min(
        max(reached, MIN_PCA_COMPONENTS),
        MAX_PCA_COMPONENTS,
        len(pca.explained_variance_ratio_),
    )


def make_feature_space(
    temperature: np.ndarray,
    precipitation: np.ndarray,
) -> FeatureSpace:
    """気温・降水量を別々に標準化・PCAし、均等重みで結合する。"""
    temperature_scaled = StandardScaler().fit_transform(temperature)
    precipitation_scaled = StandardScaler().fit_transform(
        np.log1p(precipitation)
    )
    temperature_pca_full = PCA().fit(temperature_scaled)
    precipitation_pca_full = PCA().fit(precipitation_scaled)
    temperature_components = choose_component_count(temperature_pca_full)
    precipitation_components = choose_component_count(precipitation_pca_full)

    temperature_scores = temperature_pca_full.transform(
        temperature_scaled
    )[:, :temperature_components]
    precipitation_scores = precipitation_pca_full.transform(
        precipitation_scaled
    )[:, :precipitation_components]
    temperature_scores = StandardScaler().fit_transform(temperature_scores)
    precipitation_scores = StandardScaler().fit_transform(
        precipitation_scores
    )
    temperature_scores /= math.sqrt(temperature_components)
    precipitation_scores /= math.sqrt(precipitation_components)
    scores = np.column_stack([temperature_scores, precipitation_scores])
    return FeatureSpace(
        scores=scores,
        temperature_components=temperature_components,
        precipitation_components=precipitation_components,
        temperature_variance=float(
            temperature_pca_full.explained_variance_ratio_[
                :temperature_components
            ].sum()
        ),
        precipitation_variance=float(
            precipitation_pca_full.explained_variance_ratio_[
                :precipitation_components
            ].sum()
        ),
    )


def fit_ward(scores: np.ndarray, n_clusters: int) -> np.ndarray:
    """Ward法で指定数のクラスタへ分割する。"""
    return AgglomerativeClustering(
        n_clusters=n_clusters,
        linkage="ward",
    ).fit_predict(scores)


def make_coassignment(label_sets: list[np.ndarray]) -> np.ndarray:
    """複数分割から地点対の共所属率を計算する。"""
    n_points = len(label_sets[0])
    coassignment = np.zeros((n_points, n_points), dtype=float)
    for labels in label_sets:
        coassignment += labels[:, np.newaxis] == labels[np.newaxis, :]
    return coassignment / len(label_sets)


def fit_consensus_partition(
    coassignment: np.ndarray,
    n_clusters: int,
) -> np.ndarray:
    """共所属率を平均連結法で最終分割へ変換する。"""
    distance = np.clip(1.0 - coassignment, 0.0, 1.0)
    np.fill_diagonal(distance, 0.0)
    try:
        model = AgglomerativeClustering(
            n_clusters=n_clusters,
            metric="precomputed",
            linkage="average",
        )
    except TypeError:
        model = AgglomerativeClustering(
            n_clusters=n_clusters,
            affinity="precomputed",
            linkage="average",
        )
    return model.fit_predict(distance)


def align_labels(
    reference: np.ndarray,
    candidate: np.ndarray,
) -> np.ndarray:
    """Hungarian法で候補ラベルを参照ラベルへ対応付ける。"""
    reference_labels = np.unique(reference)
    candidate_labels = np.unique(candidate)
    contingency = np.zeros(
        (len(reference_labels), len(candidate_labels)),
        dtype=int,
    )
    for row_index, reference_label in enumerate(reference_labels):
        for column_index, candidate_label in enumerate(candidate_labels):
            contingency[row_index, column_index] = int(
                np.sum(
                    (reference == reference_label)
                    & (candidate == candidate_label)
                )
            )
    row_indices, column_indices = linear_sum_assignment(-contingency)
    mapping = {
        candidate_labels[column_index]: reference_labels[row_index]
        for row_index, column_index in zip(
            row_indices,
            column_indices,
            strict=True,
        )
    }
    return np.array([mapping[label] for label in candidate], dtype=int)


def membership_confidence(
    consensus_labels: np.ndarray,
    label_sets: list[np.ndarray],
) -> np.ndarray:
    """最終ラベルと一致した補完反復の割合を地点ごとに求める。"""
    aligned = np.vstack(
        [align_labels(consensus_labels, labels) for labels in label_sets]
    )
    return np.mean(aligned == consensus_labels[np.newaxis, :], axis=0)


def make_oracle_labels(
    truth_climatology: pd.DataFrame | None,
    point_order: list[str],
    n_clusters: int,
) -> np.ndarray | None:
    """完全データがある場合だけ基準クラスタを作る。"""
    if truth_climatology is None:
        return None
    indexed = truth_climatology.set_index(["point_id", "month"])
    temperature = np.array(
        [
            [indexed.loc[(point_id, month), "temp_c"] for month in range(1, 13)]
            for point_id in point_order
        ],
        dtype=float,
    )
    precipitation = np.array(
        [
            [
                indexed.loc[(point_id, month), "precip_mm"]
                for month in range(1, 13)
            ]
            for point_id in point_order
        ],
        dtype=float,
    )
    return fit_ward(
        make_feature_space(temperature, precipitation).scores,
        n_clusters,
    )


def evaluate_candidates(
    reconstructed: pd.DataFrame,
    temperature_draws: np.ndarray,
    precipitation_draws: np.ndarray,
    truth_climatology: pd.DataFrame | None,
    point_order: list[str],
) -> tuple[list[CandidateResult], FeatureSpace]:
    """候補2〜5群を補完反復の安定性と完全データで評価する。"""
    indexed = reconstructed.set_index(["point_id", "month"])
    temperature_mean = np.array(
        [
            [indexed.loc[(point_id, month), "temp_c"] for month in range(1, 13)]
            for point_id in point_order
        ],
        dtype=float,
    )
    precipitation_mean = np.array(
        [
            [
                indexed.loc[(point_id, month), "precip_mm"]
                for month in range(1, 13)
            ]
            for point_id in point_order
        ],
        dtype=float,
    )
    mean_feature_space = make_feature_space(
        temperature_mean,
        precipitation_mean,
    )
    draw_feature_spaces = [
        make_feature_space(
            temperature_draws[draw_index],
            precipitation_draws[draw_index],
        )
        for draw_index in range(temperature_draws.shape[0])
    ]

    results: list[CandidateResult] = []
    for n_clusters in range(MIN_CLUSTERS, MAX_CLUSTERS + 1):
        label_sets = [
            fit_ward(feature_space.scores, n_clusters)
            for feature_space in draw_feature_spaces
        ]
        coassignment = make_coassignment(label_sets)
        consensus_labels = fit_consensus_partition(
            coassignment,
            n_clusters,
        )
        stability_scores = np.array(
            [
                adjusted_rand_score(consensus_labels, labels)
                for labels in label_sets
            ],
            dtype=float,
        )
        oracle_labels = make_oracle_labels(
            truth_climatology,
            point_order,
            n_clusters,
        )
        results.append(
            CandidateResult(
                n_clusters=n_clusters,
                labels=consensus_labels,
                coassignment=coassignment,
                membership_confidence=membership_confidence(
                    consensus_labels,
                    label_sets,
                ),
                mean_stability_ari=float(stability_scores.mean()),
                stability_ari_std=float(stability_scores.std(ddof=1)),
                silhouette=float(
                    silhouette_score(
                        mean_feature_space.scores,
                        consensus_labels,
                    )
                ),
                minimum_cluster_size=int(
                    np.bincount(consensus_labels).min()
                ),
                oracle_ari=(
                    None
                    if oracle_labels is None
                    else float(
                        adjusted_rand_score(oracle_labels, consensus_labels)
                    )
                ),
                oracle_nmi=(
                    None
                    if oracle_labels is None
                    else float(
                        normalized_mutual_info_score(
                            oracle_labels,
                            consensus_labels,
                        )
                    )
                ),
            )
        )
    return results, mean_feature_space


def select_candidate(
    results: list[CandidateResult],
    n_points: int,
) -> CandidateResult:
    """安定性、シルエット、群サイズの事前規則で採用候補を選ぶ。"""
    minimum_size = max(5, math.ceil(n_points * MIN_CLUSTER_FRACTION))
    eligible = [
        result
        for result in results
        if result.minimum_cluster_size >= minimum_size
    ]
    if not eligible:
        eligible = results
    best_stability = max(result.mean_stability_ari for result in eligible)
    stable_candidates = [
        result
        for result in eligible
        if result.mean_stability_ari
        >= best_stability - STABILITY_TOLERANCE
    ]
    return max(
        stable_candidates,
        key=lambda result: (result.silhouette, -result.n_clusters),
    )


def evaluate_hidden_values(
    observed: pd.DataFrame,
    truth: pd.DataFrame | None,
    reconstructed: pd.DataFrame,
) -> pd.DataFrame:
    """意図的に隠した月別気候値の再構成誤差を計算する。"""
    if truth is None:
        return pd.DataFrame()
    observed_climatology = make_climatology(observed)
    truth_climatology = make_climatology(truth)
    columns = ["point_id", "network", "month", "temp_c", "precip_mm"]
    observed_small = observed_climatology[columns].rename(
        columns={
            "temp_c": "observed_temp_c",
            "precip_mm": "observed_precip_mm",
        }
    )
    truth_small = truth_climatology[columns].rename(
        columns={
            "temp_c": "truth_temp_c",
            "precip_mm": "truth_precip_mm",
        }
    )
    reconstructed_small = reconstructed[
        ["point_id", "month", "temp_c", "precip_mm"]
    ].rename(
        columns={
            "temp_c": "predicted_temp_c",
            "precip_mm": "predicted_precip_mm",
        }
    )
    merged = (
        observed_small.merge(
            truth_small,
            on=["point_id", "network", "month"],
            validate="one_to_one",
        )
        .merge(
            reconstructed_small,
            on=["point_id", "month"],
            validate="one_to_one",
        )
    )
    metadata = make_metadata(observed)
    coordinates = project_coordinates(metadata)
    rows: list[dict[str, str | int | float]] = []
    evaluations = [
        (
            "temp_c",
            "temperature",
            merged["network"] == "precipitation",
            "truth_temp_c",
            "predicted_temp_c",
        ),
        (
            "precip_mm",
            "precipitation",
            merged["network"] == "temperature",
            "truth_precip_mm",
            "predicted_precip_mm",
        ),
    ]
    for (
        variable,
        observed_network,
        mask,
        truth_column,
        prediction_column,
    ) in evaluations:
        truth_values = merged.loc[mask, truth_column].to_numpy(dtype=float)
        spatial_predictions = merged.loc[
            mask,
            prediction_column,
        ].to_numpy(dtype=float)

        train_mask = (metadata["network"] == observed_network).to_numpy()
        target_mask = ~train_mask
        distances = cdist(
            coordinates[target_mask],
            coordinates[train_mask],
        )
        nearest_train_local = np.argmin(distances, axis=1)
        train_point_ids = metadata.loc[train_mask, "point_id"].to_numpy()
        target_point_ids = metadata.loc[target_mask, "point_id"].to_numpy()
        nearest_map = dict(
            zip(
                target_point_ids,
                train_point_ids[nearest_train_local],
                strict=True,
            )
        )
        climatology_column = variable
        observed_indexed = observed_climatology.set_index(
            ["point_id", "month"]
        )
        target_rows = merged.loc[mask, ["point_id", "month"]]
        nearest_predictions = np.array(
            [
                observed_indexed.loc[
                    (nearest_map[row.point_id], row.month),
                    climatology_column,
                ]
                for row in target_rows.itertuples(index=False)
            ],
            dtype=float,
        )

        selected_method_column = (
            "temp_interpolation_method"
            if variable == "temp_c"
            else "precip_interpolation_method"
        )
        selected_method = str(
            reconstructed[selected_method_column].iloc[0]
        )
        for method, interpolation_method, predictions in (
            ("nearest_baseline", "nearest", nearest_predictions),
            ("selected_pipeline", selected_method, spatial_predictions),
        ):
            rows.append(
                {
                    "variable": variable,
                    "method": method,
                    "interpolation_method": interpolation_method,
                    "n_values": len(truth_values),
                    "mae": float(
                        mean_absolute_error(truth_values, predictions)
                    ),
                    "rmse": float(
                        mean_squared_error(
                            truth_values,
                            predictions,
                        )
                        ** 0.5
                    ),
                    "bias": float(np.mean(predictions - truth_values)),
                    "correlation": float(
                        np.corrcoef(truth_values, predictions)[0, 1]
                    ),
                }
            )
    return pd.DataFrame(rows)


def save_tables(
    output_dir: Path,
    metadata: pd.DataFrame,
    reconstructed: pd.DataFrame,
    results: list[CandidateResult],
    selected: CandidateResult,
    feature_space: FeatureSpace,
    hidden_metrics: pd.DataFrame,
    spatial_cv_metrics: pd.DataFrame,
) -> None:
    """再構成値、候補評価、地点別最終所属をCSVへ保存する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    reconstructed.to_csv(
        output_dir / "reconstructed_climatology.csv",
        index=False,
    )
    metrics_rows = [
        {
            "n_clusters": result.n_clusters,
            "mean_stability_ari": result.mean_stability_ari,
            "stability_ari_std": result.stability_ari_std,
            "silhouette": result.silhouette,
            "minimum_cluster_size": result.minimum_cluster_size,
            "oracle_ari": result.oracle_ari,
            "oracle_nmi": result.oracle_nmi,
            "selected": result.n_clusters == selected.n_clusters,
            "temperature_pca_components": (
                feature_space.temperature_components
            ),
            "precipitation_pca_components": (
                feature_space.precipitation_components
            ),
            "temperature_pca_variance": feature_space.temperature_variance,
            "precipitation_pca_variance": (
                feature_space.precipitation_variance
            ),
        }
        for result in results
    ]
    pd.DataFrame(metrics_rows).to_csv(
        output_dir / "cluster_k_metrics.csv",
        index=False,
    )
    assignments = metadata.copy()
    assignments["n_clusters"] = selected.n_clusters
    assignments["cluster"] = selected.labels
    assignments["membership_confidence"] = (
        selected.membership_confidence
    )
    assignments.to_csv(
        output_dir / "cluster_assignments.csv",
        index=False,
    )
    pd.DataFrame(
        selected.coassignment,
        index=metadata["point_id"],
        columns=metadata["point_id"],
    ).to_csv(output_dir / "selected_cluster_coassignment.csv")
    hidden_metrics.to_csv(
        output_dir / "hidden_value_metrics.csv",
        index=False,
    )
    spatial_cv_metrics.to_csv(
        output_dir / "spatial_cv_metrics.csv",
        index=False,
    )


def plot_station_network(
    metadata: pd.DataFrame,
    output_path: Path,
) -> None:
    """気温網と降水網の配置を経緯度図に描く。"""
    fig, ax = plt.subplots(figsize=(9.4, 5.6), constrained_layout=True)
    styles = {
        "temperature": ("#c75b39", "Temperature network"),
        "precipitation": ("#31688e", "Precipitation network"),
    }
    for network, (color, label) in styles.items():
        subset = metadata.loc[metadata["network"] == network]
        ax.scatter(
            subset["longitude"],
            subset["latitude"],
            s=30,
            color=color,
            edgecolor="white",
            linewidth=0.5,
            alpha=0.9,
            label=label,
        )
    ax.set_title("Two non-collocated pseudo-observation networks")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.legend()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_candidate_metrics(
    results: list[CandidateResult],
    selected: CandidateResult,
    output_path: Path,
) -> None:
    """候補クラスタ数ごとの安定性と分離度を描く。"""
    cluster_numbers = [result.n_clusters for result in results]
    stability = [result.mean_stability_ari for result in results]
    silhouette = [result.silhouette for result in results]
    oracle = [result.oracle_ari for result in results]
    fig, ax = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
    ax.plot(
        cluster_numbers,
        stability,
        marker="o",
        linewidth=2,
        label="Imputation stability ARI",
    )
    ax.plot(
        cluster_numbers,
        silhouette,
        marker="o",
        linewidth=2,
        label="Silhouette",
    )
    if all(value is not None for value in oracle):
        ax.plot(
            cluster_numbers,
            [float(value) for value in oracle if value is not None],
            marker="o",
            linewidth=2,
            label="Oracle ARI",
        )
    ax.axvline(
        selected.n_clusters,
        color="#333333",
        linestyle="--",
        linewidth=1.2,
        label=f"Selected K={selected.n_clusters}",
    )
    ax.set_xticks(cluster_numbers)
    ax.set_ylim(-0.05, 1.05)
    ax.set_xlabel("Number of clusters")
    ax.set_ylabel("Score")
    ax.set_title("Cluster-number diagnostics")
    ax.legend()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_cluster_map(
    metadata: pd.DataFrame,
    selected: CandidateResult,
    output_path: Path,
) -> None:
    """採用クラスタと地点別所属信頼度を経緯度図に描く。"""
    fig, ax = plt.subplots(figsize=(9.4, 5.6), constrained_layout=True)
    base_color_map = plt.get_cmap("tab10")
    cluster_colors = [
        base_color_map(cluster)
        for cluster in range(selected.n_clusters)
    ]
    color_map = ListedColormap(cluster_colors)
    color_norm = BoundaryNorm(
        np.arange(-0.5, selected.n_clusters + 0.5),
        selected.n_clusters,
    )
    ax.scatter(
        metadata["longitude"],
        metadata["latitude"],
        c=selected.labels,
        s=35 + 55 * selected.membership_confidence,
        cmap=color_map,
        norm=color_norm,
        edgecolor="white",
        linewidth=0.6,
    )
    ax.set_title(
        f"Consensus climate clusters (K={selected.n_clusters})"
    )
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="",
            markerfacecolor=cluster_colors[cluster],
            markeredgecolor="white",
            markersize=8,
        )
        for cluster in range(selected.n_clusters)
    ]
    ax.legend(
        handles,
        [f"Cluster {index}" for index in range(selected.n_clusters)],
        title="Consensus cluster",
        loc="lower left",
    )
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_cluster_profiles(
    reconstructed: pd.DataFrame,
    selected: CandidateResult,
    point_order: list[str],
    output_path: Path,
) -> None:
    """採用クラスタ別の月平均気温・降水量を描く。"""
    label_map = dict(zip(point_order, selected.labels, strict=True))
    data = reconstructed.copy()
    data["cluster"] = data["point_id"].map(label_map)
    profiles = (
        data.groupby(["cluster", "month"], as_index=False)
        .agg(
            temp_c=("temp_c", "mean"),
            precip_mm=("precip_mm", "mean"),
        )
    )
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(9.0, 7.0),
        sharex=True,
        constrained_layout=True,
    )
    colors = plt.get_cmap("tab10")
    for cluster in range(selected.n_clusters):
        subset = profiles.loc[profiles["cluster"] == cluster]
        axes[0].plot(
            subset["month"],
            subset["temp_c"],
            marker="o",
            color=colors(cluster),
            label=f"Cluster {cluster}",
        )
        axes[1].plot(
            subset["month"],
            subset["precip_mm"],
            marker="o",
            color=colors(cluster),
            label=f"Cluster {cluster}",
        )
    axes[0].set_title("Cluster-mean monthly temperature")
    axes[0].set_ylabel("Temperature (°C)")
    axes[1].set_title("Cluster-mean monthly precipitation")
    axes[1].set_xlabel("Month")
    axes[1].set_ylabel("Precipitation (mm/month)")
    axes[1].set_xticks(range(1, 13))
    axes[0].legend(ncol=min(selected.n_clusters, 3))
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_coassignment(
    selected: CandidateResult,
    output_path: Path,
) -> None:
    """最終クラスタ順に並べた共所属率行列を描く。"""
    order = np.argsort(selected.labels)
    sorted_matrix = selected.coassignment[np.ix_(order, order)]
    fig, ax = plt.subplots(figsize=(6.2, 5.5), constrained_layout=True)
    image = ax.imshow(
        sorted_matrix,
        cmap="viridis",
        vmin=0.0,
        vmax=1.0,
        aspect="equal",
    )
    ax.set_title("Co-assignment across spatial imputations")
    ax.set_xlabel("Location sorted by consensus cluster")
    ax.set_ylabel("Location sorted by consensus cluster")
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label("Co-assignment probability")
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def save_plots(
    output_dir: Path,
    metadata: pd.DataFrame,
    reconstructed: pd.DataFrame,
    results: list[CandidateResult],
    selected: CandidateResult,
    point_order: list[str],
) -> None:
    """記事で使う観測網・評価・クラスタ図を保存する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_station_network(metadata, output_dir / "01-station-network.png")
    plot_candidate_metrics(
        results,
        selected,
        output_dir / "02-cluster-number-diagnostics.png",
    )
    plot_cluster_map(metadata, selected, output_dir / "03-cluster-map.png")
    plot_cluster_profiles(
        reconstructed,
        selected,
        point_order,
        output_dir / "04-cluster-seasonal-profiles.png",
    )
    plot_coassignment(
        selected,
        output_dir / "05-cluster-coassignment.png",
    )


def run_analysis(
    observed_path: Path,
    truth_path: Path | None,
    output_dir: Path,
    n_draws: int,
    random_state: int,
    run_spatial_cv: bool,
) -> None:
    """空間補完、クラスタ数比較、完全データ評価、描画を実行する。"""
    observed = load_monthly_data(observed_path, "擬似観測CSV")
    validate_observation_mask(observed)
    truth = (
        None
        if truth_path is None
        else load_monthly_data(truth_path, "完全値CSV")
    )
    if truth is not None and truth[["temp_c", "precip_mm"]].isna().any().any():
        raise ValueError("完全値CSVに欠測があります。")
    if truth is not None:
        validate_truth_alignment(observed, truth)

    metadata = make_metadata(observed)
    point_order = metadata["point_id"].astype(str).tolist()
    observed_climatology = make_climatology(observed)
    truth_climatology = None if truth is None else make_climatology(truth)
    spatial_cv_metrics = (
        evaluate_spatial_cross_validation(
            observed_climatology,
            metadata,
            random_state,
        )
        if run_spatial_cv
        else pd.DataFrame()
    )
    interpolation_methods = choose_interpolation_methods(spatial_cv_metrics)
    print(
        "interpolation:",
        f"temperature={interpolation_methods['temp_c']}",
        f"precipitation={interpolation_methods['precip_mm']}",
    )
    reconstructed, temperature_draws, precipitation_draws = (
        reconstruct_climatology(
            observed_climatology,
            metadata,
            n_draws=n_draws,
            random_state=random_state,
            interpolation_methods=interpolation_methods,
        )
    )
    results, feature_space = evaluate_candidates(
        reconstructed,
        temperature_draws,
        precipitation_draws,
        truth_climatology,
        point_order,
    )
    selected = select_candidate(results, len(metadata))
    hidden_metrics = evaluate_hidden_values(
        observed,
        truth,
        reconstructed,
    )
    save_tables(
        output_dir,
        metadata,
        reconstructed,
        results,
        selected,
        feature_space,
        hidden_metrics,
        spatial_cv_metrics,
    )
    if MAKE_PLOTS:
        save_plots(
            output_dir / "figures",
            metadata,
            reconstructed,
            results,
            selected,
            point_order,
        )
    print(
        "selected:",
        f"K={selected.n_clusters}",
        f"stability={selected.mean_stability_ari:.3f}",
        f"silhouette={selected.silhouette:.3f}",
    )


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "ERA5擬似観測を空間補完し、CONUS気候クラスタを評価する"
        ),
    )
    parser.add_argument(
        "--observed",
        type=Path,
        default=_DEFAULT_OBSERVED,
        help="片方の変数を隠した年月別CSV",
    )
    parser.add_argument(
        "--truth",
        type=Path,
        default=_DEFAULT_TRUTH,
        help="評価専用の完全年月別CSV",
    )
    parser.add_argument(
        "--without-truth",
        action="store_true",
        help="完全値による最終評価を行わない",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="CSVと図の出力先",
    )
    parser.add_argument(
        "--draws",
        type=int,
        default=DEFAULT_DRAWS,
        help="空間予測分布から生成する完全データ数",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=RANDOM_STATE,
        help="乱数シード",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力があっても再計算する",
    )
    parser.add_argument(
        "--skip-spatial-cv",
        action="store_true",
        help="空間ブロック交差検証を省略する",
    )
    return parser.parse_args()


def main() -> None:
    """解析全体を上書き防止付きで実行する。"""
    args = parse_args()
    if args.draws < 2:
        raise ValueError("--drawsは2以上にしてください。")
    truth_path = None if args.without_truth else args.truth
    required = [args.observed]
    if truth_path is not None:
        required.append(truth_path)
    require_files(required, "解析入力")
    sentinel = args.output_dir / "cluster_assignments.csv"
    run_or_skip(
        sentinel,
        OVERWRITE or args.overwrite,
        "ERA5擬似観測クラスタリング",
        lambda: run_analysis(
            args.observed,
            truth_path,
            args.output_dir,
            n_draws=args.draws,
            random_state=args.random_state,
            run_spatial_cv=not args.skip_spatial_cv,
        ),
    )


if __name__ == "__main__":
    main()
