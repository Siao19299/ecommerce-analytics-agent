import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    available_dimensions: frozenset[str]


@dataclass(frozen=True)
class MetricCatalog:
    metrics: dict[str, MetricDefinition]
    dimensions: frozenset[str]

    @classmethod
    def from_csv(
        cls,
        metric_dictionary_path: str | Path,
        dimension_dictionary_path: str | Path,
    ) -> "MetricCatalog":
        dimension_rows = _read_csv(dimension_dictionary_path)
        dimension_id_by_name = {
            row["chinese_name"]: row["dimension_id"]
            for row in dimension_rows
        }

        metrics = {}
        for row in _read_csv(metric_dictionary_path):
            dimension_names = row["available_dimensions"].split("|")
            unknown_names = set(dimension_names) - set(dimension_id_by_name)
            if unknown_names:
                names = ", ".join(sorted(unknown_names))
                raise ValueError(f"指标字典包含未知维度：{names}")

            metric_id = row["metric_id"]
            metrics[metric_id] = MetricDefinition(
                metric_id=metric_id,
                available_dimensions=frozenset(
                    dimension_id_by_name[name] for name in dimension_names
                ),
            )

        return cls(
            metrics=metrics,
            dimensions=frozenset(dimension_id_by_name.values()),
        )


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))
