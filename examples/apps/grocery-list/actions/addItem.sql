-- Add :name to the list. If it's already there (any case), uncheck it
-- instead of adding a duplicate.
UPDATE items SET checked = 0 WHERE name = trim(:name) COLLATE NOCASE;

INSERT INTO items (name)
SELECT trim(:name)
WHERE NOT EXISTS (SELECT 1 FROM items WHERE name = trim(:name) COLLATE NOCASE);
