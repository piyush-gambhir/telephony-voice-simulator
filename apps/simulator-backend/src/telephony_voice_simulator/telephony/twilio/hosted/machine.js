
const SYNC_SERVICE = 'default';

function loadPrograms() {
  const asset = Runtime.getAssets()['/programs.json'];
  return JSON.parse(asset.open());
}

const MAX_ATTEMPTS = 5;
const DAY_TTL = 86400;
const CLAIM_TTL = 600;

async function getDoc(client, name) {
  try {
    return await client.sync.v1.services(SYNC_SERVICE).documents(name).fetch();
  } catch (err) {
    if (err && err.status === 404) return null;
    throw err;
  }
}

function isConflict(err) {
  return Boolean(err && (err.status === 412 || err.status === 409 || err.code === 54301));
}

async function mutateDoc(client, name, change) {
  const documents = client.sync.v1.services(SYNC_SERVICE).documents;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    const doc = await getDoc(client, name);
    const next = change(doc ? JSON.parse(JSON.stringify(doc.data)) : null);
    if (!('data' in next)) return next.result;
    try {
      if (doc) {
        await documents(name).update({ data: next.data, ttl: DAY_TTL, ifMatch: doc.revision });
      } else {
        await documents.create({ uniqueName: name, data: next.data, ttl: DAY_TTL });
      }
      return next.result;
    } catch (err) {
      if (!isConflict(err)) throw err;
    }
  }
  throw new Error(`Sync document ${name} kept changing`);
}

function segmentTwiml(vr, context, program, call, seq, index) {
  const segments = (program.sequences[seq] || []);
  if (index >= segments.length) {
    vr.hangup();
    return;
  }
  const segment = segments[index];
  const base = `https://${context.DOMAIN_NAME}`;
  const step = call.steps;
  call.steps += 1;
  const action = `${base}/machine?mode=step&seq=${encodeURIComponent(seq)}&index=${index}&step=${step}`;

  const isFirstMain = seq === 'main' && index === 0;
  const maxRepeats = 3;
  if (program.on_dtmf && isFirstMain && call.prompt_plays < maxRepeats) {
    call.prompt_plays += 1;
    const gather = vr.gather({
      input: 'dtmf',
      numDigits: 1,
      finishOnKey: '',
      timeout: program.gather_timeout || 6,
      actionOnEmptyResult: true,
      action: `${action}&gather=1`,
      method: 'POST',
    });
    if (segment.audio_file) gather.play(`${base}/${segment.audio_file}`);
    return;
  }

  if (segment.terminal === 'reject') {
    vr.reject({ reason: segment.reject_reason || 'rejected' });
    return;
  }
  if (segment.audio_file) vr.play(`${base}/${segment.audio_file}`);
  if (segment.terminal === 'dial') {
    let target = segment.dial_target || '';
    if (target === 'bridge') target = context.BRIDGE_NUMBER || '';
    if (target) {
      const dial = vr.dial({ answerOnBridge: true, callerId: call.to });
      dial.number(target);
    }
    vr.hangup();
  } else if (segment.terminal === 'record') {
    vr.record({
      playBeep: false,
      trim: 'do-not-trim',
      maxLength: segment.record_max_s || 30,
      timeout: 0,
      finishOnKey: '',
      action,
      method: 'POST',
    });
  } else if (segment.terminal === 'hangup') {
    vr.hangup();
  } else {
    vr.redirect({ method: 'POST' }, action);
  }
}

function xml(body) {
  const response = new Twilio.Response();
  response.appendHeader('Content-Type', 'text/xml');
  response.setBody(body);
  return response;
}

exports.handler = async function (context, event, callback) {
  const client = context.getTwilioClient();
  const programs = loadPrograms();
  const mode = event.mode || 'voice';
  const sid = event.CallSid || '';
  const docName = `call-${sid}`;
  const hangup = () => {
    const vr = new Twilio.twiml.VoiceResponse();
    vr.hangup();
    return vr.toString();
  };

  try {
    if (mode === 'voice') {
      const to = event.To || '';
      const existing = await getDoc(client, docName);
      if (existing && existing.data.voice_response) return callback(null, xml(existing.data.voice_response));

      const claimed = await mutateDoc(client, `queue-${to}`, (data) => {
        const queueDoc = data || { queue: [] };
        const claims = queueDoc.claims || {};
        if (claims[sid]) return { result: claims[sid].scenario };
        if (!Array.isArray(queueDoc.queue) || queueDoc.queue.length === 0) return { result: null };
        const scenario = queueDoc.queue.shift();
        const cutoff = Date.now() / 1000 - CLAIM_TTL;
        queueDoc.claims = Object.fromEntries(
          Object.entries(claims).filter(([, claim]) => claim.at > cutoff),
        );
        queueDoc.claims[sid] = { scenario, at: Date.now() / 1000 };
        return { data: queueDoc, result: scenario };
      });
      const scenario = claimed
        || (programs.static_map && programs.static_map[to])
        || programs.default_scenario
        || null;
      if (!scenario || !programs.scenarios[scenario]) {
        const vr = new Twilio.twiml.VoiceResponse();
        vr.reject();
        return callback(null, xml(vr.toString()));
      }
      const answer = await mutateDoc(client, docName, (data) => {
        if (data && data.voice_response) return { result: data.voice_response };
        const call = {
          scenario,
          to,
          from: event.From || '',
          digits: [],
          prompt_plays: 0,
          steps: 0,
          step_responses: {},
          answered_at: Date.now() / 1000,
        };
        const vr = new Twilio.twiml.VoiceResponse();
        segmentTwiml(vr, context, programs.scenarios[scenario], call, 'main', 0);
        call.voice_response = vr.toString();
        return { data: call, result: call.voice_response };
      });
      return callback(null, xml(answer));
    }

    const seq = event.seq || 'main';
    const index = parseInt(event.index || '0', 10);
    const step = String(parseInt(event.step, 10));
    const reply = await mutateDoc(client, docName, (call) => {
      if (!call || !programs.scenarios[call.scenario]) return { result: hangup() };
      const cached = (call.step_responses || {})[step];
      if (cached) return { result: cached };
      if (step === 'NaN' || Number(step) !== call.steps - 1) return { result: hangup() };
      const program = programs.scenarios[call.scenario];
      const vr = new Twilio.twiml.VoiceResponse();

      if (event.gather) {
        const digits = event.Digits || '';
        let switched = false;
        if (digits) {
          call.digits.push({ digit: digits, t: Date.now() / 1000 - call.answered_at });
          const spec = program.on_dtmf;
          if (spec && typeof spec === 'object' && spec.switch) {
            const accepted = (spec.digits || []).map(String);
            if (accepted.length === 0 || accepted.includes(digits)) {
              segmentTwiml(vr, context, program, call, String(spec.switch), 0);
              switched = true;
            }
          }
        }
        if (!switched) {
          if (call.prompt_plays < 3) segmentTwiml(vr, context, program, call, 'main', 0);
          else vr.hangup();
        }
      } else {
        segmentTwiml(vr, context, program, call, seq, index + 1);
      }
      call.step_responses = { ...(call.step_responses || {}), [step]: vr.toString() };
      return { data: call, result: call.step_responses[step] };
    });
    return callback(null, xml(reply));
  } catch (err) {
    console.error('machine error', err && err.message);
    return callback(null, xml(hangup()));
  }
};
