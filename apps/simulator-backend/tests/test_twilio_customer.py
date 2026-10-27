import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.integrations import elevenlabs
from telephony_voice_simulator.service import ConflictError, NotFoundError
from telephony_voice_simulator.service import customer as customer_service
from telephony_voice_simulator.storage.sqlite import SimulatorStore
from telephony_voice_simulator.telephony.twilio import customer
from telephony_voice_simulator.telephony.twilio import state as twilio_state

SID = 'CA' + '1' * 32
CONNECT = ('<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
           '<Stream url="wss://api.elevenlabs.io/v1/convai/twilio"/></Connect></Response>')


@pytest.fixture
def registrations(monkeypatch):
    calls = []

    async def register(**kwargs):
        calls.append(kwargs)
        return CONNECT

    monkeypatch.setattr(elevenlabs, 'register_call', register)
    return calls


@pytest.fixture
async def client(tmp_path, monkeypatch, registrations):
    customer.ACTIVE.clear()
    monkeypatch.setattr(twilio_state, 'STATE', twilio_state.SimState())
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'+15555550100': 'You are Alex, book the earliest offered visit.'}))
    for k, v in {'PUBLIC_BASE_URL': 'https://sim.example', 'TWILIO_AUTH_TOKEN': 'test-token',
                 'TWILIO_ACCOUNT_SID': 'ACtest', 'PSTN_CUSTOMER_PROFILES': str(path),
                 'PSTN_CUSTOMER_ALLOWED_CALLERS': '+15555550101', 'ELEVENLABS_API_KEY': 'test',
                 'ELEVENLABS_CUSTOMER_AGENT_ID': 'agent_persona'}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(twilio_state, 'CALL_STORE', SimulatorStore(tmp_path / 'calls.sqlite3'))
    app = web.Application()
    customer.register_routes(app)
    async with TestClient(TestServer(app)) as session:
        yield session
    customer.ACTIVE.clear()


async def signed_post(client, path, **extra):
    data = {'CallSid': SID, 'AccountSid': 'ACtest', 'From': '+15555550101',
            'To': '+15555550100', **extra}
    payload = 'https://sim.example' + path + ''.join(k + data[k] for k in sorted(data))
    sig = base64.b64encode(hmac.new(b'test-token', payload.encode(), hashlib.sha1).digest()).decode()
    return await client.post(path, data=data, headers={'X-Twilio-Signature': sig})


async def test_unsigned_customer_webhook_rejected(client, registrations):
    r = await client.post('/twilio/customer', data={'CallSid': SID})
    assert r.status == 403
    assert not registrations


@pytest.mark.parametrize('changes', [{'To': '+15555550999'}, {'From': '+15555550999'},
                                     {'AccountSid': 'ACother'}])
async def test_customer_accepts_only_configured_line_caller_and_account(client, registrations, changes):
    r = await signed_post(client, '/twilio/customer', **changes)
    assert '<Reject' in await r.text()
    assert not registrations


async def test_missing_agent_configuration_rejects_without_registering(client, registrations, monkeypatch):
    monkeypatch.delenv('ELEVENLABS_CUSTOMER_AGENT_ID')
    r = await signed_post(client, '/twilio/customer')
    assert '<Reject' in await r.text()
    assert not registrations


async def test_allowed_call_is_handed_to_the_persona_agent(client, registrations):
    r = await signed_post(client, '/twilio/customer')

    assert await r.text() == CONNECT
    assert registrations == [{
        'agent_id': 'agent_persona', 'from_number': '+15555550101', 'to_number': '+15555550100',
        'direction': 'inbound', 'user_id': SID,
        'dynamic_variables': {'customer_profile': 'You are Alex, book the earliest offered visit.'},
    }]
    saved = twilio_state.call_store().get_incoming_call(SID)
    assert saved.scenario == 'automated_customer'
    assert saved.analysis['elevenlabs'] == {'agent_id': 'agent_persona', 'status': 'registered'}
    assert saved.analysis['customer_dialogue'] == []


