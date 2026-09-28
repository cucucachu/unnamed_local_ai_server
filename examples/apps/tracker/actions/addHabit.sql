-- Add a habit named :name. Empty or duplicate names (any case) are ignored.
INSERT INTO habits (name)
SELECT trim(:name)
WHERE length(trim(:name)) > 0
  AND NOT EXISTS (SELECT 1 FROM habits WHERE name = trim(:name) COLLATE NOCASE);
