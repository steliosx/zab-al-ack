/** Official PGlite socket adapter for sequential actual-Alerta persistence tests.
 * PostgreSQL wire protocol on loopback only. Not native PostgreSQL, TLS,
 * authentication, cancellation, PgBouncer, concurrent/HA or production evidence.
 */
import {dependency, bootstrap} from './pglite_support.mjs';
if (!process.argv.includes('--isolated-lab')) throw new Error('Explicit --isolated-lab required');
const {PGlite} = await dependency('pglite');
const {PGLiteSocketServer} = await dependency('pglite-socket');
const db = new PGlite();
const evidence = await bootstrap(db);
const server = new PGLiteSocketServer({db, host: '127.0.0.1', port: 55433, maxConnections: 16, inspect: false, debug: false});
await server.start();
console.log(JSON.stringify({event:'isolated_wasm_socket_ready',host:'127.0.0.1',port:55433,database:'postgres',...evidence,
  evidence_layer:'actual PostgreSQL18.3 WASM engine; sequential persisted Alerta supplemental tests only',
  limitations:['single backend','multiplexed connections','no native role authentication','no SSL','cancel ignored','no HA'],production_gates:'NOT_VERIFIED'}));
let stopping = false;
const stop = async () => {if(stopping)return;stopping=true;await server.stop();await db.close();process.exit(0);};
process.once('SIGINT',stop);process.once('SIGTERM',stop);
