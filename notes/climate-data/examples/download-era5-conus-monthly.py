"""Step 0: 米国本土のERA5月平均気候データと静的地形データを取得する。"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from zipfile import BadZipFile, ZipFile, ZipInfo


# ======== 設定 ========

DATASET_MONTHLY = "reanalysis-era5-single-levels-monthly-means"
DATASET_HOURLY = "reanalysis-era5-single-levels"
START_YEAR = 2001
END_YEAR = 2020
MONTHS = tuple(f"{month:02d}" for month in range(1, 13))
CONUS_AREA = (50.0, -125.0, 24.0, -66.5)
GRID_DEGREES = 0.25
MONTHLY_VARIABLES = ("2m_temperature", "total_precipitation")
STATIC_VARIABLES = ("geopotential", "land_sea_mask")
STATIC_YEAR = "2001"
STATIC_MONTH = "01"
STATIC_DAY = "01"
STATIC_TIME = "00:00"
OVERWRITE = False
MINIMUM_NETCDF_BYTES = 1_024


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT / "outputs" / "era5-conus-clustering" / "data" / "raw"
)
_MONTHLY_ARCHIVE_FILENAME = "era5-conus-monthly-2001-2020.zip"
_TEMPERATURE_FILENAME = "era5-conus-temperature-monthly-2001-2020.nc"
_PRECIPITATION_FILENAME = "era5-conus-precipitation-monthly-2001-2020.nc"
_STATIC_FILENAME = "era5-conus-static.nc"


def validate_settings() -> None:
    """期間、領域、変数の固定設定が妥当か検査する。"""
    if START_YEAR > END_YEAR:
        raise ValueError("開始年は終了年以前でなければなりません。")
    if len(MONTHS) != 12 or len(set(MONTHS)) != 12:
        raise ValueError("1月から12月までを重複なく指定してください。")
    north, west, south, east = CONUS_AREA
    if not (-90 <= south < north <= 90):
        raise ValueError("領域の緯度指定が不正です。")
    if not (-180 <= west < east <= 180):
        raise ValueError("領域の経度指定が不正です。")
    if not MONTHLY_VARIABLES or not STATIC_VARIABLES:
        raise ValueError("取得変数を1つ以上指定してください。")
    if GRID_DEGREES != 0.25:
        raise ValueError("この解析ではERA5単層データの0.25度格子を使います。")


def validate_output_paths(
    monthly_archive_path: Path,
    temperature_path: Path,
    precipitation_path: Path,
    static_path: Path,
) -> None:
    """ZIPと3つのNetCDF出力が異なるファイルを指すか検査する。"""
    paths = {
        monthly_archive_path,
        temperature_path,
        precipitation_path,
        static_path,
    }
    if len(paths) != 4:
        raise ValueError("ZIPと各NetCDFの出力先は分けてください。")
    if monthly_archive_path.suffix.lower() != ".zip":
        raise ValueError(
            f"月平均アーカイブには.zip拡張子が必要です: "
            f"{monthly_archive_path}"
        )
    for path in (temperature_path, precipitation_path, static_path):
        if path.suffix.lower() != ".nc":
            raise ValueError(f"NetCDF出力には.nc拡張子が必要です: {path}")
    for path in paths:
        if path.exists() and path.is_dir():
            raise IsADirectoryError(f"出力先がディレクトリです: {path}")


def build_monthly_request() -> dict[str, object]:
    """2001〜2020年の月平均気温・降水量リクエストを作る。"""
    return {
        "product_type": ["monthly_averaged_reanalysis"],
        "variable": list(MONTHLY_VARIABLES),
        "year": [str(year) for year in range(START_YEAR, END_YEAR + 1)],
        "month": list(MONTHS),
        "time": ["00:00"],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": list(CONUS_AREA),
        "grid": [GRID_DEGREES, GRID_DEGREES],
    }


def build_static_monthly_request() -> dict[str, object]:
    """月平均データセットから静的変数を1時刻だけ取得する要求を作る。"""
    return {
        "product_type": ["monthly_averaged_reanalysis"],
        "variable": list(STATIC_VARIABLES),
        "year": [STATIC_YEAR],
        "month": [STATIC_MONTH],
        "time": [STATIC_TIME],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": list(CONUS_AREA),
        "grid": [GRID_DEGREES, GRID_DEGREES],
    }


def build_static_hourly_request() -> dict[str, object]:
    """通常のERA5単層データから静的変数を1時刻だけ取得する要求を作る。"""
    return {
        "product_type": ["reanalysis"],
        "variable": list(STATIC_VARIABLES),
        "year": [STATIC_YEAR],
        "month": [STATIC_MONTH],
        "day": [STATIC_DAY],
        "time": [STATIC_TIME],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": list(CONUS_AREA),
        "grid": [GRID_DEGREES, GRID_DEGREES],
    }


def print_request(
    label: str,
    dataset: str,
    request: dict[str, object],
    target: Path,
) -> None:
    """CDSへ送るデータセット名、要求内容、出力先を表示する。"""
    payload = {
        "label": label,
        "dataset": dataset,
        "request": request,
        "target": str(target),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def import_cdsapi() -> ModuleType:
    """cdsapiを読み込み、未導入ならuv用の導入方法を示す。"""
    try:
        return importlib.import_module("cdsapi")
    except ModuleNotFoundError as error:
        raise ModuleNotFoundError(
            "cdsapiが必要です。"
            "`uv run --with 'cdsapi>=0.7.7' python "
            "notes/climate-data/examples/download-era5-conus-monthly.py`"
            "のように実行してください。"
        ) from error


def require_files(paths: list[Path], label: str) -> None:
    """必要なファイルが存在し、空でないことを確認する。"""
    missing = [
        path
        for path in paths
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"{label}が見つかりません: {joined}")


def require_cds_credentials() -> None:
    """CDS API認証ファイルまたは環境変数が設定済みか確認する。"""
    config_path = Path.home() / ".cdsapirc"
    has_environment = bool(
        os.environ.get("CDSAPI_URL") and os.environ.get("CDSAPI_KEY")
    )
    if not config_path.is_file() and not has_environment:
        raise FileNotFoundError(
            "CDS API認証が見つかりません。CDSへログインし、"
            "公式手順に従って~/.cdsapircを作成してください。"
        )


def validate_netcdf(path: Path, label: str) -> None:
    """出力が空でなく、NetCDFとして妥当な先頭識別子を持つか検査する。"""
    require_files([path], label)
    size = path.stat().st_size
    if size < MINIMUM_NETCDF_BYTES:
        raise ValueError(f"{label}が小さすぎます（{size} bytes）: {path}")
    with path.open("rb") as file:
        signature = file.read(8)
    is_netcdf_classic = signature.startswith(b"CDF")
    is_netcdf4 = signature == b"\x89HDF\r\n\x1a\n"
    if not (is_netcdf_classic or is_netcdf4):
        raise ValueError(
            f"{label}はNetCDFではありません。"
            "CDSがZIPを返していないか確認してください: "
            f"{path}"
        )


def inspect_monthly_archive(
    archive_path: Path,
) -> tuple[ZipInfo, ZipInfo]:
    """CDSのZIPを検査し、気温・降水量のメンバーを特定する。"""
    require_files([archive_path], "ERA5月平均ZIP")
    try:
        with ZipFile(archive_path) as archive:
            failed_member = archive.testzip()
            if failed_member is not None:
                raise ValueError(
                    f"ZIPメンバーのCRC検査に失敗しました: {failed_member}"
                )
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir() and member.filename.endswith(".nc")
            ]
    except BadZipFile as error:
        raise ValueError(
            f"ERA5月平均出力が妥当なZIPではありません: {archive_path}"
        ) from error

    temperature_members = [
        member
        for member in members
        if "stepType-avgua" in member.filename
    ]
    precipitation_members = [
        member
        for member in members
        if "stepType-avgad" in member.filename
    ]
    if len(temperature_members) != 1 or len(precipitation_members) != 1:
        member_names = ", ".join(member.filename for member in members)
        raise ValueError(
            "ZIP内のstepType-avgua（気温）とstepType-avgad（降水量）を"
            f"一意に特定できません。メンバー: {member_names}"
        )
    return temperature_members[0], precipitation_members[0]


def extract_netcdf_member(
    archive_path: Path,
    member: ZipInfo,
    output_path: Path,
    overwrite: bool,
    label: str,
) -> None:
    """ZIPメンバーを固定出力名へ安全に展開してNetCDFを検査する。"""
    if output_path.exists() and not overwrite:
        validate_netcdf(output_path, label)
        print(f"skip: {label} ({output_path})")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive_path) as archive:
        with archive.open(member, "r") as source:
            with output_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    validate_netcdf(output_path, label)
    print(f"saved: {label} ({output_path})")


def run_or_skip(
    output_path: Path,
    overwrite: bool,
    label: str,
    action: Callable[[], None],
) -> None:
    """既存の妥当な出力はスキップし、それ以外は処理を実行する。"""
    if output_path.exists() and not overwrite:
        validate_netcdf(output_path, label)
        print(f"skip: {label} ({output_path})")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    action()
    validate_netcdf(output_path, label)
    print(f"saved: {label} ({output_path})")


def retrieve(
    client: object,
    dataset: str,
    request: dict[str, object],
    output_path: Path,
) -> None:
    """CDS APIクライアントで1件の要求を取得する。"""
    retrieve_method = getattr(client, "retrieve", None)
    if not callable(retrieve_method):
        raise TypeError("cdsapi.Clientにretrieveメソッドがありません。")
    retrieve_method(dataset, request, str(output_path))


def download_monthly(
    client: object,
    archive_path: Path,
    temperature_path: Path,
    precipitation_path: Path,
    overwrite: bool,
) -> None:
    """月平均ZIPを取得し、気温と降水量のNetCDFへ安全に展開する。"""
    if (
        temperature_path.exists()
        and precipitation_path.exists()
        and not overwrite
    ):
        validate_netcdf(temperature_path, "ERA5月平均2m気温データ")
        validate_netcdf(precipitation_path, "ERA5月平均総降水量データ")
        print(f"skip: ERA5月平均2m気温データ ({temperature_path})")
        print(f"skip: ERA5月平均総降水量データ ({precipitation_path})")
        return

    request = build_monthly_request()
    if archive_path.exists() and not overwrite:
        inspect_monthly_archive(archive_path)
        print(f"reuse: ERA5月平均ZIP ({archive_path})")
    else:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        retrieve(client, DATASET_MONTHLY, request, archive_path)
        inspect_monthly_archive(archive_path)
        print(f"saved: ERA5月平均ZIP ({archive_path})")

    temperature_member, precipitation_member = inspect_monthly_archive(
        archive_path
    )
    extract_netcdf_member(
        archive_path,
        temperature_member,
        temperature_path,
        overwrite,
        "ERA5月平均2m気温データ",
    )
    extract_netcdf_member(
        archive_path,
        precipitation_member,
        precipitation_path,
        overwrite,
        "ERA5月平均総降水量データ",
    )


def download_static(
    client: object,
    output_path: Path,
    overwrite: bool,
    static_source: str,
) -> None:
    """地表ジオポテンシャルと陸海マスクを1時刻だけ取得する。"""
    monthly_request = build_static_monthly_request()
    hourly_request = build_static_hourly_request()

    def action() -> None:
        """指定した取得元または自動フォールバックで静的変数を取得する。"""
        if static_source == "monthly":
            retrieve(client, DATASET_MONTHLY, monthly_request, output_path)
            return
        if static_source == "hourly":
            retrieve(client, DATASET_HOURLY, hourly_request, output_path)
            return
        try:
            retrieve(client, DATASET_MONTHLY, monthly_request, output_path)
        except Exception as error:
            print(
                "warning: 月平均データセットで静的変数を取得できなかったため、"
                "通常のERA5単層データを試します。"
                f"（{type(error).__name__}: {error}）",
                file=sys.stderr,
            )
            retrieve(client, DATASET_HOURLY, hourly_request, output_path)

    run_or_skip(output_path, overwrite, "ERA5静的地形データ", action)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "ERA5からCONUS外接矩形の2001〜2020年月平均気候データと"
            "静的地形データをNetCDFで取得する"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="出力ディレクトリ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存のNetCDF出力を上書きする",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="CDSへ送る要求を表示し、取得しない",
    )
    parser.add_argument(
        "--static-source",
        choices=("auto", "monthly", "hourly"),
        default="auto",
        help="静的変数の取得元（既定のautoは月平均失敗時に通常単層へ切替）",
    )
    return parser.parse_args()


def main() -> None:
    """要求を検査し、表示またはCDSからの取得を実行する。"""
    args = parse_args()
    monthly_archive_path = args.output_dir / _MONTHLY_ARCHIVE_FILENAME
    temperature_path = args.output_dir / _TEMPERATURE_FILENAME
    precipitation_path = args.output_dir / _PRECIPITATION_FILENAME
    static_path = args.output_dir / _STATIC_FILENAME
    validate_settings()
    validate_output_paths(
        monthly_archive_path,
        temperature_path,
        precipitation_path,
        static_path,
    )

    monthly_request = build_monthly_request()
    static_monthly_request = build_static_monthly_request()
    print_request(
        "ERA5月平均気候データ",
        DATASET_MONTHLY,
        monthly_request,
        monthly_archive_path,
    )
    if args.static_source in {"auto", "monthly"}:
        print_request(
            "ERA5静的地形データ（月平均データセット）",
            DATASET_MONTHLY,
            static_monthly_request,
            static_path,
        )
    if args.static_source in {"auto", "hourly"}:
        print_request(
            "ERA5静的地形データ（通常単層フォールバック）",
            DATASET_HOURLY,
            build_static_hourly_request(),
            static_path,
        )
    if args.dry_run:
        print("dry-run: CDSへの要求は送信していません。")
        return

    require_cds_credentials()
    cdsapi = import_cdsapi()
    client = cdsapi.Client()
    overwrite = OVERWRITE or args.overwrite
    download_monthly(
        client,
        monthly_archive_path,
        temperature_path,
        precipitation_path,
        overwrite,
    )
    download_static(client, static_path, overwrite, args.static_source)


if __name__ == "__main__":
    main()
