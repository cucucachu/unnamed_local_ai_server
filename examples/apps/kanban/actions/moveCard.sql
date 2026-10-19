-- Move card :card_id to column :column_id (on the same board) at
-- :position: 0 is the top, and anything past the end puts it at the
-- bottom. Positions in both columns stay 0, 1, 2, …
-- First close the gap where it was.
UPDATE cards SET position = position - 1
WHERE column_id = (SELECT column_id FROM cards WHERE id = :card_id)
  AND position > (SELECT position FROM cards WHERE id = :card_id)
  AND (SELECT board_id FROM columns WHERE id = :column_id)
    = (SELECT co.board_id FROM cards ca JOIN columns co ON co.id = ca.column_id WHERE ca.id = :card_id);

-- Then make room where it goes.
UPDATE cards SET position = position + 1
WHERE column_id = :column_id AND id <> :card_id AND position >= max(0, :position)
  AND (SELECT board_id FROM columns WHERE id = :column_id)
    = (SELECT co.board_id FROM cards ca JOIN columns co ON co.id = ca.column_id WHERE ca.id = :card_id);

UPDATE cards SET
  column_id = :column_id,
  position = min(max(0, :position), (SELECT count(*) FROM cards WHERE column_id = :column_id AND id <> :card_id))
WHERE id = :card_id
  AND (SELECT board_id FROM columns WHERE id = :column_id)
    = (SELECT co.board_id FROM columns co WHERE co.id = cards.column_id);
