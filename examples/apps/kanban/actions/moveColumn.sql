-- Move column :column_id to :position on its board: 0 is the left, and
-- anything past the end puts it last. Positions stay 0, 1, 2, …
UPDATE columns SET position = position - 1
WHERE board_id = (SELECT board_id FROM columns WHERE id = :column_id)
  AND position > (SELECT position FROM columns WHERE id = :column_id);

UPDATE columns SET position = position + 1
WHERE board_id = (SELECT board_id FROM columns WHERE id = :column_id)
  AND id <> :column_id AND position >= max(0, :position);

UPDATE columns SET position = min(max(0, :position), (SELECT count(*) - 1 FROM columns c WHERE c.board_id = columns.board_id))
WHERE id = :column_id;
