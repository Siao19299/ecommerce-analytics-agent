import csv
import hashlib
from pathlib import Path


def calculate_sha256(path: str) -> str:
    """分块读取文件并返回 SHA-256 十六进制校验值。"""
    sha256 = hashlib.sha256()

    with open(path, mode="rb") as file:
        while True:
            chunk = file.read(8192)

            if not chunk:
                break

            sha256.update(chunk)

    return sha256.hexdigest()


def get_file_info(
    path: str,
) -> dict[str, bool | int | str | None]:
    file_path = Path(path)

    if not file_path.is_file():
        return {
            "exists": False,
            "size_bytes": None,
            "suffix": file_path.suffix,
        }

    return {
        "exists": True,
        "size_bytes": file_path.stat().st_size,
        "suffix": file_path.suffix,
    }


def inspect_csv(path: str) -> tuple[list[str], int]:
    """返回 CSV 字段名和数据行数。"""
    with open(path, mode="r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)

        try:
            columns = next(reader)
        except StopIteration:
            return [], 0

        row_count = sum(1 for _ in reader)

    return columns, row_count


def build_file_manifest(
    raw_dir: str,
    output_path: str,
    source_url: str,
    download_date: str,
) -> list[dict[str, str | int | None]]:
    """检查原始文件并将清单保存为 CSV。"""
    raw_path = Path(raw_dir)
    records = []

    for file_path in sorted(raw_path.iterdir()):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in {".csv", ".zip"}:
            continue

        if file_path.suffix.lower() == ".csv":
            columns, row_count = inspect_csv(str(file_path))
        else:
            columns, row_count = [], None

        record = {
            "file_name": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "row_count": row_count,
            "column_count": len(columns),
            "columns": "|".join(columns),
            "sha256": calculate_sha256(str(file_path)),
            "source_url": source_url,
            "download_date": download_date,
        }
        records.append(record)

    manifest_path = Path(output_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "file_name",
        "size_bytes",
        "row_count",
        "column_count",
        "columns",
        "sha256",
        "source_url",
        "download_date",
    ]

    with open(
        manifest_path,
        mode="w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    return records


if __name__ == "__main__":
    manifest = build_file_manifest(
        raw_dir="data/raw",
        output_path="data/metadata/olist_file_manifest.csv",
        source_url=(
            "https://www.kaggle.com/datasets/"
            "olistbr/brazilian-ecommerce"
        ),
        download_date="2026-09-04",
    )

    print(f"文件清单生成完成，共记录 {len(manifest)} 个文件")