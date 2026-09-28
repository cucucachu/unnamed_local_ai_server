-- Add a note with :title. Empty titles are ignored.
INSERT INTO notes (title)
SELECT trim(:title)
WHERE length(trim(:title)) > 0;
