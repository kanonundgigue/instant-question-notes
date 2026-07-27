"""Step 1: GNIPから米国・カナダの月別δ18Oを抽出する。"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from statistics import median

import pyarrow.compute as pc
import pyarrow as pa
import pyarrow.parquet as pq


# ======== 設定 ========

START_YEAR = 2001
END_YEAR = 2020
MIN_OBSERVATIONS = 36
MIN_YEARS = 3
MIN_MONTHS_PER_CALENDAR_MONTH = 1
MIN_DISTINCT_CALENDAR_MONTHS = 10
OVERWRITE = False

INPUT_COLUMNS = (
    "Sample UID",
    "Sample Site Name",
    "Latitude",
    "Longitude",
    "Altitude",
    "WMO Code",
    "Country ISO Code",
    "Category Group Name",
    "Sample Date",
    "O18",
    "Precipitation",
    "TempAir",
)


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_WORK_ROOT = _PROJECT_ROOT.parent
_DEFAULT_INPUT_FILE = _WORK_ROOT / "GNIP" / "data" / "gnip_all.parquet"
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT / "outputs" / "gnip-isotope-clustering" / "data"
)
_SOURCE_MONTHLY_FILENAME = "gnip_us_canada_monthly_o18.csv"
_COVERAGE_FILENAME = "gnip_us_canada_station_coverage_2001_2020.csv"


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


def is_target_region(
    country_code: str,
    latitude: float,
    longitude: float,
) -> bool:
    """ハワイを除く米国またはカナダの地点か判定する。"""
    if country_code == "CA":
        return 40.0 <= latitude <= 84.0 and -142.0 <= longitude <= -50.0
    if country_code == "US":
        conus = 24.0 <= latitude <= 50.0 and -125.0 <= longitude <= -66.0
        alaska = (
            51.0 <= latitude <= 72.0
            and -170.0 <= longitude <= -129.0
        )
        return conus or alaska
    return False


def fallback_station_id(site_name: str) -> str:
    """WMOコードがない地点用の安定した地点IDを作る。"""
    slug = re.sub(r"[^0-9A-Za-z]+", "_", site_name.strip()).strip("_")
    if not slug:
        raise ValueError("WMOコードも地点名もない行があります。")
    return f"SITE_{slug.upper()}"


def station_id_for_row(row: dict[str, object]) -> str:
    """WMOコードを優先して地点IDを返す。"""
    country_code = str(row.get("Country ISO Code") or "").strip()
    wmo_code = str(row.get("WMO Code") or "").strip()
    if wmo_code:
        return f"{country_code}_{wmo_code}"
    fallback = fallback_station_id(str(row.get("Sample Site Name") or ""))
    return f"{country_code}_{fallback}"


def load_target_rows(
    input_path: Path,
    start_year: int,
    end_year: int,
) -> list[dict[str, object]]:
    """GNIP表から対象期間の米国・カナダ降水試料を読み込む。"""
    table = pq.read_table(input_path, columns=list(INPUT_COLUMNS))
    base_mask = pc.and_(
        pc.is_in(
            table["Country ISO Code"],
            value_set=pa.array(["US", "CA"]),
        ),
        pc.equal(table["Category Group Name"], "Precipitation"),
    )
    rows = table.filter(base_mask).to_pylist()
    start_date = date(start_year, 1, 1)
    end_date = date(end_year, 12, 31)
    selected: list[dict[str, object]] = []
    for row in rows:
        sample_date = row["Sample Date"]
        latitude = row["Latitude"]
        longitude = row["Longitude"]
        country_code = str(row["Country ISO Code"] or "")
        if (
            sample_date is None
            or latitude is None
            or longitude is None
            or not start_date <= sample_date <= end_date
            or not is_target_region(
                country_code,
                float(latitude),
                float(longitude),
            )
            or row["O18"] is None
        ):
            continue
        row["station_id"] = station_id_for_row(row)
        selected.append(row)
    if not selected:
        raise ValueError("指定条件を満たすGNIP観測がありません。")
    return selected


def weighted_mean(
    rows: Sequence[dict[str, object]],
    value_column: str,
    weight_column: str,
) -> float | None:
    """欠測を除き、正の重みで加重平均する。"""
    pairs = [
        (float(row[value_column]), float(row[weight_column]))
        for row in rows
        if row[value_column] is not None
        and row[weight_column] is not None
        and float(row[weight_column]) > 0.0
    ]
    if not pairs:
        return None
    weight_sum = sum(weight for _, weight in pairs)
    return sum(value * weight for value, weight in pairs) / weight_sum


def aggregate_monthly(
    rows: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """同一地点・月のδ18O試料を月別値へまとめる。"""
    groups: defaultdict[
        tuple[str, int, int],
        list[dict[str, object]],
    ] = defaultdict(list)
    for row in rows:
        sample_date = row["Sample Date"]
        if not isinstance(sample_date, date):
            raise TypeError("Sample Dateが日付型ではありません。")
        groups[
            (
                str(row["station_id"]),
                sample_date.year,
                sample_date.month,
            )
        ].append(row)

    monthly: list[dict[str, object]] = []
    for (station_id, year, month), group in sorted(groups.items()):
        arithmetic_reference = (
            sum(float(row["O18"]) for row in group) / len(group)
            if len(group) > 1
            else None
        )
        positive_precipitation = [
            row
            for row in group
            if row["Precipitation"] is not None
            and float(row["Precipitation"]) > 0.0
        ]
        if len(group) == 1:
            delta18o = float(group[0]["O18"])
            isotope_aggregation = "single_sample"
        elif len(positive_precipitation) == len(group):
            delta18o = weighted_mean(group, "O18", "Precipitation")
            isotope_aggregation = "gnip_precipitation_weighted"
        else:
            delta18o = None
            isotope_aggregation = "arithmetic_reference_only"
        precipitation = (
            sum(float(row["Precipitation"]) for row in positive_precipitation)
            if positive_precipitation
            else None
        )
        temperature = weighted_mean(
            positive_precipitation,
            "TempAir",
            "Precipitation",
        )
        first = group[0]
        monthly.append(
            {
                "station_id": station_id,
                "country_code": str(first["Country ISO Code"]),
                "site_name": str(first["Sample Site Name"]),
                "date": f"{year:04d}-{month:02d}-01",
                "latitude": median(float(row["Latitude"]) for row in group),
                "longitude": median(float(row["Longitude"]) for row in group),
                "elevation_m": (
                    median(
                        float(row["Altitude"])
                        for row in group
                        if row["Altitude"] is not None
                    )
                    if any(row["Altitude"] is not None for row in group)
                    else None
                ),
                "n_samples": len(group),
                "gnip_temp_c": temperature,
                "gnip_precip_mm": precipitation,
                "delta18o": delta18o,
                "delta18o_arithmetic_reference": arithmetic_reference,
                "isotope_aggregation": isotope_aggregation,
                "is_primary_delta18o": delta18o is not None,
                "gnip_precipitation_complete": (
                    len(positive_precipitation) == len(group)
                ),
            }
        )
    return monthly


def build_coverage(
    monthly: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """地点別の月数・年数・解析可否を集計する。"""
    stations: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for row in monthly:
        stations[str(row["station_id"])].append(row)

    records: list[dict[str, object]] = []
    for station_id, rows in sorted(stations.items()):
        primary_rows = [
            row for row in rows if row["delta18o"] is not None
        ]
        calendar_counts: defaultdict[int, int] = defaultdict(int)
        for row in primary_rows:
            calendar_counts[int(str(row["date"])[5:7])] += 1
        n_years = len({str(row["date"])[:4] for row in primary_rows})
        minimum_calendar_count = (
            min(calendar_counts.values()) if calendar_counts else 0
        )
        eligible = (
            len(primary_rows) >= MIN_OBSERVATIONS
            and n_years >= MIN_YEARS
            and len(calendar_counts) >= MIN_DISTINCT_CALENDAR_MONTHS
            and minimum_calendar_count >= MIN_MONTHS_PER_CALENDAR_MONTH
        )
        first = rows[0]
        records.append(
            {
                "station_id": station_id,
                "country_code": first["country_code"],
                "site_name": first["site_name"],
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "n_months_o18": len(primary_rows),
                "n_months_o18_all": len(rows),
                "n_months_arithmetic_reference_only": sum(
                    row["isotope_aggregation"]
                    == "arithmetic_reference_only"
                    for row in rows
                ),
                "n_years_o18": n_years,
                "n_distinct_calendar_months": len(calendar_counts),
                "minimum_count_per_calendar_month": minimum_calendar_count,
                "start_month": (
                    min(str(row["date"]) for row in primary_rows)
                    if primary_rows
                    else ""
                ),
                "end_month": (
                    max(str(row["date"]) for row in primary_rows)
                    if primary_rows
                    else ""
                ),
                "eligible_with_era5_predictors": eligible,
            }
        )
    return sorted(
        records,
        key=lambda row: (
            not bool(row["eligible_with_era5_predictors"]),
            -int(row["n_months_o18"]),
            str(row["station_id"]),
        ),
    )


def write_csv(
    path: Path,
    rows: Sequence[dict[str, object]],
) -> None:
    """辞書行をUTF-8 CSVへ保存する。"""
    if not rows:
        raise ValueError(f"保存対象が空です: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prepare_data(
    input_path: Path,
    output_dir: Path,
    start_year: int,
    end_year: int,
) -> None:
    """抽出・月集約・被覆集計を実行してCSVを保存する。"""
    rows = load_target_rows(input_path, start_year, end_year)
    monthly = aggregate_monthly(rows)
    coverage = build_coverage(monthly)
    write_csv(output_dir / _SOURCE_MONTHLY_FILENAME, monthly)
    write_csv(output_dir / _COVERAGE_FILENAME, coverage)
    eligible = sum(
        bool(row["eligible_with_era5_predictors"]) for row in coverage
    )
    print(
        f"GNIP抽出行={len(rows)}, 月別行={len(monthly)}, "
        f"地点数={len(coverage)}, 寄与解析可能地点={eligible}"
    )


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "GNIPワイドParquetから米国（ハワイ除外）・カナダの月別"
            "δ18Oを抽出し、ERA5対応付け用CSVと地点被覆表を作る"
        )
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=_DEFAULT_INPUT_FILE,
        help="GNIP分析用ワイドParquet",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="正規化CSVと被覆表の出力ディレクトリ",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=START_YEAR,
        help="抽出開始年",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=END_YEAR,
        help="抽出終了年",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """引数を検査してGNIP月別データを作成する。"""
    args = parse_args()
    if args.start_year > args.end_year:
        raise ValueError("start-yearはend-year以下にしてください。")
    require_files([args.input_file], "GNIP月別化入力")
    output_paths = [
        args.output_dir / _SOURCE_MONTHLY_FILENAME,
        args.output_dir / _COVERAGE_FILENAME,
    ]
    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "GNIP米国・カナダ月別データ",
        lambda: prepare_data(
            args.input_file,
            args.output_dir,
            args.start_year,
            args.end_year,
        ),
    )


if __name__ == "__main__":
    main()
