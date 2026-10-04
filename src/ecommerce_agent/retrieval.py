"""Offline retrieval contracts and an explainable lexical baseline.

Documents are derived views, not a second source of business definitions.
Retrieval never authorizes dimensions or generates SQL.
"""

import csv
import math
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from src.ecommerce_agent.metric_catalog import MetricCatalog


@dataclass(frozen=True)
class RetrievalDocument:
    document_id: str
    document_type: str
    identifier: str
    chinese_name: str
    description: str
    sources: tuple[str, ...]
    grain: str
    tables: tuple[str, ...]
    fields: tuple[str, ...]
    available_dimensions: tuple[str, ...]
    constraints: str
    formula: str = ""
    default_time_field: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalHit:
    document: RetrievalDocument
    score: float
    rank: int
    evidence: dict


@dataclass(frozen=True)
class RetrievalFilter:
    document_types: frozenset[str] = frozenset({"metric", "schema"})
    tables: frozenset[str] = frozenset()

    def __post_init__(self):
        if not self.document_types <= {"metric", "schema"}:
            raise ValueError("Unknown document type")


class Retriever(Protocol):
    def search(
        self, query: str, *, top_k: int = 5,
        filters: RetrievalFilter | None = None,
    ) -> tuple[RetrievalHit, ...]: ...


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_documents(root: Path) -> tuple[RetrievalDocument, ...]:
    """Load canonical CSVs; verify physical fields against in-memory DDL.

    Metric fields are a conservative table-context set, not a minimal SQL
    dependency list. The formula and full constraints remain authoritative.
    """
    metadata_dir = root / "data" / "metadata"
    metrics = _rows(metadata_dir / "metric_dictionary.csv")
    dimensions = _rows(metadata_dir / "dimension_dictionary.csv")
    columns = _rows(metadata_dir / "database_data_dictionary.csv")
    catalog = MetricCatalog.from_csv(
        metadata_dir / "metric_dictionary.csv",
        metadata_dir / "dimension_dictionary.csv",
    )
    dimension_by_id = {row["dimension_id"]: row for row in dimensions}
    metric_by_id = {row["metric_id"]: row for row in metrics}
    table_names = sorted({row["table_name"] for row in columns})
    qualified = {f"{row['table_name']}.{row['column_name']}" for row in columns}
    if len(qualified) != len(columns):
        raise ValueError("Duplicate schema field")
    if len({row["metric_id"] for row in metrics}) != len(metrics):
        raise ValueError("Duplicate metric ID")
    if len(dimension_by_id) != len(dimensions):
        raise ValueError("Duplicate dimension ID")
    for row in dimensions:
        if f"{row['source_table']}.{row['source_column']}" not in qualified:
            raise ValueError("Dimension field is missing from schema")

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript((root / "sql" / "schema.sql").read_text(encoding="utf-8"))
        ddl = dict(connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ))
        actual_fields = {
            f"{table}.{row[1]}"
            for table in ddl
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if actual_fields != qualified:
            raise ValueError("Database dictionary and DDL fields differ")
    finally:
        connection.close()

    # Chinese field descriptions already exist in the Data import source document.
    translations = {}
    source_file = None
    for line in (root / "docs" / "OLIST_DATA_DICTIONARY.md").read_text(encoding="utf-8").splitlines():
        heading = re.match(r"## `([^`]+)`", line)
        if heading:
            source_file = heading[1]
        entry = re.match(r"\| `([^`]+)` \| (.*?) \|", line)
        if entry and source_file:
            translations[(source_file, entry[1])] = entry[2]

    documents = []
    for metric in metrics:
        identifier = metric["metric_id"]
        allowed = tuple(sorted(catalog.metrics[identifier].available_dimensions))
        source_tables = tuple(metric["source_tables"].split("|"))
        context_tables = tuple(sorted(set(source_tables) | {
            dimension_by_id[dim]["source_table"] for dim in allowed
        }))
        if not set(context_tables) <= set(table_names):
            raise ValueError("Unknown metric source table")
        # One-hop exact identifiers only; do not invent missing pseudo-metrics,
        # recursively expand formulas, or replace the canonical business rule.
        references = sorted(set(re.findall(r"[a-z_][a-z_0-9]*", metric["formula"]))
                            & (set(metric_by_id) - {identifier}))
        ratio = re.fullmatch(r"([a-z_][a-z_0-9]*)\s*/\s*([a-z_][a-z_0-9]*)", metric["formula"])
        dependencies = []
        for reference in references:
            dependency = metric_by_id[reference]
            role = "reference"
            if ratio and reference == ratio[1]:
                role = "numerator"
            elif ratio and reference == ratio[2]:
                role = "denominator"
            dependencies.append({
                "metric_id": reference, "role": role,
                "chinese_name": dependency["chinese_name"],
                "definition": dependency["definition"],
                "formula": dependency["formula"],
                "constraints": dependency["constraints"],
                "source": "data/metadata/metric_dictionary.csv",
            })
        documents.append(RetrievalDocument(
            document_id=f"metric:{identifier}", document_type="metric",
            identifier=identifier, chinese_name=metric["chinese_name"],
            description=metric["definition"],
            sources=("data/metadata/metric_dictionary.csv", "data/metadata/dimension_dictionary.csv",
                     "data/metadata/database_data_dictionary.csv", "sql/schema.sql"),
            grain=metric["base_grain"], tables=source_tables,
            fields=tuple(sorted(name for name in qualified if name.split(".")[0] in context_tables)),
            available_dimensions=allowed, constraints=metric["constraints"],
            formula=metric["formula"], default_time_field=metric["default_time_field"],
            metadata={
                "english_name": metric["english_name"],
                "dimension_names": metric["available_dimensions"],
                "formula_dependencies": dependencies,
                "dependency_scope": "one_hop_exact_existing_metric_ids_only",
                "field_scope": "all_columns_of_source_and_allowed_dimension_tables_not_minimal_dependencies",
                "context_tables": context_tables,
                "table_context": {table: {
                    "grain": next(row["grain"] for row in columns if row["table_name"] == table),
                    "ddl": ddl[table],
                } for table in context_tables},
            },
        ))
    for column in columns:
        table, name = column["table_name"], column["column_name"]
        identifier = f"{table}.{name}"
        chinese = translations.get((column["source_file"], name))
        if not chinese:
            raise ValueError(f"Missing existing Chinese description for {identifier}")
        documents.append(RetrievalDocument(
            document_id=f"schema:{identifier}", document_type="schema",
            identifier=identifier, chinese_name=chinese,
            description=column["description"],
            sources=("data/metadata/database_data_dictionary.csv", "sql/schema.sql",
                     "docs/OLIST_DATA_DICTIONARY.md", "data/metadata/dimension_dictionary.csv"),
            grain=column["grain"], tables=(table,), fields=(identifier,),
            available_dimensions=tuple(sorted(
                dim["dimension_id"] for dim in dimensions
                if dim["source_table"] == table and dim["source_column"] == name
            )),
            constraints=ddl[table],
            metadata={**column, "ddl": ddl[table],
                      "dimension_scope": "field_mapping_not_metric_permission"},
        ))
    return tuple(documents)


def lexical_terms(text: str) -> frozenset[str]:
    """Chinese adjacent bigrams + intact English identifiers; no model tokens."""
    text = text.lower()
    terms = set(re.findall(r"[a-z_][a-z_0-9]*(?:\.[a-z_][a-z_0-9]*)?", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return frozenset(terms)


class KeywordRetriever:
    """Weighted lexical overlap with corpus IDF; deterministic version v1.

    Constraints are always returned but not scored: a prohibition containing a
    term must not count as positive relevance evidence. No dimension permission
    filtering is applied here; Analysis planning remains responsible for semantic validation.
    """

    version = "keyword-bigram-idf-v1"
    weights = {"name": 3.0, "description": 1.0}

    def __init__(self, documents: tuple[RetrievalDocument, ...]):
        self.documents = documents
        self.index = [self._index_fields(doc) for doc in documents]
        terms = set().union(*(set().union(*row.values()) for row in self.index))
        self.idf = {
            term: math.log((len(documents) + 1) / (1 + sum(
                term in set().union(*row.values()) for row in self.index
            ))) + 1 for term in terms
        }

    def _index_fields(self, doc: RetrievalDocument) -> dict[str, frozenset[str]]:
        return {
            "name": lexical_terms(f"{doc.identifier} {doc.chinese_name}"),
            "description": lexical_terms(doc.description),
        }

    def search(self, query: str, *, top_k: int = 5,
               filters: RetrievalFilter | None = None) -> tuple[RetrievalHit, ...]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if not query.strip():
            raise ValueError("Query must not be blank")
        filters = filters or RetrievalFilter()
        query_terms = lexical_terms(query)
        candidates = []
        for document, indexed in zip(self.documents, self.index):
            if document.document_type not in filters.document_types:
                continue
            if filters.tables and not filters.tables.intersection(document.tables):
                continue
            contributions = {
                field_name: {term: self.weights[field_name] * self.idf[term]
                             for term in sorted(query_terms & terms)}
                for field_name, terms in indexed.items()
            }
            score = sum(sum(values.values()) for values in contributions.values())
            if score > 0:
                candidates.append((document, score, contributions))
        candidates.sort(key=lambda item: (-item[1], item[0].document_id))
        return tuple(RetrievalHit(document, score, rank, {
            "retriever_version": self.version, "field_contributions": contributions,
        }) for rank, (document, score, contributions) in enumerate(candidates[:top_k], 1))


class DependencyKeywordRetriever(KeywordRetriever):
    """Controlled v2 experiment: add one-hop definitions as a separate field.

    Formula and roles remain in the returned metadata. Scoring still does not
    infer arithmetic, time relations or compatible dimensions. Expanded corpus
    terms change IDF; comparisons concern rankings, not absolute scores.
    """

    version = "keyword-bigram-idf-dependencies-v2"
    weights = {"name": 3.0, "description": 1.0, "dependencies": 1.0}

    def _index_fields(self, doc: RetrievalDocument) -> dict[str, frozenset[str]]:
        fields = super()._index_fields(doc)
        fields["dependencies"] = lexical_terms(" ".join(
            f"{entry['chinese_name']} {entry['definition']}"
            for entry in doc.metadata.get("formula_dependencies", [])
        ))
        return fields


def retrieve_payload(retriever: Retriever, question: str, *, top_k: int = 5,
                     filters: RetrievalFilter | None = None) -> dict:
    """Implementation-independent output boundary for future business flows."""
    return {"question": question, "top_k": top_k,
            "filters": {"document_types": sorted((filters or RetrievalFilter()).document_types),
                        "tables": sorted((filters or RetrievalFilter()).tables)},
            "hits": [asdict(hit) for hit in retriever.search(question, top_k=top_k, filters=filters)]}
