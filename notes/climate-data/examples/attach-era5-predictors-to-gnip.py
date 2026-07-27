"""Step 3: GNIP月別δ18OへERA5気温・降水量と代表性診断を付与する。"""

from __future__ import annotations

import argparse
import calendar
import math
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


# ======== 設定 ========

GEOPOTENTIAL_GRAVITY = 9.80665
LAPSE_RATE_C_PER_KM = 6.5
EARTH_RADIUS_KM = 6371.0088
MIN_OBSERVATIONS = 36
MIN_YEARS = 3
MIN_DISTINCT_CALENDAR_MONTHS = 10
GRID_DISTANCE_WARNING_KM = 20.0
STATION_LOCATION_WARNING_KM = 5.0
ELEVATION_MISMATCH_WARNING_M = 300.0
TEMPERATURE_SUPPORT_WARNING_C = 1.0
PRECIPITATION_SUPPORT_WARNING_FRACTION = 0.25
PRECIPITATION_NEGATIVE_TOLERANCE_M_PER_DAY = 1.0e-7
EXPECTED_MONTHS = 240
OVERWRITE = False

TEMPERATURE_NAMES = ("t2m", "2m_temperature", "temperature_2m")
PRECIPITATION_NAMES = ("tp", "total_precipitation")
GEOPOTENTIAL_NAMES = ("z", "geopotential", "surface_geopotential")
LAND_MASK_NAMES = ("lsm", "land_sea_mask")
TIME_NAMES = ("valid_time", "time", "date")
LATITUDE_NAMES = ("latitude", "lat")
LONGITUDE_NAMES = ("longitude", "lon")


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_DATA_DIR = (
    _PROJECT_ROOT / "outputs" / "gnip-isotope-clustering" / "data"
)
_DEFAULT_ERA5_DIR = _DEFAULT_DATA_DIR / "era5"
_DEFAULT_GNIP_FILE = _DEFAULT_DATA_DIR / "gnip_us_canada_monthly_o18.csv"
_DEFAULT_TEMPERATURE_FILE = (
    _DEFAULT_ERA5_DIR
    / "era5-gnip-domain-temperature-monthly-2001-2020.nc"
)
_DEFAULT_PRECIPITATION_FILE = (
    _DEFAULT_ERA5_DIR
    / "era5-gnip-domain-precipitation-monthly-2001-2020.nc"
)
_DEFAULT_STATIC_FILE = _DEFAULT_ERA5_DIR / "era5-gnip-domain-static.nc"
_PRIMARY_FILENAME = "gnip_monthly_normalized.csv"
_TEMPERATURE_THREE_BY_THREE_FILENAME = "gnip_monthly_temp_3x3.csv"
_PRECIPITATION_THREE_BY_THREE_FILENAME = "gnip_monthly_precip_3x3.csv"
_THREE_BY_THREE_FILENAME = "gnip_monthly_3x3.csv"
_LAPSE_ADJUSTED_FILENAME = "gnip_monthly_lapse_adjusted.csv"
_DIAGNOSTICS_FILENAME = "era5_gnip_support_diagnostics.csv"


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


def find_name(
    container: xr.Dataset | xr.DataArray,
    names: Sequence[str],
) -> str:
    """候補名からデータに存在する変数・座標名を返す。"""
    if isinstance(container, xr.Dataset):
        available = set(container.variables) | set(container.dims)
    else:
        available = set(container.coords) | set(container.dims)
    for name in names:
        if name in available:
            return name
    raise ValueError(
        "必要な名前がありません。"
        f"候補={', '.join(names)}、実データ={', '.join(sorted(available))}"
    )


def normalize_longitude(data: xr.Dataset) -> xr.Dataset:
    """経度を-180〜180度へ変換して昇順に並べる。"""
    longitude_name = find_name(data, LONGITUDE_NAMES)
    longitude = data[longitude_name]
    if float(longitude.max()) > 180.0:
        normalized = ((longitude + 180.0) % 360.0) - 180.0
        data = data.assign_coords({longitude_name: normalized})
    return data.sortby(longitude_name)


def haversine_km(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    """2地点間の大円距離をkmで返す。"""
    lat_1 = math.radians(latitude_1)
    lat_2 = math.radians(latitude_2)
    delta_lat = lat_2 - lat_1
    delta_lon = math.radians(longitude_2 - longitude_1)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat_1)
        * math.cos(lat_2)
        * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(value))


