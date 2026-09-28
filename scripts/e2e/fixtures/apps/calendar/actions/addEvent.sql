INSERT INTO events (title) VALUES (:title);
SELECT id, title FROM events WHERE id = last_insert_rowid();
