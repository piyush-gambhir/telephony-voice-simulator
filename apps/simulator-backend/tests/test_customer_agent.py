import base64
import hashlib
import hmac
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.control.store import SimulatorStore
from telephony_voice_simulator.pstn import customer_agent as customer
from telephony_voice_simulator.telephony.providers.twilio import webhooks

SID = 'CA' + '1' * 32


@pytest.fixture
async def client(tmp_path, monkeypatch):
    customer.CALLS.clear()
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'+15555550100': 'You are Alex, book the earliest offered visit.'}))
    for k, v in {'PUBLIC_BASE_URL': 'https://sim.example', 'TWILIO_AUTH_TOKEN': 'test-token',
                 'TWILIO_ACCOUNT_SID': 'ACtest', 'PSTN_CUSTOMER_PROFILES': str(path),
                 'PSTN_CUSTOMER_ALLOWED_CALLERS': '+15555550101', 'OPENAI_API_KEY': 'test',
                 'ELEVENLABS_API_KEY': 'test'}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(webhooks, 'CALL_STORE', SimulatorStore(tmp_path / 'calls.sqlite3'))
    app = web.Application()
    customer.register_routes(app)
    async with TestClient(TestServer(app)) as session:
        yield session
    customer.CALLS.clear()


async def signed_post(client, path, **extra):
    data = {'CallSid': SID, 'AccountSid': 'ACtest', 'From': '+15555550101',
            'To': '+15555550100', **extra}
    payload = 'https://sim.example' + path + ''.join(k + data[k] for k in sorted(data))
    sig = base64.b64encode(hmac.new(b'test-token', payload.encode(), hashlib.sha1).digest()).decode()
    return await client.post(path, data=data, headers={'X-Twilio-Signature': sig})


async def test_unsigned_customer_webhook_rejected(client):
    r = await client.post('/twilio/customer', data={'CallSid': SID})
    assert r.status == 403
    assert not customer.CALLS


@pytest.mark.parametrize('changes', [{'To': '+15555550999'}, {'From': '+15555550999'},
                                     {'AccountSid': 'ACother'}])
async def test_customer_accepts_only_configured_line_caller_and_account(client, changes):
    r = await signed_post(client, '/twilio/customer', **changes)
    assert '<Reject' in await r.text()
    assert not customer.CALLS


async def test_turn_retry_reuses_reply_and_generated_audio(client, monkeypatch):
    requests = []
    async def reply(call, speech):
        requests.append(speech)
        return 'Yes, this is Alex.', False
    async def tts(text):
        return b'test-audio'
    monkeypatch.setattr(customer, 'customer_reply', reply)
    monkeypatch.setattr(customer, 'synthesize', tts)
    await signed_post(client, '/twilio/customer')
    first = await signed_post(client, '/twilio/customer/turn?turn=0', SpeechResult='Is this Alex?')
    second = await signed_post(client, '/twilio/customer/turn?turn=0', SpeechResult='Is this Alex?')
    assert await first.text() == await second.text()
    assert requests == ['Is this Alex?']
    assert len(customer.CALLS[SID].audio) == 1
    assert (await client.get('/twilio/customer/audio/' + SID + '/unknown.mp3')).status == 404


async def test_turn_limits_and_bad_turn_fail_closed(client):
    await signed_post(client, '/twilio/customer')
    for turn in ['16', 'wrong', '-1']:
        r = await signed_post(client, '/twilio/customer/turn?turn=' + turn)
        assert '<Hangup' in await r.text()


async def test_generation_failure_does_not_retry_paid_request(client, monkeypatch):
    attempts = []
    async def fail(call, speech):
        attempts.append(1)
        raise TimeoutError('test')
    monkeypatch.setattr(customer, 'customer_reply', fail)
    await signed_post(client, '/twilio/customer')
    for _ in range(2):
        r = await signed_post(client, '/twilio/customer/turn?turn=0', SpeechResult='Hi')
        assert '<Hangup' in await r.text()
    assert len(attempts) == 1


async def test_late_customer_analysis_cannot_reopen_completed_call(client):
    await signed_post(client, '/twilio/customer')
    store = webhooks._call_store()
    store.upsert_incoming_call(call_id=SID, status='completed', duration_s=72,
                               ended_at='2026-10-18T19:38:40Z')
    call = customer.CALLS[SID]
    call.history.append({'role': 'assistant', 'content': 'Goodbye.'})
    customer.persist(SID, call)
    saved = store.get_incoming_call(SID)
    assert saved.status == 'completed'
    assert saved.duration_s == 72
    assert saved.ended_at == '2026-10-18T19:38:40Z'
    assert saved.analysis['customer_dialogue'] == call.history