def select_eligible_stations(data: pd.DataFrame) -> list[str]:
    """緩和後の被覆条件を満たす地点IDを返す。"""
    eligible: list[str] = []
    for station_id, station in data.groupby("station_id", sort=True):
        station = station.loc[
            pd.to_numeric(station["delta18o"], errors="coerce").notna()
        ]
        dates = pd.to_datetime(station["date"])
        n_months = dates.dt.to_period("M").nunique()
        n_years = dates.dt.year.nunique()
        n_calendar_months = dates.dt.month.nunique()
        if (
            n_months >= MIN_OBSERVATIONS
            and n_years >= MIN_YEARS
            and n_calendar_months >= MIN_DISTINCT_CALENDAR_MONTHS
        ):
            eligible.append(str(station_id))
    return eligible


def data_array_from_dataset(
    data: xr.Dataset,
    names: Sequence[str],
) -> xr.DataArray:
    """候補名からDataArrayを取得する。"""
    variable_name = find_name(data, names)
    return data[variable_name].squeeze(drop=True)


def nearest_index(coordinate: xr.DataArray, value: float) -> int:
    """1次元座標で指定値に最も近い添字を返す。"""
    values = np.asarray(coordinate.values, dtype=float)
    return int(np.nanargmin(np.abs(values - value)))


def require_same_spatial_grid(
    first: xr.DataArray,
    second: xr.DataArray,
    label: str,
) -> None:
    """2変数の緯度・経度格子が同一であることを確認する。"""
    first_latitude = np.asarray(
        first[find_name(first, LATITUDE_NAMES)].values,
        dtype=float,
    )
    second_latitude = np.asarray(
        second[find_name(second, LATITUDE_NAMES)].values,
        dtype=float,
    )
    first_longitude = np.asarray(
        first[find_name(first, LONGITUDE_NAMES)].values,
        dtype=float,
    )
    second_longitude = np.asarray(
        second[find_name(second, LONGITUDE_NAMES)].values,
        dtype=float,
    )
    if (
        first_latitude.shape != second_latitude.shape
        or first_longitude.shape != second_longitude.shape
        or not np.allclose(first_latitude, second_latitude, atol=1.0e-8)
        or not np.allclose(first_longitude, second_longitude, atol=1.0e-8)
    ):
        raise ValueError(f"{label}の緯度・経度格子が一致しません。")


def require_same_monthly_axis(
    first: xr.DataArray,
    second: xr.DataArray,
) -> None:
    """気温・降水量が2001〜2020年の同じ240か月を持つか確認する。"""
    first_time = find_name(first, TIME_NAMES)
    second_time = find_name(second, TIME_NAMES)
    first_months = pd.to_datetime(first[first_time].values).to_period("M")
    second_months = pd.to_datetime(second[second_time].values).to_period("M")
    if (
        len(first_months) != EXPECTED_MONTHS
        or len(first_months.unique()) != EXPECTED_MONTHS
        or first_months[0] != pd.Period("2001-01", freq="M")
        or first_months[-1] != pd.Period("2020-12", freq="M")
        or not np.array_equal(first_months, second_months)
    ):
        raise ValueError(
            "ERA5気温・降水量の年月軸が2001-01〜2020-12の"
            "同一240か月ではありません。"
        )


def validate_era5_metadata(
    temperature: xr.DataArray,
    precipitation: xr.DataArray,
    geopotential: xr.DataArray,
    land_mask: xr.DataArray,
) -> None:
    """ERA5変数の単位と月平均蓄積の意味を確認する。"""
    if str(temperature.attrs.get("units", "")).strip() != "K":
        raise ValueError("ERA5気温の単位がKではありません。")
    if str(precipitation.attrs.get("units", "")).strip() != "m":
        raise ValueError("ERA5降水量の単位がmではありません。")
    step_type = str(
        precipitation.attrs.get(
            "GRIB_stepType",
            precipitation.attrs.get("stepType", ""),
        )
    )
    if step_type and step_type != "avgad":
        raise ValueError(
            "ERA5降水量が日平均蓄積量ではありません: "
            f"stepType={step_type}"
        )
    geopotential_units = str(geopotential.attrs.get("units", ""))
    if "m**2" not in geopotential_units and "m2" not in geopotential_units:
        raise ValueError("ERA5地表ジオポテンシャルの単位を確認できません。")
    land_min = float(land_mask.min(skipna=True))
    land_max = float(land_mask.max(skipna=True))
    if land_min < -1.0e-6 or land_max > 1.0 + 1.0e-6:
        raise ValueError("ERA5陸海マスクが0〜1の範囲外です。")


