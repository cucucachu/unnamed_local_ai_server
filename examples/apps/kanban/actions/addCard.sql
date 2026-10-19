-- Add a card titled :title to the bottom of column :column_id.
INSERT INTO cards (column_id, title, position)
SELECT id, trim(:title), (SELECT count(*) FROM cards WHERE column_id = :column_id)
FROM columns WHERE id = :column_id AND length(trim(:title)) > 0;
