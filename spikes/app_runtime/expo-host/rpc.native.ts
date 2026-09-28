// On the phone there is no platform server in this spike: expo-sqlite (bundled
// in Expo Go) stands in for the platform's per-instance database.
import * as SQLite from 'expo-sqlite';

const db = SQLite.openDatabaseSync(':memory:');
db.execSync(`CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
INSERT INTO items (name) VALUES ('Milk'), ('Eggs'), ('Bread');`);

export async function rpc(method: string, params: { sql: string; params?: any[] }) {
  const args = (params.params ?? []) as SQLite.SQLiteBindParams;
  if (method === 'db.getAll') return db.getAllAsync(params.sql, args);
  if (method === 'db.getFirst') return db.getFirstAsync(params.sql, args);
  if (method === 'db.run') {
    const r = await db.runAsync(params.sql, args);
    return { changes: r.changes, lastInsertRowId: r.lastInsertRowId };
  }
  throw Object.assign(new Error(method), { code: 'method-not-allowed' });
}
