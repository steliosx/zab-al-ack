/** Execute real PostgreSQL18 SQL in WASM as supplemental evidence only. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {dependency, bootstrap, sqlFile, repo} from './pglite_support.mjs';
if (!process.argv.includes('--isolated-lab')) throw new Error('Explicit --isolated-lab required');
const {PGlite}=await dependency('pglite');
const db=new PGlite();
const report={started_at_utc:new Date().toISOString(),evidence_layer:'PostgreSQL18 WASM single-backend SQL execution',
  native_postgresql_status:'BLOCKED_ENVIRONMENT',
  limitations:['Not native multi-backend concurrency','No PostgreSQL role login authentication','No PgBouncer','No TLS/cancellation deadline proof','No site-A/site-B failover','No Zabbix server','No production gates satisfied'],
  results:[]};
try {
  const installed=await bootstrap(db);
  Object.assign(report,installed);
  report.results.push({test:'upstream_schema_and_migrations',status:'PASS',scope:'Actual Alerta composite history[] and migrations as non-superuser SET ROLE principals, separate trigger owner'});
  report.server_version_num=(await db.query("SELECT current_setting('server_version_num') AS value")).rows[0].value;
  for(const file of ['tests/postgres/assertions.sql','tests/postgres/assertions_extended.sql','tests/postgres/assertions_boundaries.sql','tests/postgres/assertions_settlement.sql','tests/postgres/assertions_generation.sql','tests/postgres/assertions_expiry.sql','tests/postgres/assertions_late_response.sql']) {
    try {await fs.access(path.join(repo,file));} catch(error){if(error.code==='ENOENT')continue;throw error;}
    const hash=createHash('sha256').update(await fs.readFile(path.join(repo,file))).digest('hex');
    try {
      await sqlFile(db,file);
      report.results.push({test:file,sha256:hash,status:'PASS',scope:'SQL assertions only; HMAC verifier/HTTP source effects separately tested'});
    } catch(error) {
      report.results.push({test:file,sha256:hash,status:'FAIL',reason:error.message,context:error.where??null});
      process.exitCode=1;
      break;
    }
  }
} catch(error) {
  report.results.push({test:'upstream_schema_and_migrations',status:'FAIL',reason:error.message,context:error.where??null});
  process.exitCode=1;
} finally {
  await db.close();
  report.completed_at_utc=new Date().toISOString();
  await fs.mkdir(path.join(repo,'evidence'),{recursive:true});
  await fs.writeFile(process.env.ACK_SQL_REPORT_PATH ?? path.join(repo,'evidence/runtime_wasm_sql.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
}
