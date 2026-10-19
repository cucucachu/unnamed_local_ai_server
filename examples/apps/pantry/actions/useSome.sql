-- Take :amount of item :id. When nothing is left, the row is deleted
-- ("used up"); the result's changes count both statements.
UPDATE items SET quantity = max(0, quantity - :amount) WHERE id = :id;

DELETE FROM items WHERE id = :id AND quantity <= 0;
