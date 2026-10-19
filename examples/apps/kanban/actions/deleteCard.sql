-- Delete card :card_id and close the gap in its column.
UPDATE cards SET position = position - 1
WHERE column_id = (SELECT column_id FROM cards WHERE id = :card_id)
  AND position > (SELECT position FROM cards WHERE id = :card_id);

DELETE FROM cards WHERE id = :card_id;
