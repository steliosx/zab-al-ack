/* Zab-al-ack source contract v1; Zabbix 7.0 reference webhook, inert until reviewed.
 * Preserve the operator-supplied stable Alerta resource/event identities.
 * No Alerta acknowledgement route, source acknowledgement, notification,
 * credential lookup, logging, or HTTP object is used before echo filtering.
 */
function required(value, name) {
    if (typeof value !== 'string' || !value.length || value.length > 512 || /\{[A-Z$][^}]*\}/.test(value)) {
        throw 'Invalid required field: ' + name;
    }
    return value;
}
function numericId(value, name, optional) {
    if (optional && (value === null || value === '' || value === '0')) { return null; }
    if (typeof value !== 'string' || !/^[1-9][0-9]{0,19}$/.test(value)) {
        throw 'Invalid numeric identifier: ' + name;
    }
    return value;
}
function isEcho(p) {
    return p.event_update_status === '1' && typeof p.event_update_message === 'string' &&
        /^\[(?:zabalack|ackline)\] Acknowledged by the monitoring operations team\.\nRequested at \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)\. Reference: [A-Za-z0-9_-]{16,128}\.$/.test(p.event_update_message);
}
function buildPayload(p) {
    if (isEcho(p)) { return {suppressed: true, reason: 'recognised_writeback_echo'}; }
    if (p.event_update_status !== '0' && p.event_update_status !== '1') { throw 'Invalid event_update_status'; }
    if (p.event_value !== '0' && p.event_value !== '1') { throw 'Invalid event_value'; }
    var problemId = numericId(p.event_id, 'original problem event', false);
    var recoveryId = p.event_value === '0' ? numericId(p.event_recovery_id, 'recovery event', false) : null;
    if (recoveryId === problemId) { throw 'Recovery must not replace original problem identity'; }
    var payload = {
        resource: required(p.alerta_resource, 'stable resource'),
        event: required(p.alerta_event, 'stable event/family key'),
        environment: required(p.alerta_environment, 'environment'),
        customer: required(p.alerta_customer, 'top-level customer'),
        severity: p.event_value === '0' ? 'normal' : required(p.alerta_severity, 'mapped severity'),
        service: ['Zabbix'],
        origin: 'Zabbix/source-contract-v1',
        attributes: {
            source_system: 'zabbix',
            zabbix_instance: required(p.zabbix_instance, 'logical instance'),
            zabbix_event_id: problemId,
            zabbix_trigger_id: numericId(p.trigger_id, 'trigger', false),
            // A problem without a cause event: 7.0.x get_event_cause_value fails
            // and expression.c substitutes STR_UNKNOWN_VARIABLE ('*UNKNOWN*') for
            // the macro (observed on a real 7.0.10 delivery). An unexpanded macro
            // is treated the same way. Emit null, never a literal ID; any other
            // non-numeric text is still rejected.
            zabbix_cause_event_id: numericId((p.event_cause_id === '{EVENT.CAUSE.ID}' || p.event_cause_id === '*UNKNOWN*') ? null : p.event_cause_id, 'cause', true),
            zabbix_recovery_event_id: recoveryId,
            source_contract_version: '1'
        }
    };
    // Source updates remain facts; never import their acknowledgement as an Alerta /action.
    return {suppressed: false, payload: payload};
}
function execute(p) {
    var result = buildPayload(p);
    if (result.suppressed) { return JSON.stringify({status: 'suppressed_echo'}); }
    if (p.external_writes_enabled !== 'true') { return JSON.stringify({status: 'disabled_reference_artifact'}); }
    var endpoint = required(p.alerta_api_url, 'configured Alerta URL');
    if (!/^https:\/\/[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:\/[A-Za-z0-9_.~-]+)*\/alert$/.test(endpoint)) {
        throw 'An explicit HTTPS Alerta /alert URL without userinfo, query or fragment is required';
    }
    var apiKey = required(p.alerta_api_key, 'customer-bound ingestion key');
    // Peer and host verification against the executing server's CA store (SPEC_AMENDMENTS A2).
    // Honoured by Zabbix 7.0.28 and later in the 7.0 branch; 7.0.31 is the qualified build. Earlier
    // 7.0 builds ignore the argument, so enablement is gated by tools/webhook_transport_check.py.
    // HttpRequest always follows redirects: the approved ingestion route must never return one.
    var request = new HttpRequest({SSLVerifyPeer: true, SSLVerifyHost: true});
    request.addHeader('Content-Type: application/json');
    request.addHeader('Authorization: Key ' + apiKey);
    var response = request.post(endpoint, JSON.stringify(result.payload));
    if (request.getStatus() < 200 || request.getStatus() >= 300) { throw 'Alerta ingestion HTTP failure'; }
    if (typeof response !== 'string' || response.length > 1048576) { throw 'Alerta response limit'; }
    var body;
    try { body = JSON.parse(response); } catch (e) { throw 'Alerta invalid JSON response'; }
    if (body.status !== 'ok') { throw 'Alerta ingestion rejected'; }
    return JSON.stringify({status: 'accepted'});
}
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {buildPayload: buildPayload, isEcho: isEcho, execute: execute};
} else {
    return execute(JSON.parse(value));
}
