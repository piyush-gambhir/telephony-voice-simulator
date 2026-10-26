"""Hosted Twilio machine retry/concurrency behavior, run in Node with an offline Sync fake."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MACHINE_JS = (
    Path(__file__).parents[1]
    / "src/telephony_voice_simulator/pstn/twilio_hosted/machine.js"
)

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const programs = {
  default_scenario: null,
  scenarios: {
    a: {
      on_dtmf: { digits: ['5'], switch: 'connected' },
      sequences: {
        main: [{ audio_file: 'a.wav', terminal: 'record' }],
        connected: [{ terminal: 'record' }],
      },
    },
    b: { sequences: { main: [{ audio_file: 'b.wav', terminal: 'record' }] } },
  },
};

const tick = () => new Promise((resolve) => setTimeout(resolve, Math.random() * 3));
const docs = new Map();
const failure = (status, code) => Object.assign(new Error(String(status)), { status, code });
const clone = (value) => {
  const text = JSON.stringify(value);
  if (text.length > 16384) throw failure(413, 54006);  // Sync's document size limit
  return JSON.parse(text);
};
function documents(name) {
  return {
    async fetch() {
      await tick();
      if (!docs.has(name)) throw failure(404);
      return clone(docs.get(name));
    },
    async update({ data, ifMatch }) {
      await tick();
      const doc = docs.get(name);
      if (!doc) throw failure(404);
      if (ifMatch !== undefined && ifMatch !== doc.revision) throw failure(412);
      docs.set(name, { data: clone(data), revision: doc.revision + 1 });
    },
  };
}
const failCreate = new Set();
documents.create = async ({ uniqueName, data }) => {
  await tick();
  if (failCreate.delete(uniqueName)) throw failure(500);
  if (docs.has(uniqueName)) throw failure(409, 54301);
  docs.set(uniqueName, { data: clone(data), revision: 0 });
};
const client = { sync: { v1: { services: () => ({ documents }) } } };

class VoiceResponse {
  constructor() { this.verbs = []; }
  play(url) { this.verbs.push(['play', url]); }
  gather(attrs) {
    const verb = ['gather', attrs.action, []];
    this.verbs.push(verb);
    return { play: (url) => verb[2].push(url) };
  }
  record(attrs) { this.verbs.push(['record', attrs.action]); }
  redirect(_attrs, url) { this.verbs.push(['redirect', url]); }
  dial() { this.verbs.push(['dial']); return { number() {} }; }
  hangup() { this.verbs.push(['hangup']); }
  reject() { this.verbs.push(['reject']); }
  toString() { return JSON.stringify(this.verbs); }
}
class Response {
  appendHeader() {}
  setBody(body) { this.body = body; }
}

const sandbox = {
  exports: {},
  console,
  Runtime: { getAssets: () => ({ '/programs.json': { open: () => JSON.stringify(programs) } }) },
  Twilio: { twiml: { VoiceResponse }, Response },
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const context = { DOMAIN_NAME: 'sim.example.test', getTwilioClient: () => client };
const call = (event) => new Promise((resolve) => {
  sandbox.exports.handler(context, event, (_err, response) => resolve(JSON.parse(response.body)));
});
const query = (url) => Object.fromEntries(new URL(url).searchParams);

(async () => {
  docs.set('queue-+1555', { data: { queue: ['a', 'b'] }, revision: 0 });
  const [first, second] = await Promise.all([
    call({ mode: 'voice', CallSid: 'CA1', To: '+1555' }),
    call({ mode: 'voice', CallSid: 'CA2', To: '+1555' }),
  ]);
  const retry = await call({ mode: 'voice', CallSid: 'CA1', To: '+1555' });
  const both = await Promise.all([
    call({ mode: 'voice', CallSid: 'CA3', To: '+1556' }),
    call({ mode: 'voice', CallSid: 'CA3', To: '+1556' }),
  ]);

  const gatherCall = docs.get('call-CA1').data.scenario === 'a' ? 'CA1' : 'CA2';
  const answer = gatherCall === 'CA1' ? first : second;
  const gather = { ...query(answer[0][1]), CallSid: gatherCall, Digits: '5' };
  const pressed = await Promise.all([call(gather), call(gather)]);
  const pressedAgain = await call(gather);
  const stale = await call({ ...gather, step: '7' });

  // A claim whose call document failed to save must outlive later traffic for its retry.
  docs.set('queue-+1557', { data: { queue: ['a', ...Array(120).fill('b')] }, revision: 0 });
  failCreate.add('call-CA-unsaved');
  const unsaved = await call({ mode: 'voice', CallSid: 'CA-unsaved', To: '+1557' });
  let rejected = 0;
  for (let i = 0; i < 120; i += 1) {
    const reply = await call({ mode: 'voice', CallSid: `CA-bulk-${i}`, To: '+1557' });
    if (reply[0][0] !== 'play') rejected += 1;
  }
  const unsavedRetry = await call({ mode: 'voice', CallSid: 'CA-unsaved', To: '+1557' });

  console.log(JSON.stringify({
    scenarios: [docs.get('call-CA1').data.scenario, docs.get('call-CA2').data.scenario],
    queue: docs.get('queue-+1555').data.queue,
    retrySame: JSON.stringify(retry) === JSON.stringify(first),
    concurrentSameSid: JSON.stringify(both[0]) === JSON.stringify(both[1]),
    digits: docs.get(`call-${gatherCall}`).data.digits.map((d) => d.digit),
    pressesSame: JSON.stringify(pressed[0]) === JSON.stringify(pressed[1])
      && JSON.stringify(pressedAgain) === JSON.stringify(pressed[0]),
    stale,
    rejected,
    unsaved,
    unsavedRetry,
    bulkQueue: docs.get('queue-+1557').data.queue,
  }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("seed", range(5))
def test_hosted_machine_serializes_queue_and_replays_retries(tmp_path, seed):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS)
    output = subprocess.run(
        ["node", str(harness), str(MACHINE_JS)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    result = json.loads(output.stdout)
    assert sorted(result["scenarios"]) == ["a", "b"]
    assert result["queue"] == []
    assert result["retrySame"] is True
    assert result["concurrentSameSid"] is True
    assert result["digits"] == ["5"]
    assert result["pressesSame"] is True
    assert result["stale"] == [["hangup"]]
    assert result["rejected"] == 0
    assert result["unsaved"] == [["hangup"]]
    assert result["unsavedRetry"][0][2] == ["https://sim.example.test/a.wav"]
    assert result["bulkQueue"] == []
