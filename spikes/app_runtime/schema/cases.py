"""Schema-change matrix shared by the sqlite3def run and the Python differ.

Each case is (name, expected class, desired schema.sql). The live database is
always BASE with SEED rows in it.
"""

BASE = """
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE INDEX items_name ON items (name);
CREATE TABLE lists (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL
);
"""

SEED = """
INSERT INTO items (name, done, note) VALUES ('Milk', 0, '2%'), ('Eggs', 1, NULL), ('Bread', 0, 'rye');
INSERT INTO lists (title) VALUES ('Weekly');
"""

ITEMS = """CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  note TEXT{extra}
);"""
IDX = "CREATE INDEX items_name ON items (name);"
LISTS = "CREATE TABLE lists (\n  id INTEGER PRIMARY KEY,\n  title TEXT NOT NULL\n);"


def items(extra=""):
    return ITEMS.format(extra=extra)


CASES = [
    ("noop", "none", f"{items()}\n{IDX}\n{LISTS}"),
    ("add_table", "additive", f"{items()}\n{IDX}\n{LISTS}\nCREATE TABLE tags (id INTEGER PRIMARY KEY, label TEXT NOT NULL);"),
    ("add_column_nullable", "additive", f"{items(',\n  qty INTEGER')}\n{IDX}\n{LISTS}"),
    ("add_column_notnull_default", "additive", f"{items(',\n  qty INTEGER NOT NULL DEFAULT 1')}\n{IDX}\n{LISTS}"),
    ("add_column_notnull_no_default", "invalid/rebuild", f"{items(',\n  qty INTEGER NOT NULL')}\n{IDX}\n{LISTS}"),
    ("add_column_fk", "additive", f"{items(',\n  list_id INTEGER REFERENCES lists (id)')}\n{IDX}\n{LISTS}"),
    ("add_index", "additive", f"{items()}\n{IDX}\nCREATE INDEX items_done ON items (done);\n{LISTS}"),
    ("drop_index", "destructive(cheap)", f"{items()}\n{LISTS}"),
    ("drop_column", "destructive", "CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n  done INTEGER NOT NULL DEFAULT 0\n);\n" + f"{IDX}\n{LISTS}"),
    ("drop_table", "destructive", f"{items()}\n{IDX}"),
    ("type_change", "destructive(rebuild)", "CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n  done TEXT NOT NULL DEFAULT 'no',\n  note TEXT\n);\n" + f"{IDX}\n{LISTS}"),
    ("rename_column", "destructive(ambiguous)", "CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  title TEXT NOT NULL,\n  done INTEGER NOT NULL DEFAULT 0,\n  note TEXT\n);\nCREATE INDEX items_name ON items (title);\n" + LISTS),
    ("add_unique", "destructive(rebuild)", "CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL UNIQUE,\n  done INTEGER NOT NULL DEFAULT 0,\n  note TEXT\n);\n" + f"{IDX}\n{LISTS}"),
    ("nullable_to_notnull", "destructive(rebuild)", "CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n  done INTEGER NOT NULL DEFAULT 0,\n  note TEXT NOT NULL DEFAULT ''\n);\n" + f"{IDX}\n{LISTS}"),
    ("change_default", "rebuild(safe)", "CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n  done INTEGER NOT NULL DEFAULT 1,\n  note TEXT\n);\n" + f"{IDX}\n{LISTS}"),
]
