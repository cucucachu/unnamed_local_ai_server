-- Make a board called :name with three columns: To do, Doing, Done. The
-- new board is the one with the highest id.
INSERT INTO boards (name) SELECT trim(:name) WHERE length(trim(:name)) > 0;

INSERT INTO columns (board_id, name, position, wip_limit)
SELECT (SELECT max(id) FROM boards), v.column1, v.column2, v.column3
FROM (VALUES ('To do', 0, NULL), ('Doing', 1, 3), ('Done', 2, NULL)) AS v
WHERE length(trim(:name)) > 0;
