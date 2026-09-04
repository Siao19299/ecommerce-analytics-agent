from src.ecommerce_agent.day02_data_audit import (
    calculate_sha256,
    get_file_info,
)


def test_get_file_info_for_existing_file(tmp_path):
    file_path = tmp_path / "example.csv"
    content = b"name,value\napple,10\n"
    file_path.write_bytes(content)

    result = get_file_info(str(file_path))

    assert result["exists"] is True
    assert result["size_bytes"] == len(content)
    assert result["suffix"] == ".csv"


def test_get_file_info_for_missing_file(tmp_path):
    file_path = tmp_path / "missing.csv"

    result = get_file_info(str(file_path))

    assert result["exists"] is False
    assert result["size_bytes"] is None
    assert result["suffix"] == ".csv"


def test_calculate_sha256(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_bytes(b"abc")

    result = calculate_sha256(str(file_path))

    expected = (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )

    assert result == expected