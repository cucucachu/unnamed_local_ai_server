-- Add weight, sleep and water, skipping any that already exist (by name, any case).
INSERT INTO measures (name, unit, goal, decimals, summary)
SELECT v.column1, v.column2, v.column3, v.column4, v.column5
FROM (VALUES ('Weight', 'kg', NULL, 1, 'last'), ('Sleep', 'hours', 8, 1, 'sum'), ('Water', 'glasses', 8, 0, 'sum')) AS v
WHERE NOT EXISTS (SELECT 1 FROM measures m WHERE lower(m.name) = lower(v.column1));
