-- Delete column :column_id and its cards, and close the gap on its board.
UPDATE columns SET position = position - 1
WHERE board_id = (SELECT board_id FROM columns WHERE id = :column_id)
  AND position > (SELECT position FROM columns WHERE id = :column_id);

DELETE FROM columns WHERE id = :column_id;
