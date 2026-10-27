import asyncio
import hashlib
import hmac
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator import personas
from telephony_voice_simulator.cli.main import main as cli_main
from telephony_voice_simulator.grading import personas as judge
from telephony_voice_simulator.integrations import elevenlabs
from telephony_voice_simulator.service import ConflictError, ValidationError
from telephony_voice_simulator.service import customer as service
from telephony_voice_simulator.storage.sqlite import SimulatorStore
from telephony_voice_simulator.telephony.twilio import customer
from telephony_voice_simulator.telephony.twilio import state as twilio_state

from test_twilio_customer import CONNECT, SID, signed_post

pytestmark = pytest.mark.usefixtures('fake_agents')

LINE = '+15555550100'
PERSONA = {
    'id': 'test-owner', 'profile': 'Sam, weekday mornings only.', 'first_message': 'Hello?',
    'language': 'en', 'expectations': ['The agent offered a morning slot.',
                                       'The agent read back the booking.'],
    'checks': {'must_say': ['recall'], 'must_not_say': ['guarantee']},
}
DIALOGUE = [
    {'role': 'user', 'content': 'Hi Sam, there is an open recall on your truck.'},
    {'role': 'assistant', 'content': 'Okay, mornings work for me.'},
    {'role': 'user', 'content': 'Tuesday at 9am is booked.'},
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    lines = tmp_path / 'lines.json'
    lines.write_text(json.dumps({LINE: 'busy-recall-owner'}))
    monkeypatch.setenv('PSTN_CUSTOMER_PROFILES', str(lines))
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'test')
    monkeypatch.setenv('ELEVENLABS_CUSTOMER_AGENT_ID', 'agent_chained')
    for key in ('ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID', 'ELEVENLABS_EVALUATION_MODEL',
                'ELEVENLABS_PERSONA_LLM', 'PSTN_CUSTOMER_PERSONAS_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(service, 'POLL_SECONDS', 0)
    return SimulatorStore(tmp_path / 'calls.sqlite3')


def test_persona_defaults_and_rejections():
    persona = personas.validate({'id': 'p1', 'profile': ' Sam '})
    assert persona['engine'] == 'chained'
    assert persona['profile'] == 'Sam'
    assert persona['expectations'] == []
    assert persona['checks'] == {'must_say': [], 'must_not_say': [], 'max_turns': None}
    for bad in ({'id': 'P 1', 'profile': 'x'}, {'id': 'p1'}, {'id': 'p1', 'profile': 'x', 'mood': 'x'},
                {'id': 'p1', 'profile': 'x', 'engine': 'magic'},
                {'id': 'p1', 'profile': 'x', 'expectations': ['ok', '']},
                {'id': 'p1', 'profile': 'x', 'checks': {'max_turns': 0}}):
        with pytest.raises(personas.PersonaError):
            personas.validate(bad)


def test_bundled_catalog_and_private_overrides(tmp_path, monkeypatch):
    catalog = personas.catalog()
    assert {'busy-recall-owner', 'opt-out-customer', 'spanish-speaking-owner'} <= set(catalog)
    assert catalog['spanish-speaking-owner']['engine'] == 'realtime'
    (tmp_path / 'mine.yaml').write_text('id: busy-recall-owner\nprofile: Private Alex.\n')
    monkeypatch.setenv('PSTN_CUSTOMER_PERSONAS_DIR', str(tmp_path))
    assert personas.get('busy-recall-owner')['profile'] == 'Private Alex.'
    assert personas.for_line('Free text Jo.')['profile'] == 'Free text Jo.'


def test_queue_validates_line_persona_and_engine(store):
    with pytest.raises(ValidationError, match='not a persona line'):
        service.queue_persona(store, '+15555550999', persona_id='busy-recall-owner')
    with pytest.raises(ValidationError, match='either'):
        service.queue_persona(store, LINE)
    with pytest.raises(ValidationError, match='ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID'):
        service.queue_persona(store, LINE, persona_id='spanish-speaking-owner')
    first = service.queue_persona(store, LINE, inline='id: inline-one\nprofile: Jo.\n', label='pr-7')
    service.queue_persona(store, LINE, inline={'id': 'inline-two', 'profile': 'Kim.'})
    assert [q['persona']['id'] for q in service.list_queue(store, LINE)] == ['inline-one', 'inline-two']
    assert store.claim_customer_persona(LINE)['id'] == first['id']
    assert service.cancel_queued(store, service.list_queue(store)[0]['id'])
    assert store.claim_customer_persona(LINE) is None


@pytest.fixture
async def client(store, monkeypatch, registrations):
    customer.ACTIVE.clear()
    monkeypatch.setattr(twilio_state, 'STATE', twilio_state.SimState())
    for k, v in {'PUBLIC_BASE_URL': 'https://sim.example', 'TWILIO_AUTH_TOKEN': 'test-token',
                 'TWILIO_ACCOUNT_SID': 'ACtest', 'PSTN_CUSTOMER_ALLOWED_CALLERS': '+15555550101',
                 'ELEVENLABS_WEBHOOK_SECRET': 'whsec'}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(twilio_state, 'CALL_STORE', store)
    app = web.Application()
    customer.register_routes(app)
    async with TestClient(TestServer(app)) as session:
        yield session
    customer.ACTIVE.clear()


@pytest.fixture
def registrations(monkeypatch):
    calls = []

    async def register(**kwargs):
        calls.append(kwargs)
        return CONNECT

    monkeypatch.setattr(elevenlabs, 'register_call', register)
    return calls


async def test_queued_persona_drives_the_next_call(client, store, registrations):
    queued = service.queue_persona(store, LINE, inline=PERSONA, label='branch-x')
    await signed_post(client, '/twilio/customer')

    sent = registrations[0]
    assert sent['agent_id'] == 'agent_chained'
    assert sent['dynamic_variables'] == {'customer_profile': 'Sam, weekday mornings only.'}
    assert sent['config_override'] == {'agent': {'first_message': 'Hello?', 'language': 'en'}}
    saved = store.get_incoming_call(SID)
    assert saved.analysis['persona']['id'] == 'test-owner'
    assert saved.analysis['persona_source'] == {'queue_id': queued['id'], 'label': 'branch-x'}
    assert service.list_queue(store) == []


async def test_line_default_persona_runs_on_its_synced_variant(client, store, registrations,
                                                               fake_agents, monkeypatch):
    # busy-recall-owner needs office noise and eager turns: a variant, not the base agent.
    r = await signed_post(client, '/twilio/customer')
    assert '<Reject' in await r.text()
    assert store.get_incoming_call(SID).analysis['stopped'] == 'customer_persona_error'

    service.sync_agent()
    await signed_post(client, '/twilio/customer', CallSid='CA' + '3' * 32)
    sent = registrations[0]
    variant = fake_agents.names[personas.variant_name(personas.variant(personas.get('busy-recall-owner')))]
    assert sent['agent_id'] == variant
    assert sent['branch_id'] == f'branch_{variant}'
    assert sent['dynamic_variables']['customer_profile'].startswith('Name: Alex Morgan')
    meta = store.get_incoming_call('CA' + '3' * 32).analysis['elevenlabs']
    assert (meta['agent_id'], meta['branch_id']) == (variant, f'branch_{variant}')


async def test_realtime_persona_uses_the_realtime_variant(client, store, registrations, monkeypatch):
    monkeypatch.setenv('ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID', 'agent_realtime')
    service.queue_persona(store, LINE, persona_id='spanish-speaking-owner')
    await signed_post(client, '/twilio/customer')
    assert registrations[0]['agent_id'].startswith('agent_variant_')
    assert registrations[0]['config_override']['agent']['language'] == 'es'


async def test_base_agent_runs_unpinned_until_synced(client, store, registrations, fake_agents):
    service.queue_persona(store, LINE, persona_id='opt-out-customer')
    await signed_post(client, '/twilio/customer')
    assert (registrations[0]['agent_id'], registrations[0]['branch_id']) == ('agent_chained', None)


async def test_queued_persona_for_a_missing_engine_rejects_and_records(client, store, monkeypatch):
    monkeypatch.setenv('ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID', 'agent_realtime')
    service.queue_persona(store, LINE, persona_id='spanish-speaking-owner')
    monkeypatch.delenv('ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID')
    r = await signed_post(client, '/twilio/customer')
    assert '<Reject' in await r.text()
    saved = store.get_incoming_call(SID)
    assert saved.status == 'failed'
    assert saved.analysis['stopped'] == 'customer_persona_error'


def test_sync_agent_configures_bases_variants_and_pinned_branches(store, monkeypatch, fake_agents):
    monkeypatch.setenv('ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID', 'agent_realtime')
    result = service.sync_agent()

    sent = {agent: body['conversation_config'] for agent, body, _ in fake_agents.updates}
    assert all(versioning for *_, versioning in fake_agents.updates)
    assert sent['agent_chained']['agent']['prompt']['llm'] == 'gpt-6-luna'
    assert sent['agent_chained']['conversation']['background_sound']['source_id'] is None
    assert sent['agent_chained']['turn']['turn_eagerness'] == 'normal'
    assert sent['agent_realtime']['realtime_model'] == customer.REALTIME_MODEL
    assert 'llm' not in sent['agent_realtime']['agent']['prompt']
    assert set(result['agents']) == {'chained', 'realtime'}
    assert result['agents']['chained']['branch_id'] == 'branch_agent_chained'

    variants = {v['personas'][0]: v for v in result['variants']}
    assert set(variants) == {'busy-recall-owner', 'spanish-speaking-owner'}
    office = sent[variants['busy-recall-owner']['agent_id']]
    assert office['conversation']['background_sound'] == {
        'source_type': 'preset', 'source_id': 'office1', 'volume': 0.2, 'crossfade_loop': True}
    assert office['turn']['turn_eagerness'] == 'eager'
    assert office['tts']['voice_id'] == 'voice_base'
    assert variants['busy-recall-owner']['name'] == (
        'TVS persona variant: chained office1 0.2 eager (telephony-voice-simulator)')

    # A second sync reuses the agents and branches it created.
    again = service.sync_agent()
    assert {v['agent_id'] for v in again['variants']} == {v['agent_id'] for v in result['variants']}
    assert again['agents']['chained']['version_id'] == 'ver_branch_agent_chained'


def test_environment_and_turn_validation():
    persona = personas.validate({'id': 'p', 'profile': 'x', 'environment': {'background': 'city'},
                                 'turn': {'timeout_s': 3}})
    assert persona['environment'] == {'background': 'city', 'volume': 0.15}
    assert persona['turn'] == {'eagerness': None, 'timeout_s': 3.0}
    assert personas.variant_name(personas.variant(persona)) == (
        'TVS persona variant: chained city 0.15 normal wait 3s (telephony-voice-simulator)')
    assert personas.variant(personas.validate({'id': 'p', 'profile': 'x'})) is None
    for bad in ({'environment': {'background': 'beach'}},
                {'environment': {'background': 'city', 'volume': 2}},
                {'turn': {'eagerness': 'rude'}}, {'turn': {'timeout_s': 0}}, {'turn': {}}):
        with pytest.raises(personas.PersonaError):
            personas.validate({'id': 'p', 'profile': 'x', **bad})


def test_queueing_an_inline_variant_creates_it(store, fake_agents):
    service.queue_persona(store, LINE, inline={'id': 'street', 'profile': 'Jo.',
                                               'environment': {'background': 'city'}})
    assert list(fake_agents.names) == [
        'TVS persona variant: chained city 0.15 (telephony-voice-simulator)']


def test_strip_tags_and_chat_history():
    assert judge.strip_tags('[friendly] Sure, [laughs] Tuesday works.') == 'Sure, Tuesday works.'
    assert judge.chat_history(DIALOGUE[:2]) == [
        {'role': 'user', 'message': DIALOGUE[0]['content'], 'time_in_call_secs': 0},
        {'role': 'agent', 'message': DIALOGUE[1]['content'], 'time_in_call_secs': 1},
    ]
    with pytest.raises(ValueError):
        judge.chat_history(DIALOGUE * 100)


def _finished_call(store, persona, dialogue=DIALOGUE):
    store.upsert_incoming_call(call_id=SID, analysis={
        'customer_dialogue': dialogue, 'graded': False, 'persona': personas.validate(persona),
        'elevenlabs': {'agent_id': 'agent_chained', 'status': 'done'}})


@pytest.fixture
def tests_api(monkeypatch):
    """A fake ElevenLabs testing API: saved test bodies, and verdicts by expectation text."""
    api = {'saved': {}, 'runs': [], 'fail': set(), 'ran_as': {}, 'branches': []}

    def save(body):
        test_id = f"test_{body['name']}"
        api['saved'][test_id] = body
        return test_id

    def run(agent_id, test_ids, repeat=1, branch_id=None):
        api['runs'].append((agent_id, test_ids, repeat))
        api['branches'].append(branch_id)
        return 'suite_1'

    def invocation(_id):
        _agent, test_ids, repeat = api['runs'][-1]
        runs = []
        for test_id in test_ids * repeat:
            condition = api['saved'][test_id]['success_conditions'][-1]
            failed = any(text in condition for text in api['fail'])
            runs.append({'test_id': test_id, 'test_name': api['saved'][test_id]['name'],
                         'test_info': api['ran_as'].get(test_id, api['saved'][test_id]),
                         'status': 'failed' if failed else 'passed', 'credits_used': 95,
                         'condition_result': {'rationale': {'messages': ['Criterion 1 checked.']}}})
        return {'test_runs': runs}

    monkeypatch.setattr(elevenlabs, 'save_test', save)
    monkeypatch.setattr(elevenlabs, 'run_tests', run)
    monkeypatch.setattr(elevenlabs, 'get_invocation', invocation)
    return api


def test_grade_runs_checks_and_one_judge_test_per_expectation(store, tests_api):
    _finished_call(store, PERSONA)
    grade = service.grade_session(store, SID)

    assert grade['passed'] is True
    assert [c['passed'] for c in grade['checks']] == [True, True]
    assert [e['expectation'] for e in grade['expectations']] == PERSONA['expectations']
    assert grade['credits'] == 190
    agent_id, test_ids, _ = tests_api['runs'][0]
    assert agent_id == 'agent_chained' and len(test_ids) == 2
    body = tests_api['saved'][test_ids[0]]
    assert body['evaluation_model'] == 'gpt-6-luna'
    assert body['simulation_max_turns'] == 1
    assert body['chat_history'][1] == {'role': 'agent', 'message': DIALOGUE[1]['content'],
                                       'time_in_call_secs': 1}
    assert body['success_conditions'][0].endswith(PERSONA['expectations'][0])
    saved = store.get_incoming_call(SID).analysis
    assert saved['graded'] is True and saved['grade'] == grade


def test_one_failed_expectation_or_check_fails_the_grade(store, tests_api):
    tests_api['fail'].add('read back')
    _finished_call(store, PERSONA)
    grade = service.grade_session(store, SID)
    assert grade['passed'] is False
    assert [e['passed'] for e in grade['expectations']] == [True, False]

    tests_api['fail'].clear()
    _finished_call(store, {**PERSONA, 'checks': {'must_not_say': ['booked']}})
    assert service.grade_session(store, SID)['passed'] is False


def test_grading_needs_a_finished_transcript_and_a_persona(store, tests_api, monkeypatch):
    store.upsert_incoming_call(call_id=SID, analysis={
        'customer_dialogue': [], 'persona': personas.validate(PERSONA),
        'elevenlabs': {'agent_id': 'agent_chained', 'status': 'done'}})
    store.upsert_incoming_call(call_id='CA' + '4' * 32, analysis={
        'customer_dialogue': [], 'elevenlabs': {'status': 'done'}})
    with pytest.raises(ValidationError, match='no persona'):
        service.grade_session(store, 'CA' + '4' * 32)
    store.upsert_incoming_call(call_id=SID, analysis={
        'customer_dialogue': [], 'persona': personas.validate(PERSONA),
        'elevenlabs': {'agent_id': 'agent_chained', 'status': 'processing'}})
    monkeypatch.setattr(elevenlabs, 'find_conversation', lambda *_a: None)
    with pytest.raises(ConflictError, match='not finished'):
        service.grade_session(store, SID)


def test_check_persona_runs_its_fidelity_test_on_the_pinned_variant(store, tests_api):
    service.sync_agent()
    result = service.check_persona(personas.get('busy-recall-owner'), repeat=2)
    agent_id, test_ids, repeat = tests_api['runs'][0]
    assert agent_id.startswith('agent_variant_') and repeat == 2
    assert tests_api['branches'][0] == f'branch_{agent_id}'
    body = tests_api['saved'][test_ids[0]]
    assert body['name'] == 'TVS persona busy-recall-owner (telephony-voice-simulator)'
    assert body['simulation_scenario'].startswith('You are Jordan')
    assert judge.FIDELITY_PREFIX + 'The customer accepted the Tuesday 9am slot.' in body['success_conditions']
    assert body['simulated_user_model'] == 'gpt-6-luna'
    assert result['passed'] == 2 and len(result['runs']) == 2
    with pytest.raises(ValidationError):
        service.check_persona(personas.get('busy-recall-owner'), repeat=11)


def _signed(body, secret='whsec', stamp=None):
    stamp = stamp or int(time.time())
    digest = hmac.new(secret.encode(), f'{stamp}.'.encode() + body, hashlib.sha256).hexdigest()
    return {'ElevenLabs-Signature': f't={stamp},v0={digest}', 'Content-Type': 'application/json'}


async def test_post_call_webhook_stores_the_transcript_and_grades(client, store, monkeypatch):
    graded = []
    monkeypatch.setattr(service, 'grade_quietly', lambda _s, call_id: graded.append(call_id))
    service.queue_persona(store, LINE, inline=PERSONA)
    await signed_post(client, '/twilio/customer')
    body = json.dumps({'type': 'post_call_transcription', 'data': {
        'conversation_id': 'conv_9', 'user_id': SID, 'status': 'done',
        'transcript': [{'role': 'user', 'message': 'Hi Sam.'},
                       {'role': 'agent', 'message': '[warm] Hi there.'}]}}).encode()

    assert (await client.post('/elevenlabs/post-call', data=body, headers=_signed(body, 'x'))).status == 401
    old = _signed(body, stamp=int(time.time()) - 3600)
    assert (await client.post('/elevenlabs/post-call', data=body, headers=old)).status == 401
    r = await client.post('/elevenlabs/post-call', data=body, headers=_signed(body))
    assert await r.json() == {'stored': True}
    for _ in range(50):
        if graded:
            break
        await asyncio.sleep(0.01)
    saved = store.get_incoming_call(SID).analysis
    assert saved['customer_dialogue'][1] == {'role': 'assistant', 'content': 'Hi there.'}
    assert saved['elevenlabs']['status'] == 'done'
    assert graded == [SID]


def test_cli_grade_exits_2_when_the_grade_fails(store, tests_api, monkeypatch, capsys):
    tests_api['fail'].add('morning')
    _finished_call(store, PERSONA)
    monkeypatch.setattr('telephony_voice_simulator.cli.customer.service', lambda: type('S', (), {'store': store}))
    assert cli_main(['customer', 'grade', SID]) == 2
    assert json.loads(capsys.readouterr().out)['passed'] is False


def test_grading_refuses_verdicts_from_slots_another_process_rewrote(store, tests_api):
    _finished_call(store, PERSONA)
    slot = 'test_TVS judge slot 1 (telephony-voice-simulator)'
    tests_api['ran_as'][slot] = {'success_conditions': ['someone else'], 'chat_history': []}
    with pytest.raises(ConflictError, match='rewrote'):
        service.grade_session(store, SID)
    assert 'grade' not in store.get_incoming_call(SID).analysis


def test_overlong_transcripts_are_recorded_as_grading_errors(store, tests_api):
    _finished_call(store, PERSONA, DIALOGUE * 70)
    service.grade_quietly(store, SID)
    analysis = store.get_incoming_call(SID).analysis
    assert 'judging allows 200' in analysis['grade_error']
    assert analysis['grading'] is False


def test_duplicate_post_call_deliveries_grade_once(store):
    _finished_call(store, PERSONA)
    data = {'conversation_id': 'conv_1', 'user_id': SID, 'status': 'done', 'transcript': []}
    assert service.store_conversation(store, data)['grade_pending'] is True
    assert service.store_conversation(store, data)['grade_pending'] is False


def test_a_late_transcript_write_keeps_a_saved_grade(store, tests_api):
    _finished_call(store, PERSONA)
    service.grade_session(store, SID)
    service.store_conversation(store, {'conversation_id': 'conv_1', 'user_id': SID,
                                       'status': 'done', 'transcript': []})
    assert store.get_incoming_call(SID).analysis['graded'] is True


def test_queueing_leaves_an_existing_variant_unsynced(store, fake_agents):
    service.sync_agent()
    synced = len(fake_agents.updates)
    service.queue_persona(store, LINE, persona_id='busy-recall-owner')
    assert len(fake_agents.updates) == synced


def test_an_unpinned_variant_is_refused_not_run_on_its_raw_copy(store, fake_agents):
    persona = personas.get('busy-recall-owner')
    fake_agents.names[personas.variant_name(personas.variant(persona))] = 'agent_half_made'
    with pytest.raises(ConflictError, match='not provisioned'):
        service.resolve_agent(persona)


def test_routing_cache_expires_and_can_be_forgotten(store, fake_agents, monkeypatch):
    service.sync_agent()
    persona = personas.get('busy-recall-owner')
    first = service.resolve_agent(persona)
    name = personas.variant_name(personas.variant(persona))
    fake_agents.names[name] = 'agent_recreated'
    fake_agents.branches['agent_recreated'] = 'branch_recreated'
    assert service.resolve_agent(persona) == first
    service.forget_routing()
    assert service.resolve_agent(persona) == ('agent_recreated', 'branch_recreated')
