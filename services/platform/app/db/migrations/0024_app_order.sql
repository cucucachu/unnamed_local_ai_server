-- How a user arranged a space's home page (#465): the tiles in order, each
-- an app instance id or a built-in tile's name ("files", "routines",
-- "settings"). Per user, so members arrange the same space differently.
-- Tiles not listed (a new install) come after the listed ones.
CREATE TABLE app_orders (
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    items JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, space_id)
);
