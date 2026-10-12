"""A small model of a transactional database.

It copies just enough of PostgreSQL's default behaviour (READ COMMITTED) to
reproduce the bugs from the talk:

* a transaction sees committed rows plus its own changes;
* UPDATE, DELETE and SELECT ... FOR UPDATE lock a row until COMMIT or ROLLBACK;
* a statement that needs a locked row waits, then looks at the row again;
* an INSERT of a key that another open transaction is writing waits too;
* when a process stops, its open transaction is rolled back.

Nothing in this file runs by itself. Every method that looks like SQL returns
a `Statement`. A request handler yields it, and the explorer (explore.py)
decides when it runs. Between two statements another request may run, or the
process may stop. That is the entire trick.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Hashable, Iterable

RowId = tuple[str, Hashable]  # (table, key)
_DELETED = object()


class UniqueViolation(Exception):
    """An INSERT met an existing key and did not say ON CONFLICT DO NOTHING."""


class ModelError(Exception):
    """The model was used wrongly: a bug in a handler, not a finding."""


@dataclass
class Statement:
    """One round trip to the database."""

    sql: str
    run: Callable[[], Any]
    needs: Callable[[], Iterable[RowId]]
    txn: Transaction | None = None

    def blocked(self) -> bool:
        """True while a row this statement needs is locked by another transaction."""
        return any(not self.txn.can_lock(row) for row in self.needs())


def sql_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        return "..."
    return f"'{value}'"


def _matches(row: dict, where: dict | None, at_least: dict | None) -> bool:
    return all(row.get(column) == value for column, value in (where or {}).items()) and all(
        row[column] >= value for column, value in (at_least or {}).items()
    )


def _conditions(where: dict | None, at_least: dict | None) -> list[str]:
    parts = [f"{column} = {sql_value(value)}" for column, value in (where or {}).items()]
    parts += [f"{column} >= {sql_value(value)}" for column, value in (at_least or {}).items()]
    return parts


class Database:
    """Tables of committed rows, and the row locks held by open transactions."""

    def __init__(self, name: str, **tables: tuple[str, ...]):
        """`tables` maps each table name to the names of its key columns."""
        self.name = name
        self.keys = dict(tables)
        self.tables: dict[str, dict[Hashable, dict]] = {table: {} for table in tables}
        self.locks: dict[RowId, Transaction] = {}
        self.serial = 0

    # --- setting up and looking at committed data (no scheduling involved) ---

    def load(self, table: str, rows: dict[Hashable, dict]) -> None:
        """Put committed rows into a table, keyed by their key."""
        for key, row in rows.items():
            self.tables[table][key] = dict(row)

    def add(self, table: str, *rows: dict) -> None:
        """Put committed rows into a table that has no natural key."""
        for row in rows:
            self.serial += 1
            self.tables[table][self.serial] = dict(row)

    def rows(self, table: str) -> list[dict]:
        return [self.with_key(table, key, row) for key, row in self.tables[table].items()]

    def row(self, table: str, key: Hashable) -> dict | None:
        row = self.tables[table].get(key)
        return None if row is None else self.with_key(table, key, row)

    def with_key(self, table: str, key: Hashable, row: dict) -> dict:
        columns = self.keys[table]
        values = key if len(columns) > 1 else (key,)
        return {**dict(zip(columns, values)), **row}

    def key_sql(self, table: str, key: Hashable) -> str:
        columns = self.keys[table]
        values = key if len(columns) > 1 else (key,)
        return " AND ".join(f"{column} = {sql_value(value)}" for column, value in zip(columns, values))

    # --- statements ---

    def begin(self) -> Transaction:
        """Open a transaction. It starts with its first statement."""
        return Transaction(self)

    def _auto(self, method: str, *args: Any, **kwargs: Any) -> Statement:
        """A statement in its own transaction, committed at once (autocommit)."""
        txn = Transaction(self, implicit=True)
        inner = getattr(txn, method)(*args, **kwargs)

        def run() -> Any:
            try:
                result = inner.run()
            except Exception:
                txn._rollback()
                raise
            txn._commit()
            return result

        return Statement(inner.sql, run, inner.needs, txn)

    def select(self, table, key):
        return self._auto("select", table, key)

    def select_where(self, table, where=None, at_least=None):
        return self._auto("select_where", table, where, at_least)

    def update(self, table, key, to=None, add=None, where=None, at_least=None):
        return self._auto("update", table, key, to, add, where, at_least)

    def insert(self, table, key, row=None, on_conflict_do_nothing=False):
        return self._auto("insert", table, key, row, on_conflict_do_nothing)

    def append(self, table, *rows):
        return self._auto("append", table, *rows)

    def delete_where(self, table, where=None):
        return self._auto("delete_where", table, where)


class Transaction:
    """BEGIN ... COMMIT. Its changes are private until COMMIT."""

    def __init__(self, db: Database, implicit: bool = False):
        self.db = db
        self.implicit = implicit
        self.open = True
        self.writes: dict[RowId, Any] = {}
        self.held: set[RowId] = set()

    # --- what this transaction sees: committed rows plus its own changes ---

    def _row(self, table: str, key: Hashable) -> dict | None:
        mine = self.writes.get((table, key))
        if mine is _DELETED:
            return None
        if mine is not None:
            return mine
        return self.db.tables[table].get(key)

    def _visible(self, table: str) -> dict[Hashable, dict]:
        rows = dict(self.db.tables[table])
        for (other_table, key), mine in self.writes.items():
            if other_table != table:
                continue
            if mine is _DELETED:
                rows.pop(key, None)
            else:
                rows[key] = mine
        return rows

    def _out(self, table: str, key: Hashable) -> dict | None:
        row = self._row(table, key)
        return None if row is None else self.db.with_key(table, key, row)

    # --- row locks ---

    def can_lock(self, row: RowId) -> bool:
        return self.db.locks.get(row) in (None, self)

    def _lock(self, row: RowId) -> None:
        if not self.can_lock(row):
            raise ModelError(f"{row} is locked by another transaction")
        self.db.locks[row] = self
        self.held.add(row)

    def _finish(self) -> None:
        for row in self.held:
            del self.db.locks[row]
        self.held.clear()
        self.open = False

    def _commit(self) -> None:
        for (table, key), mine in self.writes.items():
            if mine is _DELETED:
                self.db.tables[table].pop(key, None)
            else:
                self.db.tables[table][key] = mine
        self._finish()

    def _rollback(self) -> None:
        self.writes.clear()
        self._finish()

    # --- statements ---

    def _statement(self, sql: str, run: Callable[[], Any], needs: Callable[[], Iterable[RowId]] = tuple) -> Statement:
        return Statement(sql, run, needs, self)

    def select(self, table, key):
        """A plain read. It never waits and never reserves anything."""
        sql = f"SELECT * FROM {table} WHERE {self.db.key_sql(table, key)}"
        return self._statement(sql, lambda: self._out(table, key))

    def select_where(self, table, where=None, at_least=None):
        def run() -> list[dict]:
            found = []
            for key, row in self._visible(table).items():
                out = self.db.with_key(table, key, row)
                if _matches(out, where, at_least):
                    found.append(out)
            return found

        conditions = _conditions(where, at_least)
        sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(conditions) if conditions else "")
        return self._statement(sql, run)

    def select_for_update(self, table, key):
        """Read a row and keep it locked until COMMIT."""
        row = (table, key)

        def needs() -> list[RowId]:
            return [row] if self._row(table, key) is not None else []

        def run() -> dict | None:
            if self._row(table, key) is None:
                return None
            self._lock(row)
            return self._out(table, key)

        sql = f"SELECT * FROM {table} WHERE {self.db.key_sql(table, key)} FOR UPDATE"
        return self._statement(sql, run, needs)

    def update(self, table, key, to=None, add=None, where=None, at_least=None):
        """Change one row if it still matches. Returns the number of rows changed: 0 or 1."""
        row = (table, key)

        def hit() -> bool:
            current = self._out(table, key)
            return current is not None and _matches(current, where, at_least)

        def needs() -> list[RowId]:
            return [row] if hit() else []

        def run() -> int:
            if not hit():
                return 0
            self._lock(row)
            changed = dict(self._row(table, key))
            changed.update(to or {})
            for column, delta in (add or {}).items():
                changed[column] += delta
            self.writes[row] = changed
            return 1

        sets = [f"{column} = {sql_value(value)}" for column, value in (to or {}).items()]
        for column, delta in (add or {}).items():
            sets.append(f"{column} = {column} {'-' if delta < 0 else '+'} {abs(delta)}")
        conditions = [self.db.key_sql(table, key)] + _conditions(where, at_least)
        sql = f"UPDATE {table} SET {', '.join(sets)} WHERE {' AND '.join(conditions)}"
        return self._statement(sql, run, needs)

    def insert(self, table, key, row=None, on_conflict_do_nothing=False):
        """Insert a row under a unique key. Returns the number of rows inserted: 0 or 1."""
        rid = (table, key)

        def needs() -> list[RowId]:
            # Wait only while another open transaction is writing this very key.
            holder = self.db.locks.get(rid)
            return [rid] if holder not in (None, self) and rid in holder.writes else []

        def run() -> int:
            if self._row(table, key) is not None:
                if on_conflict_do_nothing:
                    return 0
                raise UniqueViolation(f"{table}: {self.db.key_sql(table, key)} already exists")
            self._lock(rid)
            self.writes[rid] = dict(row or {})
            return 1

        shown = {**self.db.with_key(table, key, {}), **(row or {})}
        sql = f"INSERT INTO {table} VALUES ({', '.join(sql_value(v) for v in shown.values())})"
        if on_conflict_do_nothing:
            sql += " ON CONFLICT DO NOTHING"
        return self._statement(sql, run, needs)

    def append(self, table, *rows):
        """Insert rows into a table without a unique key. Nothing can conflict."""

        def run() -> int:
            for row in rows:
                self.db.serial += 1
                self.writes[(table, self.db.serial)] = dict(row)
            return len(rows)

        values = ", ".join("(" + ", ".join(sql_value(v) for v in row.values()) + ")" for row in rows)
        return self._statement(f"INSERT INTO {table} VALUES {values}", run)

    def delete_where(self, table, where=None):
        """Delete every matching row. Returns the number of rows deleted."""

        def hits() -> list[Hashable]:
            return [
                key
                for key, row in self._visible(table).items()
                if _matches(self.db.with_key(table, key, row), where, None)
            ]

        def needs() -> list[RowId]:
            return [(table, key) for key in hits()]

        def run() -> int:
            keys = hits()
            for key in keys:
                self._lock((table, key))
                self.writes[(table, key)] = _DELETED
            return len(keys)

        conditions = _conditions(where, None)
        sql = f"DELETE FROM {table}" + (" WHERE " + " AND ".join(conditions) if conditions else "")
        return self._statement(sql, run, needs)

    def commit(self) -> Statement:
        return self._statement("COMMIT", self._commit)

    def rollback(self) -> Statement:
        return self._statement("ROLLBACK", self._rollback)