def three_by_three_mean(
    data: xr.DataArray,
    latitude_name: str,
    longitude_name: str,
    latitude_index: int,
    longitude_index: int,
) -> xr.DataArray:
    """最近傍格子を中心とする最大3×3格子の平均を返す。"""
    latitude_slice = slice(
        max(0, latitude_index - 1),
        min(data.sizes[latitude_name], latitude_index + 2),
    )
    longitude_slice = slice(
        max(0, longitude_index - 1),
        min(data.sizes[longitude_name], longitude_index + 2),
    )
    return data.isel(
        {
            latitude_name: latitude_slice,
            longitude_name: longitude_slice,
        }
    ).mean(dim=[latitude_name, longitude_name], skipna=True)


def monthly_precipitation_mm(data: xr.DataArray) -> xr.DataArray:
    """ERA5の日平均降水量m/dayを月降水量mmへ変換する。"""
    minimum = float(data.min(skipna=True))
    if minimum < -PRECIPITATION_NEGATIVE_TOLERANCE_M_PER_DAY:
        raise ValueError(
            "ERA5降水量に許容範囲を超える負値があります: "
            f"{minimum}"
        )
    data = data.clip(min=0.0)
    time_name = find_name(data, TIME_NAMES)
    times = pd.to_datetime(data[time_name].values)
    days = xr.DataArray(
        np.asarray(
            [calendar.monthrange(time.year, time.month)[1] for time in times],
            dtype=float,
        ),
        coords={time_name: data[time_name]},
        dims=[time_name],
    )
    return data * 1000.0 * days


def series_to_month_table(
    data: xr.DataArray,
    value_name: str,
) -> pd.DataFrame:
    """時間DataArrayを月初日と値の表へ変換する。"""
    time_name = find_name(data, TIME_NAMES)
    dates = (
        pd.to_datetime(data[time_name].values)
        .to_period("M")
        .to_timestamp()
    )
    return pd.DataFrame(
        {
            "date": dates,
            value_name: np.asarray(data.values, dtype=float),
        }
    )


def comparison_metrics(
    observed: pd.Series,
    reanalysis: pd.Series,
    prefix: str,
) -> dict[str, float | int]:
    """点観測と格子値の一致度を代表性診断として要約する。"""
    paired = pd.DataFrame(
        {
            "observed": pd.to_numeric(observed, errors="coerce"),
            "reanalysis": pd.to_numeric(reanalysis, errors="coerce"),
        }
    ).dropna()
    result: dict[str, float | int] = {f"{prefix}_n_pairs": len(paired)}
    if paired.empty:
        result.update(
            {
                f"{prefix}_era5_minus_gnip_bias": np.nan,
                f"{prefix}_mae": np.nan,
                f"{prefix}_correlation": np.nan,
            }
        )
        return result
    difference = paired["reanalysis"] - paired["observed"]
    result[f"{prefix}_era5_minus_gnip_bias"] = float(difference.mean())
    result[f"{prefix}_mae"] = float(difference.abs().mean())
    result[f"{prefix}_correlation"] = (
        float(paired["reanalysis"].corr(paired["observed"]))
        if len(paired) >= 3
        else np.nan
    )
    return result