async def test_webhook_retry_reuses_the_registration(client, registrations):
    first = await (await signed_post(client, '/twilio/customer')).text()
    retry = await (await signed_post(client, '/twilio/customer')).text()
    assert first == retry == CONNECT
    assert len(registrations) == 1


async def test_retry_during_registration_waits_instead_of_registering_twice(client, registrations, monkeypatch):
    release = asyncio.Event()

    async def slow(**kwargs):
        registrations.append(kwargs)
        await release.wait()
        return CONNECT

    monkeypatch.setattr(elevenlabs, 'register_call', slow)
    first = asyncio.create_task(signed_post(client, '/twilio/customer'))
    await asyncio.sleep(0.05)
    retry = await (await signed_post(client, '/twilio/customer')).text()
    release.set()
    assert await (await first).text() == CONNECT
    assert '<Redirect method="POST">https://sim.example/twilio/customer</Redirect>' in retry
    assert len(registrations) == 1


async def test_registration_failure_rejects_and_records_the_error(client, monkeypatch):
    async def refuse(**_kwargs):
        raise elevenlabs.ElevenLabsError('ElevenLabs returned 422')

    monkeypatch.setattr(elevenlabs, 'register_call', refuse)
    r = await signed_post(client, '/twilio/customer')

    assert '<Reject' in await r.text()
    assert SID not in customer.ACTIVE
    saved = twilio_state.call_store().get_incoming_call(SID)
    assert saved.status == 'failed'
    assert saved.analysis['stopped'] == 'customer_agent_error'
    assert saved.analysis['error_type'] == 'ElevenLabsError'


async def test_concurrent_calls_are_capped(client, registrations, monkeypatch):
    monkeypatch.setattr(customer, 'MAX_ACTIVE', 1)
    customer.ACTIVE['CA' + '2' * 32] = (customer.time.monotonic(), CONNECT)
    r = await signed_post(client, '/twilio/customer')
    assert '<Hangup' in await r.text()
    assert not registrations


async def test_persona_line_can_place_the_call(client, registrations):
    # Placed by the simulator: the persona line is From, the dealership is To.
    r = await signed_post(client, '/twilio/customer', Direction='outbound-api',
                          From='+15555550100', To='+15555550101')
    assert await r.text() == CONNECT
    assert registrations[0]['direction'] == 'outbound'
    assert registrations[0]['dynamic_variables']['customer_profile'].startswith('You are Alex')
    saved = twilio_state.call_store().get_incoming_call(SID)
    assert (saved.from_address, saved.to_address, saved.direction) == (
        '+15555550100', '+15555550101', 'outbound')


async def test_placed_call_still_needs_an_allowed_counterpart(client, registrations):
    r = await signed_post(client, '/twilio/customer', Direction='outbound-api',
                          From='+15555550100', To='+15555550999')
    assert '<Reject' in await r.text()
    assert not registrations


async def test_deleted_call_cannot_be_registered_again(client, registrations):
    await signed_post(client, '/twilio/customer')
    twilio_state.call_store().delete_incoming_call(SID)
    twilio_state.evict_call(SID)
    assert SID not in customer.ACTIVE
    r = await signed_post(client, '/twilio/customer')
    assert '<Hangup' in await r.text()
    assert len(registrations) == 1


