"""Step 2: GNIP対象地点を覆うERA5月平均気温・降水量・地形を取得する。"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


# ======== 設定 ========

GNIP_DOMAIN_AREA = (65.0, -125.0, 24.0, -54.0)
OVERWRITE = False


# ======== パス ========

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
_BASE_SCRIPT = _SCRIPT_DIR / "download-era5-conus-monthly.py"
_DEFAULT_OUTPUT_DIR = (
    _PROJECT_ROOT
    / "outputs"
    / "gnip-isotope-clustering"
    / "data"
    / "era5"
)
_MONTHLY_ARCHIVE_FILENAME = "era5-gnip-domain-monthly-2001-2020.zip"
_TEMPERATURE_FILENAME = (
    "era5-gnip-domain-temperature-monthly-2001-2020.nc"
)
_PRECIPITATION_FILENAME = (
    "era5-gnip-domain-precipitation-monthly-2001-2020.nc"
)
_STATIC_FILENAME = "era5-gnip-domain-static.nc"


def require_files(paths: list[Path], label: str) -> None:
    """必要な入力ファイルが存在するか確認する。"""
    missing = [path for path in paths if not path.is_file()]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"{label}が見つかりません: {joined}")


def load_base_module() -> ModuleType:
    """CONUS取得スクリプトを共通実装として読み込む。"""
    require_files([_BASE_SCRIPT], "ERA5取得共通実装")
    specification = importlib.util.spec_from_file_location(
        "_era5_download_base",
        _BASE_SCRIPT,
    )
    if specification is None or specification.loader is None:
        raise ImportError(f"共通実装を読み込めません: {_BASE_SCRIPT}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    module.CONUS_AREA = GNIP_DOMAIN_AREA
    return module


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "解析対象のGNIP 19地点を覆う領域から、2001〜2020年の"
            "ERA5月平均気温・降水量と静的地形を取得する"
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="ERA5 NetCDFとZIPの出力ディレクトリ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力を上書きする",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="CDS要求を表示し、取得しない",
    )
    parser.add_argument(
        "--static-source",
        choices=("auto", "monthly", "hourly"),
        default="auto",
        help="静的変数の取得元",
    )
    return parser.parse_args()


def main() -> None:
    """GNIP対象領域のERA5データを表示または取得する。"""
    args = parse_args()
    base = load_base_module()
    archive_path = args.output_dir / _MONTHLY_ARCHIVE_FILENAME
    temperature_path = args.output_dir / _TEMPERATURE_FILENAME
    precipitation_path = args.output_dir / _PRECIPITATION_FILENAME
    static_path = args.output_dir / _STATIC_FILENAME
    base.validate_settings()
    base.validate_output_paths(
        archive_path,
        temperature_path,
        precipitation_path,
        static_path,
    )
    monthly_request = base.build_monthly_request()
    base.print_request(
        "GNIP対象領域ERA5月平均気候データ",
        base.DATASET_MONTHLY,
        monthly_request,
        archive_path,
    )
    if args.static_source in {"auto", "monthly"}:
        base.print_request(
            "GNIP対象領域ERA5静的地形データ（月平均）",
            base.DATASET_MONTHLY,
            base.build_static_monthly_request(),
            static_path,
        )
    if args.static_source in {"auto", "hourly"}:
        base.print_request(
            "GNIP対象領域ERA5静的地形データ（通常単層）",
            base.DATASET_HOURLY,
            base.build_static_hourly_request(),
            static_path,
        )
    if args.dry_run:
        print("dry-run: CDSへの要求は送信していません。")
        return

    base.require_cds_credentials()
    cdsapi = base.import_cdsapi()
    client = cdsapi.Client()
    overwrite = OVERWRITE or args.overwrite
    base.download_monthly(
        client,
        archive_path,
        temperature_path,
        precipitation_path,
        overwrite,
    )
    base.download_static(
        client,
        static_path,
        overwrite,
        args.static_source,
    )


if __name__ == "__main__":
    main()