def attach_predictors(
    gnip_path: Path,
    temperature_path: Path,
    precipitation_path: Path,
    static_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """GNIP地点・月へERA5の複数空間支持の予測変数を付与する。"""
    gnip = pd.read_csv(gnip_path, parse_dates=["date"])
    eligible = select_eligible_stations(gnip)
    gnip = gnip.loc[
        gnip["station_id"].isin(eligible)
        & pd.to_numeric(gnip["delta18o"], errors="coerce").notna()
    ].copy()
    if len(eligible) < 10:
        raise ValueError(
            "ERA5付与対象のGNIP地点が10未満です。抽出条件を確認してください。"
        )

    with (
        xr.open_dataset(temperature_path) as temperature_dataset,
        xr.open_dataset(precipitation_path) as precipitation_dataset,
        xr.open_dataset(static_path) as static_dataset,
    ):
        temperature_dataset = normalize_longitude(temperature_dataset)
        precipitation_dataset = normalize_longitude(precipitation_dataset)
        static_dataset = normalize_longitude(static_dataset)
        temperature = data_array_from_dataset(
            temperature_dataset,
            TEMPERATURE_NAMES,
        )
        precipitation = monthly_precipitation_mm(
            data_array_from_dataset(
                precipitation_dataset,
                PRECIPITATION_NAMES,
            )
        )
        require_same_spatial_grid(
            temperature,
            precipitation,
            "ERA5気温と降水量",
        )
        require_same_monthly_axis(temperature, precipitation)
        geopotential = data_array_from_dataset(
            static_dataset,
            GEOPOTENTIAL_NAMES,
        )
        land_mask = data_array_from_dataset(
            static_dataset,
            LAND_MASK_NAMES,
        )
        validate_era5_metadata(
            temperature,
            data_array_from_dataset(
                precipitation_dataset,
                PRECIPITATION_NAMES,
            ),
            geopotential,
            land_mask,
        )
        require_same_spatial_grid(
            geopotential,
            land_mask,
            "ERA5地表ジオポテンシャルと陸海マスク",
        )
        latitude_name = find_name(temperature, LATITUDE_NAMES)
        longitude_name = find_name(temperature, LONGITUDE_NAMES)
        static_latitude_name = find_name(geopotential, LATITUDE_NAMES)
        static_longitude_name = find_name(geopotential, LONGITUDE_NAMES)
        land_latitude_name = find_name(land_mask, LATITUDE_NAMES)
        land_longitude_name = find_name(land_mask, LONGITUDE_NAMES)

        records: list[pd.DataFrame] = []
        diagnostics: list[dict[str, object]] = []
        for station_id, station in gnip.groupby("station_id", sort=True):
            latitude = float(station["latitude"].median())
            longitude = float(station["longitude"].median())
            station_location_spread = max(
                haversine_km(
                    float(row.latitude),
                    float(row.longitude),
                    latitude,
                    longitude,
                )
                for row in station[["latitude", "longitude"]].itertuples(
                    index=False
                )
            )
            latitude_index = nearest_index(
                temperature[latitude_name],
                latitude,
            )
            longitude_index = nearest_index(
                temperature[longitude_name],
                longitude,
            )
            grid_latitude = float(
                temperature[latitude_name].isel(
                    {latitude_name: latitude_index}
                )
            )
            grid_longitude = float(
                temperature[longitude_name].isel(
                    {longitude_name: longitude_index}
                )
            )
            nearest_temperature = temperature.isel(
                {
                    latitude_name: latitude_index,
                    longitude_name: longitude_index,
                }
            ) - 273.15
            average_temperature = (
                three_by_three_mean(
                    temperature,
                    latitude_name,
                    longitude_name,
                    latitude_index,
                    longitude_index,
                )
                - 273.15
            )
            nearest_precipitation = precipitation.isel(
                {
                    latitude_name: latitude_index,
                    longitude_name: longitude_index,
                }
            )
            average_precipitation = three_by_three_mean(
                precipitation,
                latitude_name,
                longitude_name,
                latitude_index,
                longitude_index,
            )
            static_latitude_index = nearest_index(
                geopotential[static_latitude_name],
                latitude,
            )
            static_longitude_index = nearest_index(
                geopotential[static_longitude_name],
                longitude,
            )
            grid_elevation = float(
                geopotential.isel(
                    {
                        static_latitude_name: static_latitude_index,
                        static_longitude_name: static_longitude_index,
                    }
                )
                / GEOPOTENTIAL_GRAVITY
            )
            grid_land_fraction = float(
                land_mask.isel(
                    {
                        land_latitude_name: static_latitude_index,
                        land_longitude_name: static_longitude_index,
                    }
                )
            )
            station_elevation = float(station["elevation_m"].median())
            elevation_difference = station_elevation - grid_elevation
            lapse_temperature = nearest_temperature - (
                LAPSE_RATE_C_PER_KM * elevation_difference / 1000.0
            )
            predictor_table = (
                series_to_month_table(
                    nearest_temperature,
                    "era5_temp_c_nearest",
                )
                .merge(
                    series_to_month_table(
                        average_temperature,
                        "era5_temp_c_3x3",
                    ),
                    on="date",
                    validate="one_to_one",
                )
                .merge(
                    series_to_month_table(
                        lapse_temperature,
                        "era5_temp_c_lapse_adjusted",
                    ),
                    on="date",
                    validate="one_to_one",
                )
                .merge(
                    series_to_month_table(
                        nearest_precipitation,
                        "era5_precip_mm_nearest",
                    ),
                    on="date",
                    validate="one_to_one",
                )
                .merge(
                    series_to_month_table(
                        average_precipitation,
                        "era5_precip_mm_3x3",
                    ),
                    on="date",
                    validate="one_to_one",
                )
            )
            merged = station.merge(
                predictor_table,
                on="date",
                how="left",
                validate="many_to_one",
            )
            merged["latitude"] = latitude
            merged["longitude"] = longitude
            merged["elevation_m"] = station_elevation
            merged["era5_grid_latitude"] = grid_latitude
            merged["era5_grid_longitude"] = grid_longitude
            merged["era5_grid_elevation_m"] = grid_elevation
            merged["era5_land_fraction"] = grid_land_fraction
            merged["station_grid_distance_km"] = haversine_km(
                latitude,
                longitude,
                grid_latitude,
                grid_longitude,
            )
            merged["station_minus_grid_elevation_m"] = elevation_difference
            records.append(merged)
            temperature_difference = float(
                np.nanmean(
                    np.abs(
                        merged["era5_temp_c_nearest"]
                        - merged["era5_temp_c_3x3"]
                    )
                )
            )
            precipitation_fraction = float(
                np.nanmean(
                    np.abs(
                        merged["era5_precip_mm_nearest"]
                        - merged["era5_precip_mm_3x3"]
                    )
                    / np.maximum(merged["era5_precip_mm_nearest"], 1.0)
                )
            )
            auxiliary_metrics: dict[str, float | int] = {}
            if "gnip_temp_c" in merged:
                auxiliary_metrics.update(
                    comparison_metrics(
                        merged["gnip_temp_c"],
                        merged["era5_temp_c_nearest"],
                        "temperature_c",
                    )
                )
            if "gnip_precip_mm" in merged:
                auxiliary_metrics.update(
                    comparison_metrics(
                        merged["gnip_precip_mm"],
                        merged["era5_precip_mm_nearest"],
                        "precipitation_mm",
                    )
                )
            diagnostics.append(
                {
                    "station_id": station_id,
                    "country_code": station["country_code"].iloc[0],
                    "site_name": station["site_name"].iloc[0],
                    "n_months": len(station),
                    "station_latitude": latitude,
                    "station_longitude": longitude,
                    "station_elevation_m": station_elevation,
                    "era5_grid_latitude": grid_latitude,
                    "era5_grid_longitude": grid_longitude,
                    "era5_grid_elevation_m": grid_elevation,
                    "era5_land_fraction": grid_land_fraction,
                    "station_grid_distance_km": haversine_km(
                        latitude,
                        longitude,
                        grid_latitude,
                        grid_longitude,
                    ),
                    "source_station_location_spread_km": (
                        station_location_spread
                    ),
                    "station_minus_grid_elevation_m": elevation_difference,
                    "mean_abs_temp_nearest_minus_3x3_c": (
                        temperature_difference
                    ),
                    "mean_abs_precip_nearest_minus_3x3_fraction": (
                        precipitation_fraction
                    ),
                    "grid_distance_warning": haversine_km(
                        latitude,
                        longitude,
                        grid_latitude,
                        grid_longitude,
                    )
                    > GRID_DISTANCE_WARNING_KM,
                    "station_location_warning": (
                        station_location_spread
                        > STATION_LOCATION_WARNING_KM
                    ),
                    "elevation_mismatch_warning": (
                        abs(elevation_difference)
                        > ELEVATION_MISMATCH_WARNING_M
                    ),
                    "temperature_support_warning": (
                        temperature_difference
                        > TEMPERATURE_SUPPORT_WARNING_C
                    ),
                    "precipitation_support_warning": (
                        precipitation_fraction
                        > PRECIPITATION_SUPPORT_WARNING_FRACTION
                    ),
                    **auxiliary_metrics,
                }
            )
    combined = pd.concat(records, ignore_index=True)
    predictor_columns = [
        "era5_temp_c_nearest",
        "era5_temp_c_3x3",
        "era5_temp_c_lapse_adjusted",
        "era5_precip_mm_nearest",
        "era5_precip_mm_3x3",
    ]
    if combined[predictor_columns].isna().any().any():
        raise ValueError("ERA5予測変数を対応付けられないGNIP地点・月があります。")
    return combined, pd.DataFrame(diagnostics)


def normalized_variant(
    data: pd.DataFrame,
    temperature_column: str,
    precipitation_column: str,
) -> pd.DataFrame:
    """ERA5支持の組合せを解析用標準列へ変換する。"""
    output = data.copy()
    output["temp_c"] = output[temperature_column]
    output["precip_mm"] = output[precipitation_column]
    return output


def save_outputs(
    data: pd.DataFrame,
    diagnostics: pd.DataFrame,
    output_dir: Path,
) -> None:
    """主解析・因子別感度解析の5表と代表性診断を保存する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    variants = {
        _PRIMARY_FILENAME: (
            "era5_temp_c_nearest",
            "era5_precip_mm_nearest",
        ),
        _TEMPERATURE_THREE_BY_THREE_FILENAME: (
            "era5_temp_c_3x3",
            "era5_precip_mm_nearest",
        ),
        _PRECIPITATION_THREE_BY_THREE_FILENAME: (
            "era5_temp_c_nearest",
            "era5_precip_mm_3x3",
        ),
        _THREE_BY_THREE_FILENAME: (
            "era5_temp_c_3x3",
            "era5_precip_mm_3x3",
        ),
        _LAPSE_ADJUSTED_FILENAME: (
            "era5_temp_c_lapse_adjusted",
            "era5_precip_mm_nearest",
        ),
    }
    for filename, (temperature_column, precipitation_column) in variants.items():
        normalized_variant(
            data,
            temperature_column,
            precipitation_column,
        ).to_csv(
            output_dir / filename,
            index=False,
            float_format="%.8f",
        )
    diagnostics.to_csv(
        output_dir / _DIAGNOSTICS_FILENAME,
        index=False,
        float_format="%.8f",
    )
    print(
        f"ERA5対応地点={data['station_id'].nunique()}, "
        f"地点月={len(data)}, "
        f"代表性警告地点="
        f"{int(diagnostics.filter(like='_warning').any(axis=1).sum())}"
    )


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "GNIP月別δ18Oへ、ERA5最近傍・3×3平均・標高補正の"
            "気温と降水量を付与する"
        )
    )
    parser.add_argument(
        "--gnip-file",
        type=Path,
        default=_DEFAULT_GNIP_FILE,
        help="GNIP月別δ18O CSV",
    )
    parser.add_argument(
        "--temperature-file",
        type=Path,
        default=_DEFAULT_TEMPERATURE_FILE,
        help="ERA5月平均2m気温NetCDF",
    )
    parser.add_argument(
        "--precipitation-file",
        type=Path,
        default=_DEFAULT_PRECIPITATION_FILE,
        help="ERA5月平均総降水量NetCDF",
    )
    parser.add_argument(
        "--static-file",
        type=Path,
        default=_DEFAULT_STATIC_FILE,
        help="ERA5地表ジオポテンシャル・陸海マスクNetCDF",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_DATA_DIR,
        help="正規化CSVと診断表の出力ディレクトリ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """入力を検査し、GNIPへERA5予測変数を付与する。"""
    args = parse_args()
    require_files(
        [
            args.gnip_file,
            args.temperature_file,
            args.precipitation_file,
            args.static_file,
        ],
        "GNIP・ERA5対応付け入力",
    )
    output_paths = [
        args.output_dir / _PRIMARY_FILENAME,
        args.output_dir / _TEMPERATURE_THREE_BY_THREE_FILENAME,
        args.output_dir / _PRECIPITATION_THREE_BY_THREE_FILENAME,
        args.output_dir / _THREE_BY_THREE_FILENAME,
        args.output_dir / _LAPSE_ADJUSTED_FILENAME,
        args.output_dir / _DIAGNOSTICS_FILENAME,
    ]
    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "GNIP・ERA5空間支持対応付け",
        lambda: save_outputs(
            *attach_predictors(
                args.gnip_file,
                args.temperature_file,
                args.precipitation_file,
                args.static_file,
            ),
            args.output_dir,
        ),
    )


if __name__ == "__main__":
    main()
