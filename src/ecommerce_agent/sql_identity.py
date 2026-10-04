"""SQLite normalization, hashing, and duplicate-candidate detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

import sqlglot
from sqlglot.errors import SqlglotError


class SqlNormalizationError(ValueError):
    """Raised when one stable SQLite statement cannot be produced."""


@dataclass(frozen=True)
class SqlIdentity:
    normalized_sql: str
    sha256: str


@dataclass(frozen=True)
class CandidateRegistration:
    identity: SqlIdentity
    sql_attempt: int
    is_duplicate: bool
    first_seen_sql_attempt: int


def identify_sql(sql: str) -> SqlIdentity:
    """Return a stable SQLite rendering and its SHA-256 identity.

    Normalization removes irrelevant formatting, keyword/identifier case, and
    an optional trailing semicolon.  It deliberately does not claim semantic
    equivalence between structurally different queries.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SqlNormalizationError("SQL 不能为空")
    try:
        expressions = sqlglot.parse(
            sql,
            read="sqlite",
            error_level=sqlglot.ErrorLevel.RAISE,
        )
    except (SqlglotError, ValueError, RecursionError) as error:
        raise SqlNormalizationError("SQL 无法按 SQLite 方言规范化") from error
    if len(expressions) != 1 or expressions[0] is None:
        raise SqlNormalizationError("只能规范化一条 SQL")
    normalized = expressions[0].sql(
        dialect="sqlite",
        pretty=False,
        normalize=True,
    )
    return SqlIdentity(
        normalized_sql=normalized,
        sha256=sha256(normalized.encode("utf-8")).hexdigest(),
    )


@dataclass
class SqlCandidateRegistry:
    """Remember SQL identities for one run_id and reject prior candidates."""

    _first_seen_attempt_by_hash: dict[str, int] = field(
        default_factory=dict
    )
    _last_sql_attempt: int = 0

    def register(self, sql: str, *, sql_attempt: int) -> CandidateRegistration:
        if (
            isinstance(sql_attempt, bool)
            or not isinstance(sql_attempt, int)
            or sql_attempt < 1
        ):
            raise ValueError("sql_attempt 必须是正整数")
        if sql_attempt != self._last_sql_attempt + 1:
            raise ValueError("sql_attempt 必须从 1 开始连续递增")

        identity = identify_sql(sql)
        first_seen = self._first_seen_attempt_by_hash.get(identity.sha256)
        is_duplicate = first_seen is not None
        if first_seen is None:
            first_seen = sql_attempt
            self._first_seen_attempt_by_hash[identity.sha256] = sql_attempt
        self._last_sql_attempt = sql_attempt
        return CandidateRegistration(
            identity=identity,
            sql_attempt=sql_attempt,
            is_duplicate=is_duplicate,
            first_seen_sql_attempt=first_seen,
        )
