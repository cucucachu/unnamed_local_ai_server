-- One row per message in the space's room. Who sent it and when are the platform's
-- _created_by / _created_at; who last changed it, _updated_by / _updated_at.
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  body TEXT NOT NULL DEFAULT '', -- the message, plain text; '' once deleted
  edited INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0, 1)), -- 1 once it has been changed
  deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)), -- 1 once deleted (it stays as "Message deleted")
  CHECK (deleted = 1 OR length(trim(body)) > 0)
);
