"""Step 2: GNIP解析地点のNASA POWER月別気温・降水量を取得して結合する。"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


# ======== 設定 ========

START_YEAR = 2001
END_YEAR = 2020
API_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/monthly/point"
API_PARAMETERS = ("T2M", "PRECTOTCORR")
REQUEST_TIMEOUT_SECONDS = 30
MAX_REQUEST_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 2
OVERWRITE = False


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_DATA_DIR = (
    _PROJECT_ROOT / "outputs" / "gnip-isotope-clustering" / "data"
)
_DEFAULT_COVERAGE_FILE = (
    _DEFAULT_DATA_DIR
    / "gnip_us_canada_station_coverage_2001_2020.csv"
)
_DEFAULT_GNIP_FILE = _DEFAULT_DATA_DIR / "gnip_us_canada_monthly_o18.csv"
_POWER_FILENAME = "nasa_power_gnip_locations_monthly_2001_2020.csv"
_NORMALIZED_FILENAME = "gnip_monthly_nasa_power.csv"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    """UTF-8 CSVを辞書行として読み込む。"""
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    """辞書行をUTF-8 CSVへ保存する。"""
    if not rows:
        raise ValueError(f"保存対象が空です: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def eligible_stations(
    coverage_path: Path,
) -> list[dict[str, str]]:
    """GNIP被覆条件を通過した地点を返す。"""
    rows = [
        row
        for row in read_csv(coverage_path)
        if row["eligible_with_era5_predictors"].lower() == "true"
    ]
    if len(rows) < 10:
        raise ValueError("NASA POWER取得対象のGNIP地点が10未満です。")
    return rows


def request_payload(
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    """NASA POWER月別APIから1地点のJSON応答を取得する。"""
    query = urllib.parse.urlencode(
        {
            "parameters": ",".join(API_PARAMETERS),
            "community": "AG",
            "longitude": longitude,
            "latitude": latitude,
            "format": "JSON",
            "start": START_YEAR,
            "end": END_YEAR,
        }
    )
    url = f"{API_ENDPOINT}?{query}"
    last_error: Exception | None = None
    for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "instant-question-notes/1.0"},
            )
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT_SECONDS,
            ) as response:
                return json.load(response)
        except Exception as error:
            last_error = error
            if attempt < MAX_REQUEST_ATTEMPTS:
                print(
                    f"retry: NASA POWER API {attempt}/"
                    f"{MAX_REQUEST_ATTEMPTS}",
                    flush=True,
                )
                time.sleep(RETRY_WAIT_SECONDS)
    raise RuntimeError(f"NASA POWER API取得に失敗しました: {last_error}")


def validate_payload(payload: dict[str, Any], station_id: str) -> None:
    """POWER応答のデータ源、単位、期間を確認する。"""
    sources = {
        str(source).upper() for source in payload["header"]["sources"]
    }
    if "MERRA2" not in sources:
        raise ValueError(f"{station_id}: MERRA-2応答ではありません。")
    parameters = payload["parameters"]
    if parameters["T2M"]["units"] != "C":
        raise ValueError(f"{station_id}: T2Mの単位が°Cではありません。")
    if parameters["PRECTOTCORR"]["units"] != "mm/day":
        raise ValueError(
            f"{station_id}: PRECTOTCORRの単位がmm/dayではありません。"
        )


def payload_rows(
    station: dict[str, str],
    payload: dict[str, Any],
) -> list[dict[str, object]]:
    """1地点のPOWER応答を月別行へ変換する。"""
    station_id = station["station_id"]
    validate_payload(payload, station_id)
    parameter = payload["properties"]["parameter"]
    temperature = parameter["T2M"]
    precipitation = parameter["PRECTOTCORR"]
    geometry = payload.get("geometry", {}).get("coordinates", [])
    power_elevation = (
        float(geometry[2]) if len(geometry) >= 3 else None
    )
    rows: list[dict[str, object]] = []
    for key in sorted(temperature):
        if len(key) != 6:
            continue
        year = int(key[:4])
        month = int(key[4:])
        if not 1 <= month <= 12:
            continue
        temp_c = float(temperature[key])
        precip_daily_mm = float(precipitation[key])
        if temp_c <= -999.0 or precip_daily_mm <= -999.0:
            raise ValueError(f"{station_id}: {key}に欠測値があります。")
        rows.append(
            {
                "station_id": station_id,
                "country_code": station["country_code"],
                "site_name": station["site_name"],
                "date": f"{year:04d}-{month:02d}-01",
                "latitude": float(station["latitude"]),
                "longitude": float(station["longitude"]),
                "power_elevation_m": power_elevation,
                "temp_c": round(temp_c, 3),
                "precip_mm": round(
                    precip_daily_mm
                    * calendar.monthrange(year, month)[1],
                    3,
                ),
            }
        )
    expected = (END_YEAR - START_YEAR + 1) * 12
    if len(rows) != expected:
        raise ValueError(
            f"{station_id}: POWER年月数が不正です: {len(rows)}"
        )
    return rows


def merge_with_gnip(
    gnip_path: Path,
    power_rows: Sequence[dict[str, object]],
    station_ids: set[str],
) -> list[dict[str, object]]:
    """主解析可能なGNIP地点月へPOWER説明変数を結合する。"""
    power_by_key = {
        (str(row["station_id"]), str(row["date"])): row
        for row in power_rows
    }
    merged: list[dict[str, object]] = []
    for gnip in read_csv(gnip_path):
        station_id = gnip["station_id"]
        if station_id not in station_ids or not gnip["delta18o"].strip():
            continue
        key = (station_id, gnip["date"])
        if key not in power_by_key:
            raise ValueError(f"POWER値がないGNIP地点月です: {key}")
        power = power_by_key[key]
        row: dict[str, object] = dict(gnip)
        row["latitude"] = power["latitude"]
        row["longitude"] = power["longitude"]
        row["nasa_power_temp_c"] = power["temp_c"]
        row["nasa_power_precip_mm"] = power["precip_mm"]
        row["power_elevation_m"] = power["power_elevation_m"]
        row["temp_c"] = power["temp_c"]
        row["precip_mm"] = power["precip_mm"]
        merged.append(row)
    if not merged:
        raise ValueError("NASA POWERと結合できるGNIP地点月がありません。")
    return merged


def download_and_merge(
    coverage_path: Path,
    gnip_path: Path,
    output_dir: Path,
) -> None:
    """19地点のPOWER取得とGNIP結合CSVの保存を実行する。"""
    stations = eligible_stations(coverage_path)
    power_rows: list[dict[str, object]] = []
    for number, station in enumerate(stations, start=1):
        print(
            f"fetch: {number}/{len(stations)} "
            f"{station['station_id']} {station['site_name']}",
            flush=True,
        )
        payload = request_payload(
            float(station["latitude"]),
            float(station["longitude"]),
        )
        power_rows.extend(payload_rows(station, payload))
    station_ids = {station["station_id"] for station in stations}
    normalized = merge_with_gnip(gnip_path, power_rows, station_ids)
    write_csv(output_dir / _POWER_FILENAME, power_rows)
    write_csv(output_dir / _NORMALIZED_FILENAME, normalized)
    print(
        f"POWER地点数={len(stations)}, POWER月別行={len(power_rows)}, "
        f"GNIP結合行={len(normalized)}"
    )


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "GNIP解析地点のNASA POWER月別T2M・PRECTOTCORRを取得し、"
            "寄与解析用CSVへ結合する"
        )
    )
    parser.add_argument(
        "--coverage-file",
        type=Path,
        default=_DEFAULT_COVERAGE_FILE,
        help="GNIP地点被覆CSV",
    )
    parser.add_argument(
        "--gnip-file",
        type=Path,
        default=_DEFAULT_GNIP_FILE,
        help="GNIP月別δ18O CSV",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_DATA_DIR,
        help="POWER月別表とGNIP結合表の出力先",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """POWER取得とGNIP結合を安全に実行する。"""
    args = parse_args()
    require_files(
        [args.coverage_file, args.gnip_file],
        "NASA POWER取得入力",
    )
    output_paths = [
        args.output_dir / _POWER_FILENAME,
        args.output_dir / _NORMALIZED_FILENAME,
    ]
    run_or_skip(
        output_paths,
        args.overwrite or OVERWRITE,
        "GNIP地点NASA POWER月別データ",
        lambda: download_and_merge(
            args.coverage_file,
            args.gnip_file,
            args.output_dir,
        ),
    )


if __name__ == "__main__":
    main()
