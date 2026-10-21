-- Drop a disc in :col as move number :turn (0-based) of game :game_id, and
-- record how the game stands after it (:result 'playing', 'won' or 'draw',
-- with :winner's user id when won; the app works that out from the board).
-- Nothing happens unless the game is still on, :turn is the next move (so a
-- double tap, or both players at once, can't make two moves) and the column
-- has room. Returns how many moves the game has now: :turn + 1 if it landed.
INSERT INTO moves (game_id, turn, col, row)
SELECT :game_id, :turn, :col, (SELECT count(*) FROM moves WHERE game_id = :game_id AND col = :col)
WHERE EXISTS (SELECT 1 FROM games WHERE id = :game_id AND status = 'playing')
  AND (SELECT count(*) FROM moves WHERE game_id = :game_id) = :turn
  AND (SELECT count(*) FROM moves WHERE game_id = :game_id AND col = :col) < 6;

UPDATE games SET status = :result, winner = :winner
WHERE id = :game_id AND status = 'playing' AND :result IN ('won', 'draw')
  AND (SELECT count(*) FROM moves WHERE game_id = :game_id) = :turn + 1;

SELECT count(*) AS moves FROM moves WHERE game_id = :game_id;
