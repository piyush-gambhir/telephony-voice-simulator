/**
 * Telephony Voice Simulator: answering machine hosted on Twilio.
 *
 * One protected Function drives every call leg via query params:
 *   /machine?mode=voice                     — answer webhook (number's Voice URL)
 *   /machine?mode=step&seq=X&index=N        — record/segment continuation
 *   /machine?mode=step&seq=X&index=N&gather=1 — DTMF gather result
 *
 * Scenario programs (compiled by the Python PSTN compiler from the same YAML
 * files the room-mode simulator runs) ship as the private asset
 * /programs.json. Per-call state (scenario, digits, prompt plays) and the
 * per-number "next scenario" queue live in Twilio Sync (default service), so
 * the local grader can read digits back and queue scenarios without any
 * public server of ours.
 */

const SYNC_SERVICE = 'default';

function loadPrograms() {
  const asset = Runtime.getAssets()['/programs.json'];
  return JSON.parse(asset.open());
}

async function getDoc(client, name) {
  try {
    const doc = await client.sync.v1.services(SYNC_SERVICE).documents(name).fetch();
    return doc.data;
  } catch (err) {
    return null;
  }
}

async function setDoc(client, name, data, ttl) {
  const svc = client.sync.v1.services(SYNC_SERVICE);
  try {
    await svc.documents(name).update({ data, ttl });
  } catch (err) {
    try {
      await svc.documents.create({ uniqueName: name, data, ttl });
    } catch (err2) {
      // creation race — last writer wins is fine for sim state
      await svc.documents(name).update({ data, ttl });
    }
  }
}

function segmentTwiml(vr, context, program, call, seq, index) {
  const segments = (program.sequences[seq] || []);
  if (index >= segments.length) {
    vr.hangup();
    return;
  }
  const segment = segments[index];
  const base = `https://${context.DOMAIN_NAME}`;
  const action = `${base}/machine?mode=step&seq=${encodeURIComponent(seq)}&index=${index}`;

  const isFirstMain = seq === 'main' && index === 0;
  const maxRepeats = 3;
  if (program.on_dtmf && isFirstMain && call.prompt_plays < maxRepeats) {
    call.prompt_plays += 1;
    const gather = vr.gather({
      input: 'dtmf',
      numDigits: 1,
      // finishOnKey="" so '#' is captured as a DIGIT — by default '#' is
      // Gather's terminator and would return an empty Digits (the pound-gate
      // would then never register).
      finishOnKey: '',
      timeout: program.gather_timeout || 6,
      actionOnEmptyResult: true,
      action: `${action}&gather=1`,
      method: 'POST',
    });
    if (segment.audio_file) gather.play(`${base}/${segment.audio_file}`);
    return;
  }

  if (segment.audio_file) vr.play(`${base}/${segment.audio_file}`);
  if (segment.terminal === 'dial') {
    let target = segment.dial_target || '';
    if (target === 'bridge') target = context.BRIDGE_NUMBER || '';
    if (target) {
      // callerId = the sim line, so the bridged phone sees the sim call.
      const dial = vr.dial({ answerOnBridge: true, callerId: call.to });
      dial.number(target);
    }
    vr.hangup();
  } else if (segment.terminal === 'record') {
    vr.record({
      playBeep: false,
      trim: 'do-not-trim',
      maxLength: segment.record_max_s || 30,
      timeout: 6,
      action,
      method: 'POST',
    });
  } else if (segment.terminal === 'hangup') {
    vr.hangup();
  } else {
    vr.redirect({ method: 'POST' }, action);
  }
}

exports.handler = async function (context, event, callback) {
  const vr = new Twilio.twiml.VoiceResponse();
  const client = context.getTwilioClient();
  const programs = loadPrograms();
  const mode = event.mode || 'voice';
  const sid = event.CallSid || '';
  const docName = `call-${sid}`;
  const dayTtl = 86400;

  try {
    if (mode === 'voice') {
      const to = event.To || '';
      // Precedence: queued one-shot > static per-number map > default.
      let scenario = null;
      const queueName = `queue-${to}`;
      const queueDoc = await getDoc(client, queueName);
      if (queueDoc && Array.isArray(queueDoc.queue) && queueDoc.queue.length > 0) {
        scenario = queueDoc.queue.shift();
        await setDoc(client, queueName, queueDoc, dayTtl);
      } else if (programs.static_map && programs.static_map[to]) {
        scenario = programs.static_map[to];
      } else {
        scenario = programs.default_scenario || null;
      }
      if (!scenario || !programs.scenarios[scenario]) {
        vr.reject();
        return callback(null, vr);
      }
      const call = {
        scenario,
        to,
        from: event.From || '',
        digits: [],
        prompt_plays: 0,
        answered_at: Date.now() / 1000,
      };
      segmentTwiml(vr, context, programs.scenarios[scenario], call, 'main', 0);
      await setDoc(client, docName, call, dayTtl);
      return callback(null, vr);
    }

    // mode === 'step'
    const call = await getDoc(client, docName);
    if (!call || !programs.scenarios[call.scenario]) {
      vr.hangup();
      return callback(null, vr);
    }
    const program = programs.scenarios[call.scenario];
    const seq = event.seq || 'main';
    const index = parseInt(event.index || '0', 10);

    if (event.gather) {
      const digits = event.Digits || '';
      if (digits) {
        call.digits.push({ digit: digits, t: Date.now() / 1000 - call.answered_at });
        const spec = program.on_dtmf;
        if (spec && typeof spec === 'object' && spec.switch) {
          const accepted = (spec.digits || []).map(String);
          if (accepted.length === 0 || accepted.includes(digits)) {
            segmentTwiml(vr, context, program, call, String(spec.switch), 0);
            await setDoc(client, docName, call, dayTtl);
            return callback(null, vr);
          }
        }
        // "ignore" or wrong digit: machine doesn't react — re-prompt below.
      }
      if (call.prompt_plays < 3) {
        segmentTwiml(vr, context, program, call, 'main', 0);
      } else {
        vr.hangup();
      }
      await setDoc(client, docName, call, dayTtl);
      return callback(null, vr);
    }

    segmentTwiml(vr, context, program, call, seq, index + 1);
    await setDoc(client, docName, call, dayTtl);
    return callback(null, vr);
  } catch (err) {
    console.error('machine error', err && err.message);
    vr.hangup();
    return callback(null, vr);
  }
};
