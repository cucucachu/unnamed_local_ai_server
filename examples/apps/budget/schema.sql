-- A category of spending or income, with an optional monthly budget.
CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- e.g. "Groceries"
  monthly_budget REAL CHECK (monthly_budget IS NULL OR monthly_budget >= 0), -- how much to spend a month; NULL for no budget
  is_income INTEGER NOT NULL DEFAULT 0 CHECK (is_income IN (0, 1)) -- 1 for income (e.g. salary), 0 for spending
);

-- One transaction, e.g. a line of a bank statement: money spent (negative) or received (positive).
CREATE TABLE transactions (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'), -- the date, YYYY-MM-DD
  description TEXT NOT NULL DEFAULT '', -- the payee, or the bank's text for it
  amount REAL NOT NULL CHECK (typeof(amount) IN ('integer', 'real')), -- negative for money spent, positive for money in
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL, -- NULL until it's categorized
  note TEXT NOT NULL DEFAULT '' -- anything worth remembering about it
);

-- How to categorize: a transaction whose description contains `pattern` (in any case) goes in `category_id`.
CREATE TABLE rules (
  id INTEGER PRIMARY KEY,
  pattern TEXT NOT NULL CHECK (length(trim(pattern)) > 0), -- e.g. "TRADER JOE"
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE -- where matching transactions go
);

CREATE INDEX transactions_day ON transactions (day);
CREATE INDEX transactions_category ON transactions (category_id);
