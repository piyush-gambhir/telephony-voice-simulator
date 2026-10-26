import base64
import asyncio
import hashlib
import hmac
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.storage.sqlite import SimulatorStore
from telephony_voice_simulator.telephony.twilio import customer
from telephony_voice_simulator.telephony.twilio import webhooks

SID = 'CA' + '1' * 32


@pytest.fixture
async def client(tmp_path, monkeypatch):
    customer.CALLS.clear()
    monkeypatch.setattr(webhooks, 'STATE', webhooks.SimState())
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'+15555550100': 'You are Alex, book the earliest offered visit.'}))
    for k, v in {'PUBLIC_BASE_URL': 'https://sim.example', 'TWILIO_AUTH_TOKEN': 'test-token',
                 'TWILIO_ACCOUNT_SID': 'ACtest', 'PSTN_CUSTOMER_PROFILES': str(path),
                 'PSTN_CUSTOMER_ALLOWED_CALLERS': '+15555550101', 'OPENAI_API_KEY': 'test',
                 'ELEVENLABS_API_KEY': 'test', 'ELEVENLABS_VOICE_ID': 'test-voice'}.items():
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


async def test_persona_line_can_place_the_call(client):
    # Placed by the simulator: the persona line is From, the dealership is To.
    r = await signed_post(client, '/twilio/customer', Direction='outbound-api',
                          From='+15555550100', To='+15555550101')
    assert '<Gather' in await r.text()
    assert customer.CALLS[SID].profile.startswith('You are Alex')
    saved = webhooks._call_store().get_incoming_call(SID)
    assert saved.from_address == '+15555550100'
    assert saved.to_address == '+15555550101'
    assert saved.direction == 'outbound'
    customer.persist(SID, customer.CALLS[SID])
    assert webhooks._call_store().get_incoming_call(SID).direction == 'outbound'


async def test_placed_call_still_needs_an_allowed_counterpart(client):
    r = await signed_post(client, '/twilio/customer', Direction='outbound-api',
                          From='+15555550100', To='+15555550999')
    assert '<Reject' in await r.text()
    assert not customer.CALLS


@pytest.mark.parametrize('ending', ['goodbye', 'error', 'limit', 'completed', 'deleted'])
async def test_terminal_customer_cannot_generate_another_turn(client, monkeypatch, ending):
    attempts = []

    async def reply(call, speech):
        attempts.append(speech)
        if ending == 'error':
            raise TimeoutError('test')
        return 'Goodbye.', True

    async def tts(text):
        return b'test-audio'

    monkeypatch.setattr(customer, 'customer_reply', reply)
    monkeypatch.setattr(customer, 'synthesize', tts)
    await signed_post(client, '/twilio/customer')
    if ending in {'completed', 'deleted'}:
        store = webhooks._call_store()
        if ending == 'deleted':
            store.delete_incoming_call(SID)
        else:
            store.upsert_incoming_call(call_id=SID, status='completed')
    elif ending == 'limit':
        await signed_post(client, '/twilio/customer/turn?turn=16')
    else:
        first = await signed_post(client, '/twilio/customer/turn?turn=0')
        if ending == 'goodbye':
            xml = await first.text()
            assert '<Play>' in xml
            assert xml.index('</Play>') < xml.index('<Pause length="8"/>') < xml.index('<Hangup/>')
            retry = await signed_post(client, '/twilio/customer/turn?turn=0')
            assert await retry.text() == xml
    count = len(attempts)
    turn = 1 if ending in {'goodbye', 'error'} else 0
    response = await signed_post(client, f'/twilio/customer/turn?turn={turn}')
    assert '<Hangup' in await response.text()
    assert len(attempts) == count


async def test_deletion_removes_customer_audio_and_rejects_reentry(client):
    await signed_post(client, '/twilio/customer')
    customer.CALLS[SID].audio['known'] = b'audio'
    path = f'/twilio/customer/audio/{SID}/known.mp3'
    assert (await client.get(path)).status == 200
    webhooks._call_store().delete_incoming_call(SID)
    webhooks.evict_call(SID)
    assert SID not in customer.CALLS
    assert (await client.get(path)).status == 404
    response = await signed_post(client, '/twilio/customer')
    assert '<Hangup' in await response.text()
    assert SID not in customer.CALLS


@pytest.mark.parametrize('action', ['delete', 'complete'])
async def test_call_ending_during_generation_discards_reply(client, monkeypatch, action):
    entered, resume = asyncio.Event(), asyncio.Event()
    spoken = []

    async def reply(call, speech):
        entered.set()
        await resume.wait()
        return 'Yes.', False

    async def tts(text):
        spoken.append(text)
        return b'test-audio'

    monkeypatch.setattr(customer, 'customer_reply', reply)
    monkeypatch.setattr(customer, 'synthesize', tts)
    await signed_post(client, '/twilio/customer')
    task = asyncio.create_task(signed_post(client, '/twilio/customer/turn?turn=0'))
    await asyncio.wait_for(entered.wait(), timeout=2)
    store = webhooks._call_store()
    if action == 'delete':
        store.delete_incoming_call(SID)
    else:
        store.upsert_incoming_call(call_id=SID, status='completed')
    resume.set()
    response = await task
    assert '<Play' not in await response.text()
    assert not spoken
    assert not customer.CALLS.get(SID) or not customer.CALLS[SID].audio


async def test_audio_expires_without_another_call_starting(client):
    await signed_post(client, '/twilio/customer')
    call = customer.CALLS[SID]
    call.audio['known'] = b'audio'
    call.started -= 601
    assert (await client.get(f'/twilio/customer/audio/{SID}/known.mp3')).status == 404
    assert SID not in customer.CALLS