def _api(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


async def test_refresh_stores_the_elevenlabs_transcript(client, registrations, monkeypatch):
    await signed_post(client, '/twilio/customer')
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == '/v1/convai/conversations':
            return httpx.Response(200, json={'conversations': [{'conversation_id': 'conv_1'}]})
        return httpx.Response(200, json={
            'status': 'done', 'metadata': {'termination_reason': 'end_call tool'},
            'transcript': [
                {'role': 'user', 'message': 'Hi, this is Valley Ford about your recall.'},
                {'role': 'agent', 'message': 'Oh great, I can come in Tuesday.'},
                {'role': 'agent', 'message': None},
            ],
        })

    original = elevenlabs._request
    monkeypatch.setattr(elevenlabs, '_request', lambda m, p, c=None, **kw: original(m, p, _api(handler), **kw))
    session = customer_service.refresh_session(twilio_state.call_store(), SID)

    assert seen[0] == ('GET', '/v1/convai/conversations',
                       {'agent_id': 'agent_persona', 'user_id': SID, 'page_size': '1'})
    assert seen[1][1] == '/v1/convai/conversations/conv_1'
    assert session['transcript_status'] == 'done'
    assert session['dialogue'] == [
        {'role': 'user', 'content': 'Hi, this is Valley Ford about your recall.'},
        {'role': 'assistant', 'content': 'Oh great, I can come in Tuesday.'},
    ]
    saved = twilio_state.call_store().get_incoming_call(SID)
    assert saved.analysis['elevenlabs']['conversation_id'] == 'conv_1'
    assert saved.analysis['elevenlabs']['termination_reason'] == 'end_call tool'


async def test_refresh_before_elevenlabs_logs_the_call_keeps_waiting(client, registrations, monkeypatch):
    await signed_post(client, '/twilio/customer')
    original = elevenlabs._request
    empty = _api(lambda _r: httpx.Response(200, json={'conversations': []}))
    monkeypatch.setattr(elevenlabs, '_request', lambda m, p, c=None, **kw: original(m, p, empty, **kw))
    session = customer_service.refresh_session(twilio_state.call_store(), SID)
    assert session['transcript_status'] == 'registered'
    assert session['dialogue'] == []


async def test_refresh_errors_are_service_errors(client, registrations, monkeypatch):
    with pytest.raises(NotFoundError):
        customer_service.refresh_session(twilio_state.call_store(), 'CA' + '9' * 32)
    await signed_post(client, '/twilio/customer')
    original = elevenlabs._request
    down = _api(lambda _r: httpx.Response(503, text='unavailable'))
    monkeypatch.setattr(elevenlabs, '_request', lambda m, p, c=None, **kw: original(m, p, down, **kw))
    with pytest.raises(ConflictError, match='ElevenLabs returned 503'):
        customer_service.refresh_session(twilio_state.call_store(), SID)


def test_sync_agent_pushes_the_settings_this_repository_owns(monkeypatch):
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'test')
    monkeypatch.setenv('ELEVENLABS_CUSTOMER_AGENT_ID', 'agent_persona')
    sent = []

    def handler(request):
        sent.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'agent_id': 'agent_persona'})

    original = elevenlabs._request
    monkeypatch.setattr(elevenlabs, '_request', lambda m, p, c=None, **kw: original(m, p, _api(handler), **kw))
    result = customer_service.sync_agent()

    method, path, body = sent[0]
    assert (method, path) == ('PATCH', '/v1/convai/agents/agent_persona')
    config = body['conversation_config']
    assert '{{customer_profile}}' in config['agent']['prompt']['prompt']
    assert config['tts']['agent_output_audio_format'] == 'ulaw_8000'
    assert config['asr']['user_input_audio_format'] == 'ulaw_8000'
    assert config['conversation']['max_duration_seconds'] == customer.MAX_SECONDS
    assert result['agent_id'] == 'agent_persona'


async def test_a_finished_transcript_is_never_refetched_or_replaced(client, registrations, monkeypatch):
    await signed_post(client, '/twilio/customer')
    store = twilio_state.call_store()
    saved = store.get_incoming_call(SID)
    done = [{'role': 'user', 'content': 'Hi'}, {'role': 'assistant', 'content': 'Hello'}]
    store.upsert_incoming_call(call_id=SID, analysis={
        **saved.analysis, 'customer_dialogue': done,
        'elevenlabs': {'agent_id': 'agent_persona', 'status': 'done', 'conversation_id': 'conv_1'}})

    def unexpected(*_args, **_kwargs):
        raise AssertionError('a finished transcript must not be fetched again')

    monkeypatch.setattr(elevenlabs, '_request', unexpected)
    assert customer_service.refresh_session(store, SID)['dialogue'] == done
