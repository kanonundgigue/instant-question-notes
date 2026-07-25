"""Step 1: ERA5月平均データから米国本土の擬似観測網を生成する。"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy.optimize import linear_sum_assignment


# ======== 設定 ========

START_YEAR = 2001
END_YEAR = 2020
EXPECTED_MONTHS = (END_YEAR - START_YEAR + 1) * 12
N_TEMPERATURE_STATIONS = 100
N_PRECIPITATION_STATIONS = 100
MIN_PAIR_DISTANCE_KM = 25.0
MAX_PAIR_DISTANCE_KM = 100.0
TARGET_PAIR_DISTANCE_KM = 60.0
LAND_MASK_THRESHOLD = 0.5
EARTH_RADIUS_KM = 6371.0088
GEOPOTENTIAL_GRAVITY = 9.80665
RANDOM_STATE = 42
OVERWRITE = False

TEMPERATURE_NAMES = ("t2m", "2m_temperature", "temperature_2m")
PRECIPITATION_NAMES = ("tp", "total_precipitation")
GEOPOTENTIAL_NAMES = ("z", "geopotential", "surface_geopotential")
LAND_MASK_NAMES = ("lsm", "land_sea_mask")
TIME_NAMES = ("valid_time", "time", "date")
LATITUDE_NAMES = ("latitude", "lat")
LONGITUDE_NAMES = ("longitude", "lon")

# Natural Earthなどの追加データを必要としない、CONUS外周の簡略ポリゴン。
# ERA5の陸海マスクと併用し、カナダ・メキシコ・海上格子を除外する。
CONUS_POLYGON = np.asarray(
    [
        (-124.75, 48.50),
        (-123.15, 48.98),
        (-117.00, 49.00),
        (-111.00, 49.00),
        (-104.00, 49.00),
        (-97.25, 49.00),
        (-95.10, 49.00),
        (-92.20, 48.30),
        (-89.60, 47.95),
        (-86.80, 46.50),
        (-84.80, 46.90),
        (-82.40, 45.20),
        (-79.00, 43.50),
        (-76.70, 44.90),
        (-74.70, 45.00),
        (-71.50, 45.20),
        (-67.00, 47.45),
        (-67.00, 44.60),
        (-69.00, 43.20),
        (-70.70, 41.70),
        (-73.80, 40.55),
        (-75.20, 39.60),
        (-75.00, 38.30),
        (-76.30, 36.90),
        (-75.70, 35.30),
        (-77.20, 34.30),
        (-80.10, 32.50),
        (-80.05, 26.60),
        (-81.50, 24.40),
        (-82.90, 25.10),
        (-82.00, 29.00),
        (-84.90, 29.70),
        (-88.00, 30.20),
        (-89.60, 29.20),
        (-93.80, 29.60),
        (-97.20, 25.80),
        (-99.10, 26.40),
        (-100.00, 28.60),
        (-103.00, 29.00),
        (-104.60, 29.50),
        (-106.50, 31.75),
        (-108.20, 31.78),
        (-111.10, 31.35),
        (-114.75, 32.70),
        (-117.10, 32.55),
        (-118.50, 34.00),
        (-120.00, 34.50),
        (-121.00, 36.50),
        (-122.50, 37.80),
        (-123.90, 40.30),
        (-124.75, 43.50),
        (-124.75, 48.50),
    ],
    dtype=float,
)


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_RAW_DIR = (
    _PROJECT_ROOT / "outputs" / "era5-conus-clustering" / "data" / "raw"
)
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT
    / "outputs"
    / "era5-conus-clustering"
    / "data"
    / "processed"
)
_TEMPERATURE_FILENAME = "era5-conus-temperature-monthly-2001-2020.nc"
_PRECIPITATION_FILENAME = "era5-conus-precipitation-monthly-2001-2020.nc"
_STATIC_FILENAME = "era5-conus-static.nc"
_TRUTH_FILENAME = "pseudo_truth_monthly.csv"
_OBSERVED_FILENAME = "pseudo_observed_monthly.csv"
_PAIRS_FILENAME = "station_pairs.csv"


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
    """出力一式がそろっていれば処理を省略し、部分出力は拒否する。"""
    existing = [path.exists() for path in output_paths]
    if all(existing) and not overwrite:
        print(f"skip: {label}（出力一式が既に存在します）")
        return
    if any(existing) and not overwrite:
        present = ", ".join(
            str(path) for path, exists in zip(output_paths, existing) if exists
        )
        raise FileExistsError(
            "出力が一部だけ存在します。既存ファイルを保護するため停止します。"
            f"--overwriteを使うか出力先を変更してください: {present}"
        )
    action()
    for path in output_paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"{label}の出力が作成されませんでした: {path}")
    print(f"saved: {label} ({output_paths[0].parent})")


def find_name(container: xr.Dataset | xr.DataArray, names: Sequence[str]) -> str:
    """候補名からDatasetまたはDataArrayに存在する名前を返す。"""
    if isinstance(container, xr.Dataset):
        available = set(container.variables) | set(container.dims)
    else:
        available = set(container.coords) | set(container.dims)
    for name in names:
        if name in available:
            return name
    candidates = ", ".join(names)
    found = ", ".join(sorted(available))
    raise ValueError(f"必要な名前がありません（候補: {candidates}、実データ: {found}）")


def normalize_longitude(data: xr.Dataset) -> xr.Dataset:
    """経度を-180〜180度へ正規化し、昇順に並べる。"""
    longitude_name = find_name(data, LONGITUDE_NAMES)
    longitude = data[longitude_name]
    if float(longitude.max()) > 180.0:
        normalized = ((longitude + 180.0) % 360.0) - 180.0
        data = data.assign_coords({longitude_name: normalized})
    return data.sortby(longitude_name)


def squeeze_static_field(data: xr.DataArray) -> xr.DataArray:
    """静的場から緯度・経度以外の長さ1の次元を取り除く。"""
    latitude_name = find_name(data, LATITUDE_NAMES)
    longitude_name = find_name(data, LONGITUDE_NAMES)
    extra_dimensions = [
        dimension
        for dimension in data.dims
        if dimension not in {latitude_name, longitude_name}
    ]
    for dimension in extra_dimensions:
        if data.sizes[dimension] != 1:
            raise ValueError(
                f"静的場の{dimension}次元が長さ1ではありません: "
                f"{data.sizes[dimension]}"
            )
        data = data.isel({dimension: 0}, drop=True)
    return data


def require_regular_months(time: xr.DataArray) -> pd.DatetimeIndex:
    """時間座標が2001〜2020年の連続240か月であることを確認する。"""
    dates = pd.DatetimeIndex(pd.to_datetime(time.values))
    if len(dates) != EXPECTED_MONTHS:
        raise ValueError(
            f"時間数が{EXPECTED_MONTHS}ではありません: {len(dates)}"
        )
    periods = dates.to_period("M")
    expected = pd.period_range(
        f"{START_YEAR}-01",
        f"{END_YEAR}-12",
        freq="M",
    )
    if periods.has_duplicates or not periods.equals(expected):
        raise ValueError("時間座標が2001年1月〜2020年12月の連続月ではありません。")
    return dates


def align_static_to_monthly(
    monthly: xr.Dataset,
    static: xr.Dataset,
) -> tuple[xr.Dataset, xr.Dataset]:
    """静的場を月平均場の緯度・経度格子へ厳密にそろえる。"""
    monthly_latitude = find_name(monthly, LATITUDE_NAMES)
    monthly_longitude = find_name(monthly, LONGITUDE_NAMES)
    static_latitude = find_name(static, LATITUDE_NAMES)
    static_longitude = find_name(static, LONGITUDE_NAMES)
    rename: dict[str, str] = {}
    if static_latitude != monthly_latitude:
        rename[static_latitude] = monthly_latitude
    if static_longitude != monthly_longitude:
        rename[static_longitude] = monthly_longitude
    if rename:
        static = static.rename(rename)

    same_latitude = np.array_equal(
        monthly[monthly_latitude].values,
        static[monthly_latitude].values,
    )
    same_longitude = np.array_equal(
        monthly[monthly_longitude].values,
        static[monthly_longitude].values,
    )
    if not (same_latitude and same_longitude):
        static = static.interp(
            {
                monthly_latitude: monthly[monthly_latitude],
                monthly_longitude: monthly[monthly_longitude],
            },
            method="nearest",
        )
    return monthly, static


def merge_monthly_inputs(
    temperature_source: xr.Dataset,
    precipitation_source: xr.Dataset,
) -> xr.Dataset:
    """別ファイルの月平均気温と降水量を同じ座標へそろえて結合する。"""
    temperature_source = normalize_longitude(temperature_source)
    precipitation_source = normalize_longitude(precipitation_source)
    temperature_name = find_name(temperature_source, TEMPERATURE_NAMES)
    precipitation_name = find_name(
        precipitation_source,
        PRECIPITATION_NAMES,
    )
    temperature_time = find_name(temperature_source, TIME_NAMES)
    temperature_latitude = find_name(temperature_source, LATITUDE_NAMES)
    temperature_longitude = find_name(temperature_source, LONGITUDE_NAMES)
    precipitation_time = find_name(precipitation_source, TIME_NAMES)
    precipitation_latitude = find_name(
        precipitation_source,
        LATITUDE_NAMES,
    )
    precipitation_longitude = find_name(
        precipitation_source,
        LONGITUDE_NAMES,
    )
    rename: dict[str, str] = {}
    for source_name, target_name in (
        (precipitation_time, temperature_time),
        (precipitation_latitude, temperature_latitude),
        (precipitation_longitude, temperature_longitude),
    ):
        if source_name != target_name:
            rename[source_name] = target_name
    precipitation = precipitation_source[[precipitation_name]]
    if rename:
        precipitation = precipitation.rename(rename)

    temperature_dates = require_regular_months(
        temperature_source[temperature_time]
    )
    precipitation_dates = require_regular_months(precipitation[temperature_time])
    if not temperature_dates.to_period("M").equals(
        precipitation_dates.to_period("M")
    ):
        raise ValueError("気温と降水量の年月が一致しません。")
    # CDSの分割NetCDFでは変数により時刻が00時と06時になることがある。
    # 月単位では同じため、気温側の月初時刻へ統一する。
    precipitation = precipitation.assign_coords(
        {
            temperature_time: temperature_source[temperature_time],
        }
    )
    same_latitude = np.array_equal(
        temperature_source[temperature_latitude].values,
        precipitation[temperature_latitude].values,
    )
    same_longitude = np.array_equal(
        temperature_source[temperature_longitude].values,
        precipitation[temperature_longitude].values,
    )
    if not (same_latitude and same_longitude):
        precipitation = precipitation.interp(
            {
                temperature_latitude: temperature_source[
                    temperature_latitude
                ],
                temperature_longitude: temperature_source[
                    temperature_longitude
                ],
            },
            method="nearest",
        )
    return xr.merge(
        [temperature_source[[temperature_name]], precipitation],
        compat="override",
        join="exact",
    )


def points_in_polygon(
    longitude: np.ndarray,
    latitude: np.ndarray,
    polygon: np.ndarray,
) -> np.ndarray:
    """レイ交差法で各点がポリゴン内部にあるか判定する。"""
    inside = np.zeros(longitude.shape, dtype=bool)
    x1, y1 = polygon[-1]
    for x2, y2 in polygon:
        crosses = (y1 > latitude) != (y2 > latitude)
        denominator = y2 - y1
        safe_denominator = (
            denominator if abs(denominator) > 1.0e-12 else 1.0e-12
        )
        boundary_x = (x2 - x1) * (latitude - y1) / safe_denominator + x1
        inside ^= crosses & (longitude < boundary_x)
        x1, y1 = x2, y2
    return inside


def make_candidate_table(
    monthly: xr.Dataset,
    static: xr.Dataset,
) -> tuple[pd.DataFrame, str, str, str]:
    """CONUS陸域に含まれる有限値格子を候補表へ変換する。"""
    temperature_name = find_name(monthly, TEMPERATURE_NAMES)
    precipitation_name = find_name(monthly, PRECIPITATION_NAMES)
    geopotential_name = find_name(static, GEOPOTENTIAL_NAMES)
    land_mask_name = find_name(static, LAND_MASK_NAMES)
    time_name = find_name(monthly, TIME_NAMES)
    latitude_name = find_name(monthly, LATITUDE_NAMES)
    longitude_name = find_name(monthly, LONGITUDE_NAMES)

    dates = require_regular_months(monthly[time_name])
    temperature = monthly[temperature_name].transpose(
        time_name,
        latitude_name,
        longitude_name,
    )
    precipitation = monthly[precipitation_name].transpose(
        time_name,
        latitude_name,
        longitude_name,
    )
    geopotential = squeeze_static_field(static[geopotential_name]).transpose(
        latitude_name,
        longitude_name,
    )
    land_mask = squeeze_static_field(static[land_mask_name]).transpose(
        latitude_name,
        longitude_name,
    )

    longitude_2d, latitude_2d = np.meshgrid(
        monthly[longitude_name].to_numpy().astype(float),
        monthly[latitude_name].to_numpy().astype(float),
    )
    finite_climate = (
        np.isfinite(temperature.to_numpy()).all(axis=0)
        & np.isfinite(precipitation.to_numpy()).all(axis=0)
    )
    finite_static = np.isfinite(geopotential) & np.isfinite(land_mask)
    conus = points_in_polygon(longitude_2d, latitude_2d, CONUS_POLYGON)
    eligible = (
        conus
        & finite_climate
        & finite_static
        & (land_mask.to_numpy() >= LAND_MASK_THRESHOLD)
    )
    row_index, column_index = np.nonzero(eligible)
    if len(row_index) < N_TEMPERATURE_STATIONS + N_PRECIPITATION_STATIONS:
        raise ValueError(
            "CONUS陸域の有効格子数が擬似観測点数より少なすぎます: "
            f"{len(row_index)}"
        )

    elevation_m = geopotential.to_numpy()[eligible] / GEOPOTENTIAL_GRAVITY
    candidates = pd.DataFrame(
        {
            "grid_index": np.arange(len(row_index), dtype=int),
            "latitude": latitude_2d[eligible],
            "longitude": longitude_2d[eligible],
            "elevation_m": elevation_m,
            "row_index": row_index,
            "column_index": column_index,
        }
    )
    candidates.attrs["dates"] = dates
    return candidates, temperature_name, precipitation_name, time_name


def approximate_xy_km(longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
    """CONUS内の均衡抽出に使う近似平面座標をkmで求める。"""
    reference_latitude = math.radians(37.5)
    reference_longitude = math.radians(-96.0)
    longitude_radians = np.deg2rad(longitude)
    latitude_radians = np.deg2rad(latitude)
    x_km = (
        EARTH_RADIUS_KM
        * (longitude_radians - reference_longitude)
        * math.cos(reference_latitude)
    )
    y_km = EARTH_RADIUS_KM * (
        latitude_radians - math.radians(37.5)
    )
    return np.column_stack([x_km, y_km])


def rank_standardize(values: np.ndarray) -> np.ndarray:
    """値を経験分位へ変換し、範囲を0〜1へそろえる。"""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    if len(values) == 1:
        return np.zeros(1, dtype=float)
    return ranks / float(len(values) - 1)


def select_temperature_stations(
    candidates: pd.DataFrame,
    n_stations: int,
    random_state: int,
) -> np.ndarray:
    """空間位置と標高分位に対する最大最小距離で気温点を選ぶ。"""
    if n_stations > len(candidates):
        raise ValueError("候補格子数より多い気温点は選べません。")
    xy = approximate_xy_km(
        candidates["longitude"].to_numpy(),
        candidates["latitude"].to_numpy(),
    )
    feature_columns = [
        rank_standardize(xy[:, 0]),
        rank_standardize(xy[:, 1]),
        rank_standardize(candidates["elevation_m"].to_numpy()),
    ]
    features = np.column_stack(feature_columns)
    rng = np.random.default_rng(random_state)
    first = int(rng.integers(len(candidates)))
    selected = [first]
    minimum_distance_squared = np.sum(
        (features - features[first]) ** 2,
        axis=1,
    )
    minimum_distance_squared[first] = -np.inf

    while len(selected) < n_stations:
        jitter = rng.uniform(0.0, 1.0e-12, size=len(candidates))
        next_index = int(np.argmax(minimum_distance_squared + jitter))
        selected.append(next_index)
        distance_squared = np.sum(
            (features - features[next_index]) ** 2,
            axis=1,
        )
        minimum_distance_squared = np.minimum(
            minimum_distance_squared,
            distance_squared,
        )
        minimum_distance_squared[np.asarray(selected)] = -np.inf
    return np.asarray(selected, dtype=int)


def haversine_distance_matrix(
    source_longitude: np.ndarray,
    source_latitude: np.ndarray,
    target_longitude: np.ndarray,
    target_latitude: np.ndarray,
) -> np.ndarray:
    """2つの地点集合間の大円距離行列をkmで求める。"""
    source_lon = np.deg2rad(source_longitude)[:, np.newaxis]
    source_lat = np.deg2rad(source_latitude)[:, np.newaxis]
    target_lon = np.deg2rad(target_longitude)[np.newaxis, :]
    target_lat = np.deg2rad(target_latitude)[np.newaxis, :]
    longitude_difference = target_lon - source_lon
    latitude_difference = target_lat - source_lat
    haversine = (
        np.sin(latitude_difference / 2.0) ** 2
        + np.cos(source_lat)
        * np.cos(target_lat)
        * np.sin(longitude_difference / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(
        np.sqrt(np.clip(haversine, 0.0, 1.0))
    )


def select_precipitation_stations(
    candidates: pd.DataFrame,
    temperature_indices: np.ndarray,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray]:
    """各気温点から25〜100kmの重複しない別格子を降水点に割り当てる。"""
    available_mask = np.ones(len(candidates), dtype=bool)
    available_mask[temperature_indices] = False
    available_indices = np.flatnonzero(available_mask)
    temperature = candidates.iloc[temperature_indices]
    available = candidates.iloc[available_indices]
    distances = haversine_distance_matrix(
        temperature["longitude"].to_numpy(),
        temperature["latitude"].to_numpy(),
        available["longitude"].to_numpy(),
        available["latitude"].to_numpy(),
    )
    eligible = (
        (distances >= MIN_PAIR_DISTANCE_KM)
        & (distances <= MAX_PAIR_DISTANCE_KM)
    )
    counts = eligible.sum(axis=1)
    if (counts == 0).any():
        failed = np.flatnonzero(counts == 0)
        raise ValueError(
            "指定距離内に降水候補がない気温点があります: "
            f"{failed.tolist()}"
        )

    rng = np.random.default_rng(random_state + 1)
    normalized_distance_cost = (
        np.abs(distances - TARGET_PAIR_DISTANCE_KM)
        / (MAX_PAIR_DISTANCE_KM - MIN_PAIR_DISTANCE_KM)
    )
    cost = normalized_distance_cost + rng.uniform(
        0.0,
        1.0e-4,
        size=distances.shape,
    )
    invalid_cost = 1.0e6
    cost[~eligible] = invalid_cost
    row_indices, assigned_columns = linear_sum_assignment(cost)
    if len(row_indices) != len(temperature_indices):
        raise RuntimeError("全気温点へ降水点を割り当てられませんでした。")
    assigned_distance = distances[row_indices, assigned_columns]
    if np.any(cost[row_indices, assigned_columns] >= invalid_cost):
        raise ValueError(
            "25〜100km制約を満たす一対一対応が存在しません。"
            "格子解像度または観測点数を見直してください。"
        )
    precipitation_indices = available_indices[assigned_columns]
    return precipitation_indices, assigned_distance


def convert_temperature_to_celsius(data: xr.DataArray) -> xr.DataArray:
    """ERA5の2m気温を摂氏へ変換する。"""
    units = str(data.attrs.get("units", "")).strip().lower()
    values = data.astype(float)
    if units in {"k", "kelvin"} or float(values.mean()) > 100.0:
        values = values - 273.15
    elif units not in {"degc", "°c", "c", "celsius", "degree_celsius"}:
        raise ValueError(f"解釈できない気温単位です: {units!r}")
    values.attrs.update({"units": "degC", "long_name": "2 m temperature"})
    return values


def convert_precipitation_to_monthly_mm(
    data: xr.DataArray,
    dates: pd.DatetimeIndex,
    time_name: str,
) -> xr.DataArray:
    """ERA5月平均日量の総降水量を月積算mmへ変換する。"""
    units = str(data.attrs.get("units", "")).strip().lower()
    values = data.astype(float)
    days = xr.DataArray(
        dates.days_in_month.astype(float),
        dims=(time_name,),
        coords={time_name: data[time_name]},
    )
    if units in {"m", "m/day", "m d**-1", "m day-1", "m per day"}:
        values = values * 1000.0 * days
    elif units in {"mm/day", "mm d**-1", "mm day-1", "mm per day"}:
        values = values * days
    elif units in {"mm/month", "mm month-1", "mm"}:
        values = values
    else:
        raise ValueError(f"解釈できない降水量単位です: {units!r}")
    if float(values.min()) < -1.0e-6:
        raise ValueError("単位変換後の降水量に負値があります。")
    values = values.clip(min=0.0)
    values.attrs.update(
        {"units": "mm month-1", "long_name": "monthly total precipitation"}
    )
    return values


def extract_network_data(
    monthly: xr.Dataset,
    candidates: pd.DataFrame,
    temperature_name: str,
    precipitation_name: str,
    time_name: str,
    temperature_indices: np.ndarray,
    precipitation_indices: np.ndarray,
    pair_distances: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """選択した200地点の完全月別値と地点対応表を作る。"""
    latitude_name = find_name(monthly, LATITUDE_NAMES)
    longitude_name = find_name(monthly, LONGITUDE_NAMES)
    dates = require_regular_months(monthly[time_name])
    temperature = convert_temperature_to_celsius(
        monthly[temperature_name].transpose(
            time_name,
            latitude_name,
            longitude_name,
        )
    )
    precipitation = convert_precipitation_to_monthly_mm(
        monthly[precipitation_name].transpose(
            time_name,
            latitude_name,
            longitude_name,
        ),
        dates,
        time_name,
    )

    records: list[pd.DataFrame] = []
    pair_records: list[dict[str, object]] = []
    network_specs = (
        ("temperature", "T", temperature_indices),
        ("precipitation", "P", precipitation_indices),
    )
    for network, prefix, selected_indices in network_specs:
        for pair_number, candidate_index in enumerate(selected_indices, start=1):
            candidate = candidates.iloc[int(candidate_index)]
            row_index = int(candidate["row_index"])
            column_index = int(candidate["column_index"])
            point_id = f"{prefix}{pair_number:03d}"
            pair_id = f"PAIR{pair_number:03d}"
            records.append(
                pd.DataFrame(
                    {
                        "point_id": point_id,
                        "network": network,
                        "pair_id": pair_id,
                        "date": dates.strftime("%Y-%m-%d"),
                        "latitude": float(candidate["latitude"]),
                        "longitude": float(candidate["longitude"]),
                        "elevation_m": float(candidate["elevation_m"]),
                        "temp_c": temperature.to_numpy()[
                            :,
                            row_index,
                            column_index,
                        ],
                        "precip_mm": precipitation.to_numpy()[
                            :,
                            row_index,
                            column_index,
                        ],
                    }
                )
            )
            if network == "temperature":
                precipitation_candidate = candidates.iloc[
                    int(precipitation_indices[pair_number - 1])
                ]
                pair_records.append(
                    {
                        "pair_id": pair_id,
                        "temperature_point_id": point_id,
                        "precipitation_point_id": f"P{pair_number:03d}",
                        "temperature_latitude": float(candidate["latitude"]),
                        "temperature_longitude": float(candidate["longitude"]),
                        "temperature_elevation_m": float(
                            candidate["elevation_m"]
                        ),
                        "precipitation_latitude": float(
                            precipitation_candidate["latitude"]
                        ),
                        "precipitation_longitude": float(
                            precipitation_candidate["longitude"]
                        ),
                        "precipitation_elevation_m": float(
                            precipitation_candidate["elevation_m"]
                        ),
                        "distance_km": float(pair_distances[pair_number - 1]),
                    }
                )
    truth = pd.concat(records, ignore_index=True)
    truth = truth.sort_values(["point_id", "date"]).reset_index(drop=True)
    pairs = pd.DataFrame(pair_records).sort_values("pair_id").reset_index(
        drop=True
    )
    return truth, pairs


def validate_outputs(truth: pd.DataFrame, observed: pd.DataFrame) -> None:
    """完全値と観測マスクが設計条件を満たすか検査する。"""
    expected_rows = (
        N_TEMPERATURE_STATIONS + N_PRECIPITATION_STATIONS
    ) * EXPECTED_MONTHS
    if len(truth) != expected_rows or len(observed) != expected_rows:
        raise ValueError(f"出力行数が{expected_rows}ではありません。")
    if truth["point_id"].nunique() != 200:
        raise ValueError("完全値の地点数が200ではありません。")
    if truth[["temp_c", "precip_mm"]].isna().any().any():
        raise ValueError("完全値に欠測があります。")
    counts = truth[["point_id", "network"]].drop_duplicates()["network"].value_counts()
    if counts.to_dict() != {
        "temperature": N_TEMPERATURE_STATIONS,
        "precipitation": N_PRECIPITATION_STATIONS,
    }:
        raise ValueError(f"観測網別の地点数が不正です: {counts.to_dict()}")
    temperature_rows = observed["network"] == "temperature"
    precipitation_rows = observed["network"] == "precipitation"
    if observed.loc[temperature_rows, "temp_c"].isna().any():
        raise ValueError("気温観測点の気温に欠測があります。")
    if observed.loc[temperature_rows, "precip_mm"].notna().any():
        raise ValueError("気温観測点で降水量が隠されていません。")
    if observed.loc[precipitation_rows, "precip_mm"].isna().any():
        raise ValueError("降水観測点の降水量に欠測があります。")
    if observed.loc[precipitation_rows, "temp_c"].notna().any():
        raise ValueError("降水観測点で気温が隠されていません。")


def generate_pseudo_observations(
    temperature_path: Path,
    precipitation_path: Path,
    static_path: Path,
    truth_path: Path,
    observed_path: Path,
    pairs_path: Path,
    random_state: int,
) -> None:
    """ERA5から完全値、片変数観測値、観測点対応表を生成する。"""
    require_files(
        [temperature_path, precipitation_path, static_path],
        "ERA5入力",
    )
    with (
        xr.open_dataset(temperature_path) as temperature_source,
        xr.open_dataset(precipitation_path) as precipitation_source,
        xr.open_dataset(static_path) as static_source,
    ):
        monthly = merge_monthly_inputs(
            temperature_source,
            precipitation_source,
        )
        static = normalize_longitude(static_source)
        monthly, static = align_static_to_monthly(monthly, static)
        candidates, temperature_name, precipitation_name, time_name = (
            make_candidate_table(monthly, static)
        )
        temperature_indices = select_temperature_stations(
            candidates,
            N_TEMPERATURE_STATIONS,
            random_state,
        )
        precipitation_indices, pair_distances = (
            select_precipitation_stations(
                candidates,
                temperature_indices,
                random_state,
            )
        )
        truth, pairs = extract_network_data(
            monthly,
            candidates,
            temperature_name,
            precipitation_name,
            time_name,
            temperature_indices,
            precipitation_indices,
            pair_distances,
        )

    observed = truth.copy()
    observed.loc[
        observed["network"] == "temperature",
        "precip_mm",
    ] = np.nan
    observed.loc[
        observed["network"] == "precipitation",
        "temp_c",
    ] = np.nan
    validate_outputs(truth, observed)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth.to_csv(truth_path, index=False, float_format="%.6f")
    observed.to_csv(observed_path, index=False, float_format="%.6f")
    pairs.to_csv(pairs_path, index=False, float_format="%.6f")
    print(
        "候補格子数: "
        f"{len(candidates)}, 観測点数: 200, "
        f"対応距離: {pair_distances.min():.1f}〜"
        f"{pair_distances.max():.1f} km"
    )


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "ERA5の2001〜2020年月平均データから、気温100点と"
            "降水量100点のCONUS擬似観測網を生成する"
        )
    )
    parser.add_argument(
        "--temperature-file",
        type=Path,
        default=_DEFAULT_RAW_DIR / _TEMPERATURE_FILENAME,
        help="2m気温を含む月平均NetCDF",
    )
    parser.add_argument(
        "--precipitation-file",
        type=Path,
        default=_DEFAULT_RAW_DIR / _PRECIPITATION_FILENAME,
        help="総降水量を含む月平均NetCDF",
    )
    parser.add_argument(
        "--static-file",
        type=Path,
        default=_DEFAULT_RAW_DIR / _STATIC_FILENAME,
        help="ジオポテンシャルと陸海マスクを含む静的NetCDF",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="CSV出力ディレクトリ",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=RANDOM_STATE,
        help="観測点抽出に使う固定乱数シード",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力CSVを上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """入力を検査し、擬似観測CSV一式を生成する。"""
    args = parse_args()
    if args.random_state < 0:
        raise ValueError("random-stateは0以上にしてください。")
    output_paths = (
        args.output_dir / _TRUTH_FILENAME,
        args.output_dir / _OBSERVED_FILENAME,
        args.output_dir / _PAIRS_FILENAME,
    )
    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "ERA5 CONUS擬似観測データ",
        lambda: generate_pseudo_observations(
            args.temperature_file,
            args.precipitation_file,
            args.static_file,
            output_paths[0],
            output_paths[1],
            output_paths[2],
            args.random_state,
        ),
    )


if __name__ == "__main__":
    main()
