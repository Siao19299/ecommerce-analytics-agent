"""Validate the fixed Day 14 dataset and emit coverage/repetition reports."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Iterable

from src.ecommerce_agent.day14_schema import (
    CATEGORY_CASE_PREFIX,
    REQUIRED_FROZEN_CATEGORY_COUNTS,
    DatasetCategory,
    SqlReferenceKind,
    canonical_dataset_path,
    load_dataset,
    verify_dataset_content_sha256,
    validate_named_parameter_contract,
)
from src.ecommerce_agent.day14_single_metric import _sha256_file


HIGH_SIMILARITY_THRESHOLD = 0.86
MAX_HIGH_SIMILARITY_PAIR_RATIO = 0.05
MAX_HIGH_SIMILARITY_CLUSTER_SIZE = 3


def normalize_question_template(question: str) -> str:
    text = unicodedata.normalize("NFKC", question).lower()
    text = re.sub(r"\d{4}-\d{1,2}-\d{1,2}", "<date>", text)
    text = re.sub(r"\d{4}\s*年\s*第?[一二三四1-4]\s*季度", "<quarter>", text)
    text = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月", "<month>", text)
    text = re.sub(r"\d{4}\s*年", "<year>", text)
    text = re.sub(r"(?<![a-z_])\d+(?:\.\d+)?(?![a-z_])", "<number>", text)
    text = re.sub(r"\b[a-z]{2}\s*州\b", "<state>", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^\w<>\u4e00-\u9fff]", "", text)
    return text


def _character_ngrams(text: str) -> frozenset[str]:
    grams: set[str] = set()
    for width in (2, 3):
        grams.update(text[index : index + width] for index in range(len(text) - width + 1))
    return frozenset(grams or {text})


def template_similarity(left: str, right: str) -> float:
    left_grams = _character_ngrams(normalize_question_template(left))
    right_grams = _character_ngrams(normalize_question_template(right))
    return 2 * len(left_grams & right_grams) / (len(left_grams) + len(right_grams))


def _count(values: Iterable[object]) -> dict[str, int]:
    return dict(sorted(Counter(str(getattr(value, "value", value)) for value in values).items()))


def _dictionary_ids(path: Path, field: str) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {row[field] for row in csv.DictReader(stream)}


def _similarity_audit(cases) -> dict[str, object]:
    exact_questions = defaultdict(list)
    normalized_questions = defaultdict(list)
    for case in cases:
        exact_questions[case.question].append(case.case_id)
        normalized_questions[normalize_question_template(case.question)].append(case.case_id)
    exact_duplicates = [ids for ids in exact_questions.values() if len(ids) > 1]
    normalized_duplicates = [ids for ids in normalized_questions.values() if len(ids) > 1]

    all_pairs = []
    adjacency: dict[str, set[str]] = defaultdict(set)
    for left, right in combinations(cases, 2):
        score = template_similarity(left.question, right.question)
        pair = {
            "left_case_id": left.case_id,
            "right_case_id": right.case_id,
            "similarity": round(score, 6),
        }
        all_pairs.append(pair)
        if score >= HIGH_SIMILARITY_THRESHOLD:
            adjacency[left.case_id].add(right.case_id)
            adjacency[right.case_id].add(left.case_id)
    all_pairs.sort(
        key=lambda item: (-item["similarity"], item["left_case_id"], item["right_case_id"])
    )
    pairs = [
        pair for pair in all_pairs if pair["similarity"] >= HIGH_SIMILARITY_THRESHOLD
    ]

    clusters: list[list[str]] = []
    remaining = set(adjacency)
    while remaining:
        start = min(remaining)
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        remaining -= component
        clusters.append(sorted(component))
    clusters.sort(key=lambda item: (-len(item), item))
    possible_pairs = len(cases) * (len(cases) - 1) // 2
    pair_ratio = len(pairs) / possible_pairs if possible_pairs else 0.0
    largest_cluster = max((len(cluster) for cluster in clusters), default=0)
    passed = (
        not exact_duplicates
        and not normalized_duplicates
        and pair_ratio <= MAX_HIGH_SIMILARITY_PAIR_RATIO
        and largest_cluster <= MAX_HIGH_SIMILARITY_CLUSTER_SIZE
    )
    return {
        "normalization": "NFKC; lowercase; dates, months, quarters, years, numbers and state codes masked; punctuation/space removed",
        "similarity_method": "Dice coefficient over combined character bigram and trigram sets",
        "high_similarity_threshold": HIGH_SIMILARITY_THRESHOLD,
        "maximum_high_similarity_pair_ratio": MAX_HIGH_SIMILARITY_PAIR_RATIO,
        "maximum_high_similarity_cluster_size": MAX_HIGH_SIMILARITY_CLUSTER_SIZE,
        "exact_duplicate_groups": exact_duplicates,
        "normalized_template_duplicate_groups": normalized_duplicates,
        "high_similarity_pair_count": len(pairs),
        "possible_pair_count": possible_pairs,
        "high_similarity_pair_ratio": pair_ratio,
        "largest_high_similarity_cluster_size": largest_cluster,
        "high_similarity_clusters": clusters,
        "high_similarity_pairs": pairs,
        "top_similarity_pairs_for_review": all_pairs[:10],
        "passed": passed,
    }


def _write_markdown(root: Path, report: dict[str, object]) -> None:
    coverage = report["coverage"]
    repetition = report["template_repetition"]
    lines = [
        "# Day 14 Coverage Report",
        "",
        "This report measures dataset composition and reference integrity. It is not candidate-model accuracy or independent business accuracy.",
        "",
        "## Validation summary",
        "",
        f"- Overall validation: `{str(report['passed']).lower()}`",
        f"- Cases: `{report['case_count']}`",
        f"- Critical failures: `{len(report['critical_failures'])}`",
        f"- External API calls: `0`",
        f"- Candidate-model runs: `0`",
        "",
        "## Category and difficulty",
        "",
        "| Category | easy | medium | hard | total |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, row in coverage["category_by_difficulty"].items():
        lines.append(
            f"| {category} | {row.get('easy', 0)} | {row.get('medium', 0)} | {row.get('hard', 0)} | {sum(row.values())} |"
        )
    lines.extend(["", "## Workflow status", ""])
    for key, value in coverage["workflow_status"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Calculation status", ""])
    for key, value in coverage["calculation_status"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Metric coverage", ""])
    for key, value in coverage["metric_ids"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Dimension coverage", ""])
    for key, value in coverage["dimensions"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Uncovered dictionary metrics", ""])
    if coverage["uncovered_metric_ids"]:
        for metric_id in coverage["uncovered_metric_ids"]:
            lines.append(f"- `{metric_id}`")
    else:
        lines.append("- None")
    lines.extend(["", "## Analysis type coverage", ""])
    for key, value in coverage["analysis_type"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## Template repetition audit",
            "",
            f"- Passed: `{str(repetition['passed']).lower()}`",
            f"- Normalized exact-template duplicates: `{len(repetition['normalized_template_duplicate_groups'])}`",
            f"- High-similarity pairs: `{repetition['high_similarity_pair_count']}` / `{repetition['possible_pair_count']}`",
            f"- High-similarity pair ratio: `{repetition['high_similarity_pair_ratio']:.6f}`",
            f"- Largest high-similarity cluster: `{repetition['largest_high_similarity_cluster_size']}`",
            "",
            "Top ten pairs are shown for audit even when they are below the failure threshold.",
            "",
            "| Left | Right | Similarity |",
            "|---|---|---:|",
        ]
    )
    for pair in repetition["top_similarity_pairs_for_review"]:
        lines.append(
            f"| {pair['left_case_id']} | {pair['right_case_id']} | {pair['similarity']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Authorship and evidence disclosure",
            "",
            "- All 60 current questions are assistant-mechanically authored.",
            "- User-reviewed or user-authored cases: 0.",
            "- Independently verified business references: 0.",
            "- Real SQLite verification and deterministic policy contracts are tracked separately from independent business review.",
            "",
        ]
    )
    (root / "docs/DAY14_COVERAGE_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def validate_dataset(root: Path) -> dict[str, object]:
    dataset_path = canonical_dataset_path(root)
    dataset = load_dataset(dataset_path)
    failures: list[str] = []
    cases = dataset.cases

    if len(cases) != 60:
        failures.append(f"expected exactly 60 cases, observed {len(cases)}")
    if dataset.state.value == "frozen" and not verify_dataset_content_sha256(dataset):
        failures.append("frozen dataset content_sha256 mismatch")
    category_counts = Counter(case.category for case in cases)
    if category_counts != Counter(REQUIRED_FROZEN_CATEGORY_COUNTS):
        failures.append("category counts are not 20/20/10/10")
    for category, expected_count in REQUIRED_FROZEN_CATEGORY_COUNTS.items():
        prefix = CATEGORY_CASE_PREFIX[category]
        expected_ids = [f"D14_{prefix}_{index:03d}" for index in range(1, expected_count + 1)]
        actual_ids = sorted(case.case_id for case in cases if case.category is category)
        if actual_ids != expected_ids:
            failures.append(f"{category.value} case IDs are not stable and contiguous")

    metric_ids = _dictionary_ids(root / "data/metadata/metric_dictionary.csv", "metric_id")
    dimension_ids = _dictionary_ids(root / "data/metadata/dimension_dictionary.csv", "dimension_id")
    unknown_supported_metrics: set[str] = set()
    unknown_supported_dimensions: set[str] = set()
    intentional_unsupported_dimensions: set[str] = set()
    reference_sql_hash_count = 0
    result_hash_count = 0
    source_hash_verified_count = 0
    source_hash_semantic_locator_count = 0

    for case in cases:
        unknown_supported_metrics.update(set(case.metric_ids) - metric_ids)
        unknown_dimensions = set(case.dimensions) - dimension_ids
        if case.category is DatasetCategory.RISK_AMBIGUOUS_UNANSWERABLE:
            intentional_unsupported_dimensions.update(unknown_dimensions)
        else:
            unknown_supported_dimensions.update(unknown_dimensions)
        if not case.provenance.reference_sources:
            failures.append(f"{case.case_id} has no reference source")

        if case.sql_reference.kind is SqlReferenceKind.STANDARD_SQL_FILE:
            sql_path = root / case.sql_reference.path
            if not sql_path.is_file() or _sha256_file(sql_path) != case.sql_reference.sha256:
                failures.append(f"{case.case_id} SQL reference hash mismatch")
            else:
                reference_sql_hash_count += 1
                validate_named_parameter_contract(
                    sql_path.read_text(encoding="utf-8"),
                    case.sql_reference.named_parameters,
                )
        if case.result_reference.path is not None:
            result_path = root / case.result_reference.path
            if not result_path.is_file() or _sha256_file(result_path) != case.result_reference.sha256:
                failures.append(f"{case.case_id} result reference hash mismatch")
            else:
                result_hash_count += 1
        for source in case.provenance.reference_sources:
            locator_path = source.locator.split("#", 1)[0]
            path = root / locator_path
            if source.sha256 is not None and path.is_file():
                if _sha256_file(path) != source.sha256:
                    failures.append(f"{case.case_id} provenance source hash mismatch: {source.locator}")
                else:
                    source_hash_verified_count += 1
            elif source.sha256 is not None:
                source_hash_semantic_locator_count += 1

    if unknown_supported_metrics:
        failures.append(f"unknown metric IDs outside dictionary: {sorted(unknown_supported_metrics)}")
    if unknown_supported_dimensions:
        failures.append(
            f"unknown dimensions outside risk cases: {sorted(unknown_supported_dimensions)}"
        )

    category_by_difficulty: dict[str, dict[str, int]] = {}
    for category in DatasetCategory:
        category_by_difficulty[category.value] = _count(
            case.difficulty for case in cases if case.category is category
        )
    coverage = {
        "category": _count(case.category for case in cases),
        "difficulty": _count(case.difficulty for case in cases),
        "category_by_difficulty": category_by_difficulty,
        "workflow_status": _count(case.expected_workflow_status for case in cases),
        "calculation_status": _count(case.expected_calculation_status for case in cases),
        "analysis_type": _count(case.analysis_type for case in cases),
        "metric_ids": _count(metric for case in cases for metric in case.metric_ids),
        "dictionary_metric_count": len(metric_ids),
        "covered_dictionary_metric_count": len(
            {metric for case in cases for metric in case.metric_ids} & metric_ids
        ),
        "uncovered_metric_ids": sorted(
            metric_ids - {metric for case in cases for metric in case.metric_ids}
        ),
        "dimensions": _count(dimension for case in cases for dimension in case.dimensions),
        "dictionary_dimension_count": len(dimension_ids),
        "covered_dictionary_dimension_count": len(
            {dimension for case in cases for dimension in case.dimensions} & dimension_ids
        ),
        "uncovered_supported_dimension_ids": sorted(
            dimension_ids - {dimension for case in cases for dimension in case.dimensions}
        ),
        "time_scope_mode": _count(case.time_scope.mode for case in cases),
        "sql_reference_kind": _count(case.sql_reference.kind for case in cases),
        "result_reference_kind": _count(case.result_reference.kind for case in cases),
        "row_comparison": _count(case.comparison_rules.row_comparison for case in cases),
        "should_enter_sqlite": _count(case.should_enter_sqlite for case in cases),
        "allows_repair": _count(case.allows_repair for case in cases),
        "case_authorship": _count(case.provenance.case_authorship for case in cases),
        "user_review_status": _count(case.provenance.user_review_status for case in cases),
        "business_reference_status": _count(
            case.provenance.business_reference_status for case in cases
        ),
        "intentional_unsupported_dimensions_in_risk_cases": sorted(
            intentional_unsupported_dimensions
        ),
    }
    repetition = _similarity_audit(cases)
    if not repetition["passed"]:
        failures.append("template repetition policy failed")

    report: dict[str, object] = {
        "measurement_scope": (
            "fixed_dataset_structure_reference_integrity_coverage_and_template_"
            "repetition; not_candidate_accuracy; not_independent_business_accuracy"
        ),
        "dataset_id": dataset.dataset_id,
        "schema_version": dataset.schema_version,
        "dataset_version": dataset.dataset_version,
        "dataset_state": dataset.state.value,
        "dataset_split": dataset.split.value,
        "dataset_content_sha256": dataset.content_sha256,
        "dataset_content_sha256_verified": (
            verify_dataset_content_sha256(dataset) if dataset.content_sha256 else None
        ),
        "case_count": len(cases),
        "category_counts_valid": category_counts == Counter(REQUIRED_FROZEN_CATEGORY_COUNTS),
        "case_ids_unique": len({case.case_id for case in cases}) == len(cases),
        "case_ids_stable_and_contiguous": not any("case IDs" in item for item in failures),
        "reference_sql_hash_verified_count": reference_sql_hash_count,
        "result_hash_verified_count": result_hash_count,
        "provenance_source_hash_verified_count": source_hash_verified_count,
        "provenance_semantic_locator_with_hash_count": source_hash_semantic_locator_count,
        "coverage": coverage,
        "template_repetition": repetition,
        "critical_failures": failures,
        "passed": not failures,
        "external_api_calls": 0,
        "candidate_model_runs": 0,
        "independent_business_reference_count": 0,
    }
    (root / "docs/DAY14_DATASET_VALIDATION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(root, report)
    if failures:
        raise RuntimeError("Day 14 dataset validation failed: " + "; ".join(failures))
    return report


def main() -> None:
    root = Path(__file__).parents[2]
    report = validate_dataset(root)
    repetition = report["template_repetition"]
    print(
        f"Day 14 dataset validation: {report['case_count']}/60 cases; "
        f"high-similarity pairs={repetition['high_similarity_pair_count']}; passed"
    )
    print("Candidate-model and external API calls: 0")


if __name__ == "__main__":
    main()
