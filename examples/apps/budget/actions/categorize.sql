-- Put transaction :id in category :category_id (null for none). With a
-- non-empty :pattern, also remember it as a rule for that category (an
-- existing rule with the same pattern moves to it) and apply the rules to
-- every uncategorized transaction.
UPDATE transactions SET category_id = (SELECT id FROM categories WHERE id = :category_id)
WHERE id = :id;

UPDATE rules SET category_id = :category_id
WHERE lower(pattern) = lower(trim(:pattern))
  AND EXISTS (SELECT 1 FROM categories WHERE id = :category_id);

INSERT INTO rules (pattern, category_id)
SELECT trim(:pattern), id FROM categories
WHERE id = :category_id
  AND length(trim(:pattern)) > 0
  AND NOT EXISTS (SELECT 1 FROM rules WHERE lower(pattern) = lower(trim(:pattern)));

UPDATE transactions SET category_id = (
  SELECT r.category_id FROM rules r
  WHERE instr(lower(transactions.description), lower(r.pattern)) > 0
  ORDER BY length(r.pattern) DESC, r.id
  LIMIT 1
)
WHERE category_id IS NULL
  AND length(trim(:pattern)) > 0
  AND EXISTS (SELECT 1 FROM rules r WHERE instr(lower(transactions.description), lower(r.pattern)) > 0);
