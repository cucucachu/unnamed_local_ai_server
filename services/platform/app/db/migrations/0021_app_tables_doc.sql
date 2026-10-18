-- What each table and column of an app version holds (M22-06,
-- docs/APP_TEMPLATES.md §5.1): parsed from `schema.sql` comments on build,
-- copied on publish. `[{name, description, columns: {column: description}}]`
-- in schema order; versions built before this have none.

ALTER TABLE app_versions ADD COLUMN tables_doc JSONB NOT NULL DEFAULT '[]'::jsonb;
