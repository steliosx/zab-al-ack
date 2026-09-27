/** Official PGlite socket adapter for sequential actual-Alerta persistence tests.
 * PostgreSQL wire protocol on loopback only. Not native PostgreSQL, TLS,
 * authentication, cancellation, PgBouncer, concurrent/HA or production evidence.
 */
import {dependency, bootstrap} from './pglite_support.mjs';
if (!process.argv.includes('--isolated-lab')) throw new Error('Explicit --isolated-lab required');
const {PGlite} = await dependency('pglite');
const {PGLiteSocketServer, PGLiteSocketHandler} = await dependency('pglite-socket');
// pglite-socket 0.2.11 releases a connection slot only when a handler dispatches 'close'. A client socket that ends
// in an error (for example ECONNRESET) is detached without that event, so the server keeps counting it. After
// maxConnections such resets, every new client is refused with a plain-text "Too many connections" that psycopg
// reports as "expected authentication request from server, but received T". Dispatch 'close' for an errored,
// active handler so the server releases its slot, as it does for an ordinary close. The engine is not changed.
const handleError = PGLiteSocketHandler.prototype.handleError;
PGLiteSocketHandler.prototype.handleError = function (error) {
  const active = this.active;
  const result = handleError.call(this, error);
  if (active) this.dispatchEvent(new CustomEvent('close'));
  return result;
};
const db = new PGlite();
const evidence = await bootstrap(db);
const server = new PGLiteSocketServer({db, host: '127.0.0.1', port: 55433, maxConnections: 16, inspect: false, debug: false});
await server.start();
console.log(JSON.stringify({event:'isolated_wasm_socket_ready',host:'127.0.0.1',port:55433,database:'postgres',...evidence,
  evidence_layer:'actual PostgreSQL18.3 WASM engine; sequential persisted Alerta supplemental tests only',
  limitations:['single backend','multiplexed connections','no native role authentication','no SSL','cancel ignored','no HA'],production_gates:'NOT_VERIFIED'}));
let stopping = false;
const stop = async () => {
  if(stopping)return;
  stopping=true;
  // Public adapter counters only, retained in the wrapper's private stdout.
  console.log(JSON.stringify({event:'isolated_wasm_socket_stopping',...server.getStats()}));
  await server.stop();
  await db.close();
  process.exit(0);
};
process.once('SIGINT',stop);process.once('SIGTERM',stop);
