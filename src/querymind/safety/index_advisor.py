"""Index advisor: analyzes query AST and EXPLAIN warnings to suggest indexes."""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp


def suggest_indexes_for_query(sql: str, warnings: list[str], dialect: str = "mysql") -> list[str]:
    """Inspect query WHERE/JOIN/ORDER BY clauses for tables flagged in warnings and suggest indexes."""
    suggestions: list[str] = []
    flagged_tables: set[str] = set()

    for w in warnings:
        # e.g. "Full table scan on `orders` (~100,000 rows)..."
        match = re.search(r"on [`'\"]?([A-Za-z0-9_]+)[`'\"]?", w)
        if match:
            flagged_tables.add(match.group(1).lower())

    if not flagged_tables:
        return suggestions

    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return suggestions

    # Map aliases to table names
    table_aliases: dict[str, str] = {}
    for table_node in parsed.find_all(exp.Table):
        tname = table_node.name
        alias = table_node.alias or tname
        table_aliases[alias.lower()] = tname.lower()
        table_aliases[tname.lower()] = tname.lower()

    # Collect columns used in WHERE predicates and JOIN ON conditions for each table
    table_filter_cols: dict[str, list[str]] = {t: [] for t in flagged_tables}

    for col_node in parsed.find_all(exp.Column):
        cname = col_node.name
        table_ref = col_node.table
        if not table_ref and len(flagged_tables) == 1:
            actual_table = next(iter(flagged_tables))
        elif table_ref and table_ref.lower() in table_aliases:
            actual_table = table_aliases[table_ref.lower()]
        else:
            continue

        if actual_table in table_filter_cols and cname:
            # Check if this column is under a Where, Join, or Order clause
            parent = col_node.find_ancestor(exp.Where, exp.Join, exp.Order)
            if parent is not None and cname.lower() not in [c.lower() for c in table_filter_cols[actual_table]]:
                table_filter_cols[actual_table].append(cname)

    for tname, cols in table_filter_cols.items():
        if cols:
            idx_cols = ", ".join(f"`{c}`" for c in cols[:3])
            col_suffix = "_".join(cols[:2])
            idx_name = f"idx_{tname}_{col_suffix}"
            suggestions.append(f"CREATE INDEX `{idx_name}` ON `{tname}` ({idx_cols});")

    return suggestions
