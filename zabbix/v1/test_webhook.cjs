'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const webhook = require('./webhook.js');
function fixture(name) { return JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures',name+'.params.json'))); }
let passed=0;
const problem=webhook.buildPayload(fixture('problem')).payload;
assert.equal(problem.attributes.zabbix_event_id,'10001'); passed++;
const update=webhook.buildPayload(fixture('human_update')).payload;
assert.equal(update.attributes.zabbix_event_id,'10001'); passed++;
const recovery=webhook.buildPayload(fixture('recovery')).payload;
assert.equal(recovery.attributes.zabbix_event_id,'10001');
assert.equal(recovery.attributes.zabbix_recovery_event_id,'10002'); passed++;
assert.equal(problem.event,recovery.event); assert.equal(problem.resource,recovery.resource); passed++;
assert.equal(webhook.buildPayload(fixture('symptom')).payload.attributes.zabbix_cause_event_id,'9001'); passed++;
assert.equal(webhook.buildPayload(fixture('no_cause_macro')).payload.attributes.zabbix_cause_event_id,null); passed++;
assert.equal(JSON.parse(webhook.execute(fixture('writeback_echo'))).status,'suppressed_echo'); passed++;
// A historical neutral comment is suppressed before resolving any destination.
const legacy=fixture('writeback_echo');
legacy.event_update_message=legacy.event_update_message.replace('[zabalack]', '[ackline]');
Object.defineProperty(legacy,'alerta_api_key',{get(){throw Error('must not access token');}});
Object.defineProperty(legacy,'alerta_api_url',{get(){throw Error('must not resolve URL');}});
assert.equal(JSON.parse(webhook.execute(legacy)).status,'suppressed_echo'); passed++;
const echo=fixture('writeback_echo');
Object.defineProperty(echo,'alerta_api_key',{get(){throw Error('must not access token');}});
Object.defineProperty(echo,'alerta_api_url',{get(){throw Error('must not resolve URL');}});
assert.equal(JSON.parse(webhook.execute(echo)).status,'suppressed_echo'); passed++;
assert.equal(JSON.parse(webhook.execute(fixture('human_update'))).status,'disabled_reference_artifact'); passed++;
assert.throws(()=>webhook.buildPayload({...fixture('problem'),event_id:'{EVENT.ID}'})); passed++;
assert.throws(()=>webhook.buildPayload({...fixture('recovery'),event_recovery_id:'10001'})); passed++;
// Real 7.0.10 renders an unresolvable cause macro as *UNKNOWN* (expression.c); it means no cause.
assert.equal(webhook.buildPayload(fixture('no_cause_unknown')).payload.attributes.zabbix_cause_event_id,null); passed++;
assert.throws(()=>webhook.buildPayload({...fixture('problem'),event_cause_id:'unknown'})); passed++;
assert.throws(()=>webhook.buildPayload({...fixture('problem'),event_cause_id:'*UNKNOWN*x'})); passed++;
process.stdout.write(JSON.stringify({passed,evidence:'synthetic Node contract; not Zabbix macro/API/notification evidence'})+'\n');
