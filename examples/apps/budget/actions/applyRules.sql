-- Categorize every uncategorized transaction that a rule matches. When
-- several rules match, the longest pattern wins.
UPDATE transactions SET category_id = (
  SELECT r.category_id FROM rules r
  WHERE instr(lower(transactions.description), lower(r.pattern)) > 0
  ORDER BY length(r.pattern) DESC, r.id
  LIMIT 1
)
WHERE category_id IS NULL
  AND EXISTS (SELECT 1 FROM rules r WHERE instr(lower(transactions.description), lower(r.pattern)) > 0);
