-- Put :quantity :unit of :name in :location, best before :expires_on
-- (YYYY-MM-DD, or null), with :note. If the same thing is already there (same name in
-- any case, location, unit and expiry date), its quantity goes up instead
-- of adding a second row.
UPDATE items
SET quantity = quantity + max(0, :quantity)
WHERE name = trim(:name) COLLATE NOCASE
  AND location = :location
  AND unit = trim(:unit)
  AND expires_on IS :expires_on;

INSERT INTO items (name, quantity, unit, location, expires_on, note)
SELECT trim(:name), max(0, :quantity), trim(:unit), :location, :expires_on, trim(:note)
WHERE length(trim(:name)) > 0
  AND NOT EXISTS (
    SELECT 1 FROM items
    WHERE name = trim(:name) COLLATE NOCASE
      AND location = :location
      AND unit = trim(:unit)
      AND expires_on IS :expires_on
  );
