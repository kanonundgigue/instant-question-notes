"""Step 0: NASA POWERから10地点の年月別気候データを取得する。"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


# ======== 設定 ========

START_YEAR = 1991
END_YEAR = 2020
OVERWRITE = False
API_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/monthly/point"
LOCATIONS = {
    "Bangkok": ("Tropical", 13.76, 100.50),
    "Jakarta": ("Tropical", -6.21, 106.85),
    "Kuala Lumpur": ("Tropical", 3.14, 101.69),
    "London": ("Mid-latitude", 51.51, -0.13),
    "Manila": ("Tropical", 14.60, 120.98),
    "New York": ("Mid-latitude", 40.71, -74.01),
    "Paris": ("Mid-latitude", 48.86, 2.35),
    "Seoul": ("Mid-latitude", 37.57, 126.98),
    "Singapore": ("Tropical", 1.35, 103.82),
    "Tokyo": ("Mid-latitude", 35.68, 139.76),
}


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OUTPUT = (
    _SCRIPT_DIR.parent
    / "data"
    / "nasa-power-10-locations-monthly-1991-2020.csv"
)


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


def fetch_location(
    station: str,
    known_group: str,
    latitude: float,
    longitude: float,
) -> list[dict[str, str | float]]:
    """1地点の年月別気温と月降水量をNASA POWERから取得する。"""
    query = urllib.parse.urlencode(
        {
            "parameters": "T2M,PRECTOTCORR",
            "community": "AG",
            "longitude": longitude,
            "latitude": latitude,
            "format": "JSON",
            "start": START_YEAR,
            "end": END_YEAR,
        }
    )
    with urllib.request.urlopen(
        f"{API_ENDPOINT}?{query}",
        timeout=60,
    ) as response:
        payload: dict[str, Any] = json.load(response)

    parameters = payload["properties"]["parameter"]
    temperature = parameters["T2M"]
    precipitation = parameters["PRECTOTCORR"]
    rows: list[dict[str, str | float]] = []

    for key in sorted(temperature):
        if len(key) != 6:
            continue
        year = int(key[:4])
        month = int(key[4:])
        if not 1 <= month <= 12:
            continue

        temp_c = float(temperature[key])
        precip_daily_mm = float(precipitation[key])
        if temp_c <= -999 or precip_daily_mm <= -999:
            raise ValueError(f"{station}の{key}に欠測値があります。")

        rows.append(
            {
                "station": station,
                "known_group": known_group,
                "date": f"{year}-{month:02d}-01",
                "temp_c": round(temp_c, 3),
                "precip_mm": round(
                    precip_daily_mm * calendar.monthrange(year, month)[1],
                    3,
                ),
            }
        )

    expected_rows = (END_YEAR - START_YEAR + 1) * 12
    if len(rows) != expected_rows:
        raise ValueError(
            f"{station}の年月数が不正です: {len(rows)}行"
        )
    return rows


def save_monthly_csv(output_path: Path) -> None:
    """10地点の年月別データを取得してCSVへ保存する。"""
    all_rows: list[dict[str, str | float]] = []
    for station, (known_group, latitude, longitude) in LOCATIONS.items():
        print(f"fetch: {station}")
        all_rows.extend(
            fetch_location(
                station,
                known_group,
                latitude,
                longitude,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "station",
                "known_group",
                "date",
                "temp_c",
                "precip_mm",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(all_rows)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description="NASA POWERの10地点年月別CSVを作成する",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="出力CSVのパス",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    return parser.parse_args()


def main() -> None:
    """10地点のNASA POWERデータを取得する。"""
    args = parse_args()
    run_or_skip(
        args.output,
        OVERWRITE or args.overwrite,
        "NASA POWER年月別CSV",
        lambda: save_monthly_csv(args.output),
    )


if __name__ == "__main__":
    main()
