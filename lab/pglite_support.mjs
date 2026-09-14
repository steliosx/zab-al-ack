/** Shared isolated WASM lab bootstrap. No monkeypatches of the database engine. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export async function dependency(name) {
  const bases = [path.join(repo, 'lab/node_modules')];
  for (const base of bases) {
    const candidate = path.join(base, '@electric-sql', name, 'dist/index.js');
    try { await fs.access(candidate); return await import(pathToFileURL(candidate)); } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  throw new Error('Install pinned lab dependencies: npm ci --prefix lab --legacy-peer-deps');
}

export async function sqlFile(db, relative) {
  return db.exec(await fs.readFile(path.join(repo, relative), 'utf8'));
}

export async function bootstrap(db) {
  await db.waitReady;
  await db.exec("SET timezone='UTC'");
  const version = (await db.query('SELECT version() AS version')).rows[0].version;
  if (!version.startsWith('PostgreSQL 18.')) throw new Error('Reference requires PostgreSQL18 engine');
  await sqlFile(db, 'tests/postgres/bootstrap.sql');
  await db.exec('SET ROLE alerta_owner');
  await sqlFile(db, 'lab/upstream/alerta-9.1.0-schema.sql');
  await db.exec('RESET ROLE');
  await sqlFile(db, 'tests/postgres/bootstrap_after_schema.sql');
  await db.exec('SET ROLE zabalack_owner');
  const files = (await fs.readdir(path.join(repo, 'sql/migrations'))).filter(x => x.endsWith('.sql')).sort();
  for (const file of files) await sqlFile(db, 'sql/migrations/' + file);
  await db.exec('RESET ROLE; SET ROLE alerta_owner');
  await sqlFile(db, 'sql/install/10_trigger.sql');
  await db.exec('RESET ROLE');
  return {version, migrations: files};
}
