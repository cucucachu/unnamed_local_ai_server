-- Add the usual categories, skipping any that already exist (by name, any case).
INSERT INTO categories (name, is_income)
SELECT v.column1, v.column2
FROM (VALUES ('Groceries', 0), ('Eating out', 0), ('Rent', 0), ('Bills', 0), ('Transport', 0), ('Fun', 0), ('Salary', 1)) AS v
WHERE NOT EXISTS (SELECT 1 FROM categories c WHERE lower(c.name) = lower(v.column1));
