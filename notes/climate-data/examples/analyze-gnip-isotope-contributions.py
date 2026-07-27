"""Step 1: GNIP月別酸素同位体比への気温・降水量寄与を推定して分類する。"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


# ======== 設定 ========

MIN_CLUSTERS = 2
MAX_CLUSTERS = 5
MIN_OBSERVATIONS = 36
MIN_YEARS = 3
MIN_MONTHS_PER_CALENDAR_MONTH = 1
MIN_DISTINCT_CALENDAR_MONTHS = 10
MIN_ANOMALY_OBSERVATIONS_PER_MONTH = 2
CV_FOLDS = 5
BOOTSTRAP_REPLICATES = 200
RANDOM_STATE = 42
OVERWRITE = False
MAKE_PLOTS = True
FIGURE_DPI = 180
EPSILON = 1.0e-12
MAP_LABEL_FONTSIZE = 6.5
MAP_LABEL_MAXIMUM_LENGTH = 24
MAP_LABEL_PLACEMENTS = {
    "CA_7129901": (-6.0, 10.0, "right", "bottom"),
    "CA_7140801": (-6.0, 5.0, "right", "bottom"),
    "CA_7162800": (-6.0, 11.0, "right", "bottom"),
    "CA_7162801": (-6.0, -10.0, "right", "top"),
    "CA_7170201": (8.0, 12.0, "left", "bottom"),
    "CA_7181600": (-6.0, 6.0, "right", "bottom"),
    "CA_7182400": (6.0, 11.0, "left", "bottom"),
    "CA_7185001": (-6.0, -10.0, "right", "top"),
    "CA_7186601": (6.0, 8.0, "left", "bottom"),
    "CA_7189200": (6.0, 8.0, "left", "bottom"),
    "CA_7191400": (6.0, -9.0, "left", "top"),
    "CA_SITE_SABLE_ISLAND": (8.0, -14.0, "left", "top"),
    "US_7222071": (6.0, 8.0, "left", "bottom"),
    "US_7257201": (6.0, -9.0, "left", "top"),
}

COLUMN_ALIASES = {
    "station_id": (
        "station_id",
        "station",
        "station_code",
        "gnip_code",
        "gnipcode",
        "wmo_id",
        "site_id",
    ),
    "site_name": (
        "site_name",
        "station_name",
        "site",
        "name",
    ),
    "date": (
        "date",
        "sample_date",
        "sampling_date",
        "collection_date",
        "year_month",
    ),
    "year": ("year", "sample_year"),
    "month": ("month", "sample_month"),
    "latitude": ("latitude", "lat", "latitude_deg"),
    "longitude": ("longitude", "lon", "long", "longitude_deg"),
    "precip_mm": (
        "precip_mm",
        "p",
        "precipitation",
        "precipitation_mm",
        "amount_of_precipitation",
        "monthly_precipitation",
    ),
    "delta18o": (
        "delta18o",
        "delta_18o",
        "d18o",
        "o18",
        "oxygen_18",
        "oxygen18",
    ),
    "temp_c": (
        "temp_c",
        "ta",
        "temperature",
        "temperature_c",
        "air_temperature",
        "monthly_temperature",
        "t2m",
    ),
}


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_INPUT_DIR = (
    _PROJECT_ROOT / "outputs" / "gnip-isotope-clustering" / "data"
)
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT
    / "outputs"
    / "gnip-isotope-clustering"
    / "analysis"
    / "nearest"
)
_DEFAULT_GNIP_FILE = _DEFAULT_INPUT_DIR / "gnip_monthly_normalized.csv"
_CONTRIBUTIONS_FILENAME = "station_contributions.csv"
_BOOTSTRAP_FILENAME = "station_contribution_bootstrap.csv"
_CLUSTER_ASSIGNMENTS_FILENAME = "cluster_assignments.csv"
_CLUSTER_EVALUATION_FILENAME = "cluster_evaluation.csv"
_CONSENSUS_FILENAME = "consensus_matrices.csv"
_CONTRIBUTION_FIGURE_FILENAME = "isotope_contribution_scatter.png"
_CLUSTER_FIGURE_FILENAME = "contribution_cluster_heatmap.png"
_CLUSTER_DIAGNOSTICS_FIGURE_FILENAME = "cluster_number_diagnostics.png"
_CONSENSUS_FIGURE_FILENAME = "cluster_consensus_matrix.png"
_DENDROGRAM_FIGURE_FILENAME = "contribution_cluster_dendrogram.png"
_CLUSTER_MAP_FIGURE_FILENAME = "contribution_cluster_map.png"


@dataclass(frozen=True)
class ModelScores:
    """1つの応答変数に対する3モデルの決定係数を保持する。"""

    r2_temperature: float
    r2_precipitation: float
    r2_full: float
    cv_r2_temperature: float
    cv_r2_precipitation: float
    cv_r2_full: float


@dataclass(frozen=True)
class ContributionResult:
    """共通性分析とShapley型寄与の結果を保持する。"""

    unique_temperature: float
    unique_precipitation: float
    shared: float
    shapley_temperature: float
    shapley_precipitation: float
    temperature_fraction: float
    precipitation_fraction: float
    shared_fraction: float


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
    """出力一式が存在すれば省略し、部分出力は上書きせず停止する。"""
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
    print(f"saved: {label} ({output_paths[0].parent})")


def normalize_column_name(name: str) -> str:
    """入力列名を英小文字とアンダースコアへ正規化する。"""
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower())
    return normalized.strip("_")


def find_column(
    columns: Sequence[str],
    canonical_name: str,
    required: bool = True,
) -> str | None:
    """別名候補から入力に存在する列を見つける。"""
    normalized_to_original = {
        normalize_column_name(column): column for column in columns
    }
    for alias in COLUMN_ALIASES[canonical_name]:
        normalized_alias = normalize_column_name(alias)
        if normalized_alias in normalized_to_original:
            return normalized_to_original[normalized_alias]
    if required:
        aliases = ", ".join(COLUMN_ALIASES[canonical_name])
        raise ValueError(
            f"{canonical_name}列がありません。受理できる列名: {aliases}"
        )
    return None


def read_table_flexibly(path: Path) -> pd.DataFrame:
    """CSV・TSV・Excelの表を拡張子に応じて読み込む。"""
    if path.suffix.lower() in {".xls", ".xlsx"}:
        try:
            return pd.read_excel(path)
        except (ImportError, ValueError) as error:
            raise ValueError(
                "Excelファイルを読み取れません。"
                "openpyxl（.xlsx）またはxlrd（.xls）を追加してください: "
                f"{path}"
            ) from error
    try:
        return pd.read_csv(path, sep=None, engine="python")
    except (pd.errors.ParserError, UnicodeDecodeError) as error:
        raise ValueError(f"表形式ファイルを読み取れません: {path}") from error


def parse_monthly_date(data: pd.DataFrame) -> pd.Series:
    """日付列または年・月列から月初の日付を作る。"""
    date_column = find_column(data.columns, "date", required=False)
    if date_column is not None:
        dates = pd.to_datetime(data[date_column], errors="coerce")
    else:
        year_column = find_column(data.columns, "year")
        month_column = find_column(data.columns, "month")
        dates = pd.to_datetime(
            {
                "year": pd.to_numeric(data[year_column], errors="coerce"),
                "month": pd.to_numeric(data[month_column], errors="coerce"),
                "day": 1,
            },
            errors="coerce",
        )
    if dates.isna().any():
        raise ValueError("年月を解釈できない行があります。")
    return dates.dt.to_period("M").dt.to_timestamp()


def normalize_gnip_table(path: Path) -> pd.DataFrame:
    """GNIP公式エクスポートまたは正規化表を標準列へ変換する。"""
    raw = read_table_flexibly(path)
    station_column = find_column(raw.columns, "station_id")
    site_name_column = find_column(raw.columns, "site_name", required=False)
    latitude_column = find_column(raw.columns, "latitude")
    longitude_column = find_column(raw.columns, "longitude")
    precipitation_column = find_column(raw.columns, "precip_mm")
    isotope_column = find_column(raw.columns, "delta18o")
    temperature_column = find_column(raw.columns, "temp_c", required=False)
    station_ids = raw[station_column].astype(str).str.strip()
    if site_name_column is None:
        site_names = station_ids
    else:
        site_names = raw[site_name_column].astype(str).str.strip()
        site_names = site_names.mask(
            site_names.isin({"", "nan", "None"}),
            station_ids,
        )
    normalized = pd.DataFrame(
        {
            "station_id": station_ids,
            "site_name": site_names,
            "date": parse_monthly_date(raw),
            "latitude": pd.to_numeric(
                raw[latitude_column],
                errors="coerce",
            ),
            "longitude": pd.to_numeric(
                raw[longitude_column],
                errors="coerce",
            ),
            "precip_mm": pd.to_numeric(
                raw[precipitation_column],
                errors="coerce",
            ),
            "delta18o": pd.to_numeric(
                raw[isotope_column],
                errors="coerce",
            ),
        }
    )
    if temperature_column is not None:
        normalized["temp_c"] = pd.to_numeric(
            raw[temperature_column],
            errors="coerce",
        )
    numeric_columns = ["precip_mm", "delta18o", "temp_c"]
    for column in numeric_columns:
        if column in normalized:
            normalized.loc[normalized[column] <= -900.0, column] = np.nan
    return normalized


def normalize_temperature_table(path: Path) -> pd.DataFrame:
    """地点・年月別気温表を標準列へ変換する。"""
    raw = read_table_flexibly(path)
    station_column = find_column(raw.columns, "station_id")
    temperature_column = find_column(raw.columns, "temp_c")
    normalized = pd.DataFrame(
        {
            "station_id": raw[station_column].astype(str).str.strip(),
            "date": parse_monthly_date(raw),
            "temp_c": pd.to_numeric(
                raw[temperature_column],
                errors="coerce",
            ),
        }
    )
    normalized.loc[normalized["temp_c"] <= -900.0, "temp_c"] = np.nan
    return normalized


def load_analysis_data(
    gnip_path: Path,
    temperature_path: Path | None,
) -> pd.DataFrame:
    """GNIP表と任意の気温表を地点・年月で結合して検査する。"""
    required_paths = [gnip_path]
    if temperature_path is not None:
        required_paths.append(temperature_path)
    require_files(required_paths, "寄与解析入力")
    gnip = normalize_gnip_table(gnip_path)
    if temperature_path is None:
        if "temp_c" not in gnip.columns:
            raise ValueError(
                "GNIP入力にtemp_cがないため、--temperature-fileが必要です。"
            )
        merged = gnip
    else:
        temperature = normalize_temperature_table(temperature_path)
        if temperature.duplicated(["station_id", "date"]).any():
            raise ValueError("気温表に地点・年月の重複があります。")
        merged = gnip.drop(columns=["temp_c"], errors="ignore").merge(
            temperature,
            on=["station_id", "date"],
            how="left",
            validate="many_to_one",
        )

    if merged["station_id"].eq("").any():
        raise ValueError("空のstation_idがあります。")
    if merged.duplicated(["station_id", "date"]).any():
        raise ValueError("GNIP表に地点・年月の重複があります。")
    if not merged["latitude"].between(-90.0, 90.0).all():
        raise ValueError("緯度が-90〜90度の範囲外です。")
    if not merged["longitude"].between(-180.0, 180.0).all():
        raise ValueError("経度が-180〜180度の範囲外です。")
    if (merged["precip_mm"].dropna() < 0.0).any():
        raise ValueError("降水量に負値があります。")

    metadata_counts = merged.groupby("station_id")[
        ["latitude", "longitude"]
    ].nunique(dropna=True)
    if (metadata_counts > 1).any().any():
        raise ValueError("同一地点IDに複数の緯度・経度があります。")
    analysis_columns = [
        "station_id",
        "site_name",
        "date",
        "latitude",
        "longitude",
        "temp_c",
        "precip_mm",
        "delta18o",
    ]
    merged = merged[analysis_columns].replace([np.inf, -np.inf], np.nan)
    merged = merged.dropna(
        subset=["temp_c", "precip_mm", "delta18o"]
    ).copy()
    merged["log_precip"] = np.log1p(merged["precip_mm"])
    return merged.sort_values(["station_id", "date"]).reset_index(drop=True)


def validate_station_coverage(station_data: pd.DataFrame) -> None:
    """1地点の標本数、年数、暦月被覆が解析可能か確認する。"""
    station_id = str(station_data["station_id"].iloc[0])
    if len(station_data) < MIN_OBSERVATIONS:
        raise ValueError(
            f"{station_id}: 有効月数が{MIN_OBSERVATIONS}未満です。"
        )
    n_years = station_data["date"].dt.year.nunique()
    if n_years < MIN_YEARS:
        raise ValueError(f"{station_id}: 有効年数が{MIN_YEARS}未満です。")
    month_counts = station_data["date"].dt.month.value_counts()
    if len(month_counts) < MIN_DISTINCT_CALENDAR_MONTHS or (
        month_counts < MIN_MONTHS_PER_CALENDAR_MONTH
    ).any():
        raise ValueError(
            f"{station_id}: 少なくとも{MIN_DISTINCT_CALENDAR_MONTHS}暦月を"
            f"含み、各暦月に最低{MIN_MONTHS_PER_CALENDAR_MONTH}標本が必要です。"
        )


def make_mode_data(
    station_data: pd.DataFrame,
    mode: str,
) -> pd.DataFrame:
    """総変動または暦月偏差の応答・説明変数を作る。"""
    working = station_data.reset_index(drop=True).copy()
    working["year"] = working["date"].dt.year
    working["month"] = working["date"].dt.month
    columns = ["delta18o", "temp_c", "log_precip"]
    if mode == "total":
        return working
    if mode != "calendar_anomaly":
        raise ValueError(f"未知の解析モードです: {mode}")
    month_counts = working["month"].value_counts()
    usable_months = month_counts.loc[
        month_counts >= MIN_ANOMALY_OBSERVATIONS_PER_MONTH
    ].index
    working = working.loc[working["month"].isin(usable_months)].copy()
    for column in columns:
        working[column] = working[column] - working.groupby("month")[
            column
        ].transform("mean")
    return working


def fit_predict_linear(
    training_x: np.ndarray,
    training_y: np.ndarray,
    target_x: np.ndarray,
) -> np.ndarray:
    """切片付き最小二乗回帰を学習して予測する。"""
    training_design = np.column_stack(
        [np.ones(len(training_x)), training_x]
    )
    target_design = np.column_stack([np.ones(len(target_x)), target_x])
    coefficients, _, _, _ = np.linalg.lstsq(
        training_design,
        training_y,
        rcond=None,
    )
    return target_design @ coefficients


def r2_score_safe(observed: np.ndarray, predicted: np.ndarray) -> float:
    """分散がある応答に対して決定係数を計算する。"""
    residual_sum = float(np.sum((observed - predicted) ** 2))
    total_sum = float(np.sum((observed - np.mean(observed)) ** 2))
    if total_sum <= EPSILON:
        return float("nan")
    return 1.0 - residual_sum / total_sum


def in_sample_r2(x: np.ndarray, y: np.ndarray) -> float:
    """切片付き線形回帰の標本内決定係数を求める。"""
    prediction = fit_predict_linear(x, y, x)
    return r2_score_safe(y, prediction)


def blocked_cv_r2(
    station_data: pd.DataFrame,
    predictor_columns: Sequence[str],
    mode: str,
    n_folds: int,
) -> float:
    """訓練期間内で前処理する連続年ブロックCV決定係数を求める。"""
    working = station_data.reset_index(drop=True).copy()
    working["year"] = working["date"].dt.year
    working["month"] = working["date"].dt.month
    unique_years = np.sort(working["year"].unique())
    effective_folds = min(n_folds, len(unique_years))
    if effective_folds < 2:
        return float("nan")
    year_blocks = np.array_split(unique_years, effective_folds)
    prediction = np.full(len(working), np.nan, dtype=float)
    observed = np.full(len(working), np.nan, dtype=float)
    transform_columns = ["delta18o", "temp_c", "log_precip"]
    for test_years in year_blocks:
        test_mask = working["year"].isin(test_years).to_numpy()
        training_mask = ~test_mask
        training = working.loc[training_mask].copy()
        test = working.loc[test_mask].copy()
        if len(training) <= len(predictor_columns) + 1:
            continue
        if mode == "calendar_anomaly":
            climatology = training.groupby("month")[transform_columns].mean()
            climatology_counts = training.groupby("month").size()
            climatology = climatology.loc[
                climatology_counts
                >= MIN_ANOMALY_OBSERVATIONS_PER_MONTH
            ]
            for column in transform_columns:
                training[column] = training[column] - training["month"].map(
                    climatology[column]
                )
                test[column] = test[column] - test["month"].map(
                    climatology[column]
                )
        elif mode != "total":
            raise ValueError(f"未知の解析モードです: {mode}")
        training = training.dropna(
            subset=["delta18o", *predictor_columns]
        )
        test = test.dropna(subset=["delta18o", *predictor_columns])
        if len(training) <= len(predictor_columns) + 1 or test.empty:
            continue
        test_indices = test.index.to_numpy(dtype=int)
        prediction[test_indices] = fit_predict_linear(
            training[list(predictor_columns)].to_numpy(dtype=float),
            training["delta18o"].to_numpy(dtype=float),
            test[list(predictor_columns)].to_numpy(dtype=float),
        )
        observed[test_indices] = test["delta18o"].to_numpy(dtype=float)
    valid = np.isfinite(prediction) & np.isfinite(observed)
    if valid.sum() < 3:
        return float("nan")
    return r2_score_safe(observed[valid], prediction[valid])


def calculate_model_scores(
    station_data: pd.DataFrame,
    mode: str,
    cv_folds: int,
) -> ModelScores:
    """T-only、P-only、fullモデルの標本内・時間ブロックCV性能を求める。"""
    mode_data = make_mode_data(station_data, mode)
    y = mode_data["delta18o"].to_numpy(dtype=float)
    temperature = mode_data[["temp_c"]].to_numpy(dtype=float)
    precipitation = mode_data[["log_precip"]].to_numpy(dtype=float)
    full = mode_data[["temp_c", "log_precip"]].to_numpy(dtype=float)
    return ModelScores(
        r2_temperature=in_sample_r2(temperature, y),
        r2_precipitation=in_sample_r2(precipitation, y),
        r2_full=in_sample_r2(full, y),
        cv_r2_temperature=blocked_cv_r2(
            station_data,
            ["temp_c"],
            mode,
            cv_folds,
        ),
        cv_r2_precipitation=blocked_cv_r2(
            station_data,
            ["log_precip"],
            mode,
            cv_folds,
        ),
        cv_r2_full=blocked_cv_r2(
            station_data,
            ["temp_c", "log_precip"],
            mode,
            cv_folds,
        ),
    )


def decompose_contributions(scores: ModelScores) -> ContributionResult:
    """2説明変数の共通性成分と順序平均したShapley型寄与を求める。"""
    unique_temperature = scores.r2_full - scores.r2_precipitation
    unique_precipitation = scores.r2_full - scores.r2_temperature
    shared = (
        scores.r2_temperature
        + scores.r2_precipitation
        - scores.r2_full
    )
    shapley_temperature = 0.5 * (
        scores.r2_temperature
        + scores.r2_full
        - scores.r2_precipitation
    )
    shapley_precipitation = 0.5 * (
        scores.r2_precipitation
        + scores.r2_full
        - scores.r2_temperature
    )
    denominator = shapley_temperature + shapley_precipitation
    if denominator <= EPSILON:
        temperature_fraction = float("nan")
        precipitation_fraction = float("nan")
        shared_fraction = float("nan")
    else:
        temperature_fraction = shapley_temperature / denominator
        precipitation_fraction = shapley_precipitation / denominator
        shared_fraction = shared / denominator
    return ContributionResult(
        unique_temperature=unique_temperature,
        unique_precipitation=unique_precipitation,
        shared=shared,
        shapley_temperature=shapley_temperature,
        shapley_precipitation=shapley_precipitation,
        temperature_fraction=temperature_fraction,
        precipitation_fraction=precipitation_fraction,
        shared_fraction=shared_fraction,
    )


def analyze_one_mode(
    station_data: pd.DataFrame,
    mode: str,
    cv_folds: int,
) -> dict[str, object]:
    """1地点・1解析モードの寄与統計量を計算する。"""
    mode_data = make_mode_data(station_data, mode)
    scores = calculate_model_scores(station_data, mode, cv_folds)
    contribution = decompose_contributions(scores)
    return {
        "analysis_mode": mode,
        "n_observations": len(mode_data),
        "n_years": mode_data["year"].nunique(),
        **scores.__dict__,
        **contribution.__dict__,
    }


def bootstrap_station(
    station_data: pd.DataFrame,
    n_replicates: int,
    cv_folds: int,
    random_state: int,
) -> pd.DataFrame:
    """年を単位に復元抽出し、寄与統計量の標本分布を作る。"""
    rng = np.random.default_rng(random_state)
    working = station_data.copy()
    working["year"] = working["date"].dt.year
    years = np.sort(working["year"].unique())
    records: list[dict[str, object]] = []
    for replicate in range(n_replicates):
        sampled_years = rng.choice(years, size=len(years), replace=True)
        pieces: list[pd.DataFrame] = []
        for bootstrap_year, source_year in enumerate(sampled_years):
            piece = working.loc[working["year"] == source_year].copy()
            piece["date"] = piece["date"].map(
                lambda date: date.replace(
                    year=2000 + bootstrap_year,
                )
            )
            pieces.append(piece)
        sample = pd.concat(pieces, ignore_index=True)
        for mode in ("total", "calendar_anomaly"):
            result = analyze_one_mode(sample, mode, cv_folds)
            result["replicate"] = replicate
            records.append(result)
    return pd.DataFrame(records)


def summarize_bootstrap(
    bootstrap: pd.DataFrame,
) -> dict[str, float]:
    """主要寄与率の中央値と95%区間を横持ち列へまとめる。"""
    summary: dict[str, float] = {}
    statistics = (
        "temperature_fraction",
        "precipitation_fraction",
        "shared_fraction",
        "r2_full",
        "cv_r2_full",
    )
    for statistic in statistics:
        values = bootstrap[statistic].dropna().to_numpy(dtype=float)
        if len(values) == 0:
            quantiles = (float("nan"),) * 3
        else:
            quantiles = tuple(np.quantile(values, [0.025, 0.5, 0.975]))
        summary[f"{statistic}_q025"] = quantiles[0]
        summary[f"{statistic}_median"] = quantiles[1]
        summary[f"{statistic}_q975"] = quantiles[2]
    return summary


def analyze_all_stations(
    data: pd.DataFrame,
    n_bootstrap: int,
    cv_folds: int,
    random_state: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """全地点の点推定と年ブートストラップ分布を計算する。"""
    point_records: list[dict[str, object]] = []
    bootstrap_records: list[pd.DataFrame] = []
    rejected: list[str] = []
    for station_number, (station_id, station_data) in enumerate(
        data.groupby("station_id", sort=True)
    ):
        try:
            validate_station_coverage(station_data)
        except ValueError as error:
            rejected.append(str(error))
            continue
        metadata = {
            "station_id": station_id,
            "latitude": float(station_data["latitude"].iloc[0]),
            "longitude": float(station_data["longitude"].iloc[0]),
        }
        station_bootstrap = bootstrap_station(
            station_data,
            n_bootstrap,
            cv_folds,
            random_state + station_number * 10_000,
        )
        station_bootstrap.insert(0, "station_id", station_id)
        for mode in ("total", "calendar_anomaly"):
            result = analyze_one_mode(station_data, mode, cv_folds)
            mode_bootstrap = station_bootstrap.loc[
                station_bootstrap["analysis_mode"] == mode
            ]
            point_records.append(
                {
                    **metadata,
                    **result,
                    **summarize_bootstrap(mode_bootstrap),
                }
            )
        bootstrap_records.append(station_bootstrap)
    if rejected:
        print(
            f"warning: 被覆不足で除外した地点数={len(rejected)} "
            f"（先頭: {rejected[0]}）"
        )
    n_valid_stations = len(point_records) // 2
    if n_valid_stations < MIN_CLUSTERS * 2:
        raise ValueError(
            "寄与解析を通過した地点が少なすぎます。"
            f"最低{MIN_CLUSTERS * 2}地点が必要です。"
        )
    point_estimates = pd.DataFrame(point_records)
    bootstrap = pd.concat(bootstrap_records, ignore_index=True)
    return point_estimates, bootstrap


def build_feature_table(contributions: pd.DataFrame) -> pd.DataFrame:
    """総変動・暦月偏差の寄与率を地点別クラスタ特徴へ変換する。"""
    feature_columns = [
        "temperature_fraction",
        "shared_fraction",
        "r2_full",
        "cv_r2_full",
    ]
    pivot = contributions.pivot(
        index="station_id",
        columns="analysis_mode",
        values=feature_columns,
    )
    pivot.columns = [
        f"{mode}_{statistic}" for statistic, mode in pivot.columns
    ]
    pivot = pivot.reset_index()
    required = [
        f"{mode}_{statistic}"
        for mode in ("total", "calendar_anomaly")
        for statistic in feature_columns
    ]
    finite = np.isfinite(pivot[required]).all(axis=1)
    if (~finite).any():
        rejected = pivot.loc[~finite, "station_id"].tolist()
        print(
            "warning: 寄与率を定義できないためクラスタから除外: "
            f"{rejected[:5]}"
        )
    return pivot.loc[finite, ["station_id", *required]].reset_index(drop=True)


def ward_labels(features: np.ndarray, n_clusters: int) -> np.ndarray:
    """標準化特徴にWard法を適用し、1始まりのラベルを返す。"""
    if len(features) <= n_clusters:
        raise ValueError("地点数はクラスタ数より多くなければなりません。")
    hierarchy = linkage(features, method="ward", metric="euclidean")
    return fcluster(hierarchy, t=n_clusters, criterion="maxclust").astype(int)


def make_bootstrap_feature_cube(
    bootstrap: pd.DataFrame,
    station_ids: Sequence[str],
) -> tuple[np.ndarray, list[str]]:
    """ブートストラップ寄与表を反復×地点×特徴の配列にする。"""
    feature_columns = [
        "temperature_fraction",
        "shared_fraction",
        "r2_full",
        "cv_r2_full",
    ]
    records: list[np.ndarray] = []
    valid_station_ids = list(station_ids)
    for replicate in sorted(bootstrap["replicate"].unique()):
        subset = bootstrap.loc[
            (bootstrap["replicate"] == replicate)
            & bootstrap["station_id"].isin(valid_station_ids)
        ]
        pivot = subset.pivot(
            index="station_id",
            columns="analysis_mode",
            values=feature_columns,
        )
        pivot.columns = [
            f"{mode}_{statistic}" for statistic, mode in pivot.columns
        ]
        required = [
            f"{mode}_{statistic}"
            for mode in ("total", "calendar_anomaly")
            for statistic in feature_columns
        ]
        pivot = pivot.reindex(valid_station_ids)
        values = pivot[required].to_numpy(dtype=float)
        if np.isfinite(values).all():
            records.append(values)
    if not records:
        raise ValueError("クラスタに使えるブートストラップ反復がありません。")
    return np.stack(records), valid_station_ids


def consensus_cluster(
    point_features: pd.DataFrame,
    bootstrap: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """地点数に応じたKでWardコンセンサスクラスタを計算する。"""
    station_ids = point_features["station_id"].tolist()
    feature_values = point_features.drop(columns="station_id").to_numpy(
        dtype=float
    )
    standardized_point = StandardScaler().fit_transform(feature_values)
    cube, station_ids = make_bootstrap_feature_cube(bootstrap, station_ids)
    assignments: list[dict[str, object]] = []
    evaluations: list[dict[str, object]] = []
    consensus_records: list[dict[str, object]] = []

    maximum_clusters = min(
        MAX_CLUSTERS,
        len(station_ids) // 2,
    )
    if maximum_clusters < MIN_CLUSTERS:
        raise ValueError("コンセンサスクラスタに使える地点が少なすぎます。")

    for n_clusters in range(MIN_CLUSTERS, maximum_clusters + 1):
        consensus = np.zeros(
            (len(station_ids), len(station_ids)),
            dtype=float,
        )
        for replicate_values in cube:
            standardized = StandardScaler().fit_transform(replicate_values)
            labels = ward_labels(standardized, n_clusters)
            consensus += labels[:, None] == labels[None, :]
        consensus /= cube.shape[0]
        consensus_profiles = StandardScaler().fit_transform(consensus)
        final_labels = ward_labels(consensus_profiles, n_clusters)
        silhouette = silhouette_score(
            standardized_point,
            final_labels,
            metric="euclidean",
        )
        pac = float(
            np.mean(
                (consensus[np.triu_indices(len(station_ids), k=1)] > 0.1)
                & (
                    consensus[
                        np.triu_indices(len(station_ids), k=1)
                    ]
                    < 0.9
                )
            )
        )
        confidences: list[float] = []
        for index, station_id in enumerate(station_ids):
            same_cluster = final_labels == final_labels[index]
            same_cluster[index] = False
            if same_cluster.any():
                confidence = float(consensus[index, same_cluster].mean())
            else:
                confidence = 1.0
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
                "n_bootstrap_used": cube.shape[0],
                "silhouette": silhouette,
                "pac_0p1_0p9": pac,
                "mean_membership_confidence": float(np.mean(confidences)),
                "minimum_cluster_size": int(
                    pd.Series(final_labels).value_counts().min()
                ),
            }
        )
        upper_row, upper_column = np.triu_indices(len(station_ids))
        for row, column in zip(upper_row, upper_column):
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


def configure_matplotlib() -> None:
    """記事用図のMatplotlib設定を適用する。"""
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": FIGURE_DPI,
            "font.size": 9,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "legend.frameon": False,
        }
    )


def compact_station_labels(
    station_ids: Sequence[str],
    station_labels: dict[str, str],
    maximum_length: int = 28,
) -> list[str]:
    """図中で地点を識別できる範囲に保ちながら長い地点名を縮める。"""
    original = [
        station_labels.get(station_id, station_id)
        for station_id in station_ids
    ]
    base = [label.split("(", 1)[0].strip() for label in original]
    base_counts = pd.Series(base).value_counts()
    compact: list[str] = []
    for full_label, base_label in zip(original, base):
        label = (
            full_label
            if base_counts.get(base_label, 0) > 1
            else base_label
        )
        if len(label) > maximum_length:
            label = f"{label[: maximum_length - 1].rstrip()}…"
        compact.append(label)
    return compact


def save_contribution_figure(
    contributions: pd.DataFrame,
    output_path: Path,
) -> None:
    """総変動と暦月偏差のShapley型気温寄与率を散布図にする。"""
    pivot = contributions.pivot(
        index="station_id",
        columns="analysis_mode",
        values=["temperature_fraction", "r2_full"],
    )
    x = pivot["temperature_fraction"]["total"]
    y = pivot["temperature_fraction"]["calendar_anomaly"]
    color = pivot["r2_full"]["total"]
    fig, ax = plt.subplots(figsize=(7.0, 5.5), constrained_layout=True)
    scatter = ax.scatter(
        x,
        y,
        c=color,
        cmap="viridis",
        s=38,
        alpha=0.85,
        edgecolor="white",
        linewidth=0.4,
    )
    ax.axhline(0.5, color="0.5", linewidth=0.8, linestyle="--")
    ax.axvline(0.5, color="0.5", linewidth=0.8, linestyle="--")
    ax.set(
        xlabel="Temperature Shapley fraction: total variation",
        ylabel="Temperature Shapley fraction: calendar-month anomaly",
        title="Temperature and precipitation contributions to GNIP δ18O",
    )
    fig.colorbar(scatter, ax=ax, label="Full-model R²: total variation")
    fig.savefig(output_path)
    plt.close(fig)


def save_cluster_figure(
    features: pd.DataFrame,
    assignments: pd.DataFrame,
    evaluation: pd.DataFrame,
    station_labels: dict[str, str],
    output_path: Path,
) -> None:
    """推奨Kの標準化寄与特徴をクラスタ順ヒートマップにする。"""
    recommended_k = int(
        evaluation.loc[evaluation["recommended"], "n_clusters"].iloc[0]
    )
    selected = assignments.loc[
        assignments["n_clusters"] == recommended_k,
        ["station_id", "cluster"],
    ]
    merged = features.merge(selected, on="station_id", validate="one_to_one")
    merged = merged.sort_values(["cluster", "station_id"]).reset_index(drop=True)
    feature_columns = [
        column
        for column in merged.columns
        if column not in {"station_id", "cluster"}
    ]
    standardized = StandardScaler().fit_transform(merged[feature_columns])
    figure_height = max(5.0, 0.18 * len(merged))
    fig, ax = plt.subplots(
        figsize=(10.0, figure_height),
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
    ax.set_xticklabels(feature_columns, rotation=45, ha="right")
    ax.set_yticks(np.arange(len(merged)))
    ax.set_yticklabels(
        [
            f"C{cluster} {station_label}"
            for cluster, station_label in zip(
                merged["cluster"],
                compact_station_labels(
                    merged["station_id"].tolist(),
                    station_labels,
                ),
            )
        ]
    )
    ax.set_title(
        "Ward consensus clusters of isotope contributions "
        f"(K={recommended_k})"
    )
    fig.colorbar(image, ax=ax, label="Standardized contribution feature")
    fig.savefig(output_path)
    plt.close(fig)


def save_cluster_diagnostics_figure(
    evaluation: pd.DataFrame,
    output_path: Path,
) -> None:
    """候補クラスタ数ごとの分離度と安定性を3指標で描く。"""
    diagnostics = (
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
        len(diagnostics),
        figsize=(11.0, 3.6),
        sharex=True,
        constrained_layout=True,
    )
    for axis_index, (ax, (column, title, higher_is_better)) in enumerate(
        zip(axes, diagnostics)
    ):
        ax.plot(
            evaluation["n_clusters"],
            evaluation[column],
            color="0.55",
            marker="o",
            linewidth=1.3,
            zorder=1,
        )
        ax.scatter(
            evaluation.loc[recommended, "n_clusters"],
            evaluation.loc[recommended, column],
            s=90,
            facecolors="none",
            edgecolors="#c44e52",
            linewidth=2.0,
            label="Recommended K",
            zorder=2,
        )
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_xlabel("Number of clusters (K)")
        ax.set_ylabel("Higher is better" if higher_is_better else "Lower is better")
        ax.set_xticks(evaluation["n_clusters"])
        ax.grid(False)
        ax.grid(axis="y", alpha=0.25)
        if axis_index == 0:
            ax.legend(loc="best")
    fig.savefig(output_path)
    plt.close(fig)


def build_consensus_matrix(
    consensus: pd.DataFrame,
    n_clusters: int,
) -> tuple[list[str], np.ndarray]:
    """ロング形式の共所属率を地点順と対称行列へ変換する。"""
    selected = consensus.loc[consensus["n_clusters"] == n_clusters]
    station_ids = sorted(
        set(selected["station_id_1"]) | set(selected["station_id_2"])
    )
    station_index = {
        station_id: index for index, station_id in enumerate(station_ids)
    }
    consensus_matrix = np.eye(len(station_ids), dtype=float)
    for row in selected.itertuples(index=False):
        first = station_index[row.station_id_1]
        second = station_index[row.station_id_2]
        consensus_matrix[first, second] = row.coassignment_probability
        consensus_matrix[second, first] = row.coassignment_probability
    return station_ids, consensus_matrix


def save_consensus_figure(
    consensus: pd.DataFrame,
    assignments: pd.DataFrame,
    evaluation: pd.DataFrame,
    station_labels: dict[str, str],
    output_path: Path,
) -> None:
    """推奨Kの地点間共所属率をクラスタ順の行列として描く。"""
    recommended_k = int(
        evaluation.loc[evaluation["recommended"], "n_clusters"].iloc[0]
    )
    station_ids, consensus_matrix = build_consensus_matrix(
        consensus,
        recommended_k,
    )
    station_index = {
        station_id: index for index, station_id in enumerate(station_ids)
    }
    selected_assignments = assignments.loc[
        assignments["n_clusters"] == recommended_k,
        ["station_id", "cluster", "membership_confidence"],
    ].sort_values(
        ["cluster", "membership_confidence"],
        ascending=[True, False],
    )
    ordered_ids = selected_assignments["station_id"].tolist()
    order = [station_index[station_id] for station_id in ordered_ids]
    ordered_matrix = consensus_matrix[np.ix_(order, order)]
    labels = compact_station_labels(ordered_ids, station_labels)

    fig, ax = plt.subplots(
        figsize=(9.5, 6.0),
        constrained_layout=True,
    )
    image = ax.imshow(
        ordered_matrix,
        cmap="viridis",
        vmin=0.0,
        vmax=1.0,
    )
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=70, ha="right", fontsize=7)
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_title(
        f"Station coassignment across year bootstraps (K={recommended_k})",
        loc="left",
        fontweight="bold",
    )
    ax.grid(False)
    fig.colorbar(
        image,
        ax=ax,
        label="Coassignment probability",
        fraction=0.03,
        pad=0.02,
    )
    fig.savefig(output_path)
    plt.close(fig)


def save_dendrogram_figure(
    consensus: pd.DataFrame,
    evaluation: pd.DataFrame,
    station_labels: dict[str, str],
    output_path: Path,
) -> None:
    """推奨Kの共所属プロファイルからWard法デンドログラムを描く。"""
    recommended_k = int(
        evaluation.loc[evaluation["recommended"], "n_clusters"].iloc[0]
    )
    station_ids, consensus_matrix = build_consensus_matrix(
        consensus,
        recommended_k,
    )

    standardized_profiles = StandardScaler().fit_transform(consensus_matrix)
    hierarchy = linkage(
        standardized_profiles,
        method="ward",
        metric="euclidean",
    )
    lower_distance = hierarchy[len(station_ids) - recommended_k - 1, 2]
    upper_distance = hierarchy[len(station_ids) - recommended_k, 2]
    cut_distance = float((lower_distance + upper_distance) / 2.0)

    fig, ax = plt.subplots(
        figsize=(10.0, 6.0),
        constrained_layout=True,
    )
    dendrogram(
        hierarchy,
        labels=compact_station_labels(station_ids, station_labels),
        orientation="right",
        color_threshold=cut_distance,
        above_threshold_color="0.35",
        leaf_font_size=8,
        ax=ax,
    )
    ax.axvline(
        cut_distance,
        color="0.35",
        linewidth=0.9,
        linestyle="--",
    )
    ax.set_title(
        f"Ward dendrogram of GNIP consensus profiles (K={recommended_k})",
        loc="left",
        fontweight="bold",
    )
    ax.set_xlabel("Ward distance between standardized consensus profiles")
    ax.set_ylabel("GNIP station")
    ax.grid(False)
    ax.grid(axis="x", alpha=0.25)
    fig.savefig(output_path)
    plt.close(fig)


def save_cluster_map_figure(
    data: pd.DataFrame,
    assignments: pd.DataFrame,
    evaluation: pd.DataFrame,
    output_path: Path,
) -> None:
    """推奨Kの地点をクラスタ別に色分けして北米地図へ描く。"""
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
    except ImportError as error:
        raise RuntimeError(
            "地図の描画にはcartopyが必要です。uvの依存関係へ追加してください。"
        ) from error

    recommended_k = int(
        evaluation.loc[evaluation["recommended"], "n_clusters"].iloc[0]
    )
    selected = assignments.loc[
        assignments["n_clusters"] == recommended_k,
        ["station_id", "cluster"],
    ]
    locations = (
        data[
            [
                "station_id",
                "site_name",
                "latitude",
                "longitude",
            ]
        ]
        .drop_duplicates("station_id")
        .merge(selected, on="station_id", validate="one_to_one")
        .sort_values(["cluster", "station_id"])
    )

    data_crs = ccrs.PlateCarree()
    projection = ccrs.LambertConformal(
        central_longitude=-100,
        central_latitude=45,
    )
    cluster_colors = {
        1: "#31688e",
        2: "#d95f02",
        3: "#1b9e77",
        4: "#7570b3",
        5: "#e7298a",
    }
    fig = plt.figure(figsize=(11.0, 6.4), constrained_layout=True)
    ax = fig.add_subplot(1, 1, 1, projection=projection)
    ax.set_extent([-130, -55, 20, 75], crs=data_crs)
    ax.add_feature(cfeature.LAND, facecolor="#f2f2f2", zorder=0)
    ax.add_feature(cfeature.OCEAN, facecolor="#eaf2f8", zorder=0)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.55, zorder=1)
    ax.add_feature(cfeature.LAKES, facecolor="#eaf2f8", alpha=0.8, zorder=1)
    ax.gridlines(
        draw_labels=False,
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
            s=62,
            color=cluster_colors[int(cluster)],
            edgecolor="white",
            linewidth=0.7,
            label=f"Cluster {int(cluster)} (n={len(group)})",
            zorder=3,
        )

    station_labels = dict(
        locations[["station_id", "site_name"]].itertuples(
            index=False,
            name=None,
        )
    )
    map_labels = compact_station_labels(
        locations["station_id"].tolist(),
        station_labels,
        maximum_length=MAP_LABEL_MAXIMUM_LENGTH,
    )
    label_coordinates = data_crs._as_mpl_transform(ax)
    for row, label in zip(locations.itertuples(index=False), map_labels):
        offset_x, offset_y, horizontal_alignment, vertical_alignment = (
            MAP_LABEL_PLACEMENTS.get(
                row.station_id,
                (6.0, 5.0, "left", "bottom"),
            )
        )
        ax.annotate(
            label,
            xy=(row.longitude, row.latitude),
            xycoords=label_coordinates,
            xytext=(offset_x, offset_y),
            textcoords="offset points",
            ha=horizontal_alignment,
            va=vertical_alignment,
            fontsize=MAP_LABEL_FONTSIZE,
            color="0.18",
            bbox={
                "boxstyle": "round,pad=0.12",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.78,
            },
            arrowprops={
                "arrowstyle": "-",
                "color": "0.35",
                "linewidth": 0.35,
            },
            annotation_clip=True,
            zorder=4,
        )

    ax.set_title(
        f"GNIP station clusters from isotope-contribution profiles (K={recommended_k})",
        loc="left",
        fontweight="bold",
    )
    ax.legend(loc="lower left", title="Consensus cluster")
    fig.savefig(output_path)
    plt.close(fig)


def run_analysis(
    gnip_path: Path,
    temperature_path: Path | None,
    output_dir: Path,
    n_bootstrap: int,
    cv_folds: int,
    random_state: int,
    make_plots: bool,
) -> None:
    """入力結合から寄与推定・クラスタ・表・図の保存まで実行する。"""
    data = load_analysis_data(gnip_path, temperature_path)
    contributions, bootstrap = analyze_all_stations(
        data,
        n_bootstrap,
        cv_folds,
        random_state,
    )
    features = build_feature_table(contributions)
    assignments, evaluation, consensus = consensus_cluster(
        features,
        bootstrap,
    )
    station_labels = dict(
        data[["station_id", "site_name"]]
        .drop_duplicates("station_id")
        .itertuples(index=False, name=None)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    contributions.to_csv(
        output_dir / _CONTRIBUTIONS_FILENAME,
        index=False,
        float_format="%.8f",
    )
    bootstrap.to_csv(
        output_dir / _BOOTSTRAP_FILENAME,
        index=False,
        float_format="%.8f",
    )
    assignments.to_csv(
        output_dir / _CLUSTER_ASSIGNMENTS_FILENAME,
        index=False,
        float_format="%.8f",
    )
    evaluation.to_csv(
        output_dir / _CLUSTER_EVALUATION_FILENAME,
        index=False,
        float_format="%.8f",
    )
    consensus.to_csv(
        output_dir / _CONSENSUS_FILENAME,
        index=False,
        float_format="%.8f",
    )
    if make_plots:
        configure_matplotlib()
        save_contribution_figure(
            contributions,
            output_dir / _CONTRIBUTION_FIGURE_FILENAME,
        )
        save_cluster_figure(
            features,
            assignments,
            evaluation,
            station_labels,
            output_dir / _CLUSTER_FIGURE_FILENAME,
        )
        save_cluster_diagnostics_figure(
            evaluation,
            output_dir / _CLUSTER_DIAGNOSTICS_FIGURE_FILENAME,
        )
        save_consensus_figure(
            consensus,
            assignments,
            evaluation,
            station_labels,
            output_dir / _CONSENSUS_FIGURE_FILENAME,
        )
        save_dendrogram_figure(
            consensus,
            evaluation,
            station_labels,
            output_dir / _DENDROGRAM_FIGURE_FILENAME,
        )
        save_cluster_map_figure(
            data,
            assignments,
            evaluation,
            output_dir / _CLUSTER_MAP_FIGURE_FILENAME,
        )
    print(
        f"解析地点数={features['station_id'].nunique()}, "
        f"年ブートストラップ={n_bootstrap}, "
        "推奨K="
        f"{int(evaluation.loc[evaluation['recommended'], 'n_clusters'].iloc[0])}"
    )


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "GNIP月別δ18Oに対する気温・降水量の寄与を地点別に推定し、"
            "寄与特徴をK=2〜5のWardコンセンサスクラスタへ分類する"
        )
    )
    parser.add_argument(
        "--gnip-file",
        type=Path,
        default=_DEFAULT_GNIP_FILE,
        help="GNIP月別正規化CSVまたは公式Excel/CSVエクスポート",
    )
    parser.add_argument(
        "--temperature-file",
        type=Path,
        default=None,
        help="任意の地点・年月別気温表（GNIP表にtemp_cがあれば省略可）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="解析表と図の出力ディレクトリ",
    )
    parser.add_argument(
        "--bootstrap-replicates",
        type=int,
        default=BOOTSTRAP_REPLICATES,
        help="地点別の年ブートストラップ反復数",
    )
    parser.add_argument(
        "--cv-folds",
        type=int,
        default=CV_FOLDS,
        help="連続年ブロック交差検証の分割数",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=RANDOM_STATE,
        help="ブートストラップの固定乱数シード",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="PNG図を作成しない",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の解析出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """引数を検査し、GNIP同位体寄与解析を実行する。"""
    args = parse_args()
    if args.bootstrap_replicates < 10:
        raise ValueError("bootstrap-replicatesは10以上にしてください。")
    if args.cv_folds < 2:
        raise ValueError("cv-foldsは2以上にしてください。")
    if args.random_state < 0:
        raise ValueError("random-stateは0以上にしてください。")
    make_plots = MAKE_PLOTS and not args.no_plots
    output_paths = [
        args.output_dir / _CONTRIBUTIONS_FILENAME,
        args.output_dir / _BOOTSTRAP_FILENAME,
        args.output_dir / _CLUSTER_ASSIGNMENTS_FILENAME,
        args.output_dir / _CLUSTER_EVALUATION_FILENAME,
        args.output_dir / _CONSENSUS_FILENAME,
    ]
    if make_plots:
        output_paths.extend(
            [
                args.output_dir / _CONTRIBUTION_FIGURE_FILENAME,
                args.output_dir / _CLUSTER_FIGURE_FILENAME,
                args.output_dir / _CLUSTER_DIAGNOSTICS_FIGURE_FILENAME,
                args.output_dir / _CONSENSUS_FIGURE_FILENAME,
                args.output_dir / _DENDROGRAM_FIGURE_FILENAME,
                args.output_dir / _CLUSTER_MAP_FIGURE_FILENAME,
            ]
        )
    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "GNIP酸素同位体比の寄与解析",
        lambda: run_analysis(
            args.gnip_file,
            args.temperature_file,
            args.output_dir,
            args.bootstrap_replicates,
            args.cv_folds,
            args.random_state,
            make_plots,
        ),
    )


if __name__ == "__main__":
    main()
