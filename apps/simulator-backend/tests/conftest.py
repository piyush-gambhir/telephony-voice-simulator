import pytest

from telephony_voice_simulator.integrations import elevenlabs
from telephony_voice_simulator.service import customer as customer_service


class FakeAgents:
    """ElevenLabs agents and branches in memory: what sync-agent and call routing touch."""

    def __init__(self):
        self.names = {}      # variant agent name -> agent id
        self.branches = {}   # agent id -> pinned branch id
        self.updates = []    # (agent id, body, enable_versioning)
        self.voice = 'voice_base'

    def update_agent(self, agent_id, body, enable_versioning=False):
        self.updates.append((agent_id, body, enable_versioning))
        return {'version_id': f'ver_main_{len(self.updates)}'}

    def find_branch(self, agent_id, _name):
        return self.branches.get(agent_id)

    def create_branch(self, agent_id, _name, _parent_version_id, _description):
        self.branches[agent_id] = f'branch_{agent_id}'

    def find_agent(self, name):
        return self.names.get(name)

    def duplicate_agent(self, _agent_id, name):
        self.names[name] = f'agent_variant_{len(self.names) + 1}'
        return self.names[name]

    def get_agent(self, _agent_id):
        return {'conversation_config': {'tts': {'voice_id': self.voice}}}


@pytest.fixture
def fake_agents(monkeypatch):
    agents = FakeAgents()
    for name in ('update_agent', 'find_branch', 'create_branch', 'find_agent',
                 'duplicate_agent', 'get_agent'):
        monkeypatch.setattr(elevenlabs, name, getattr(agents, name))
    monkeypatch.setattr(elevenlabs, 'rebase_branch', lambda *_a: {})
    monkeypatch.setattr(elevenlabs, 'branch_version', lambda a, b: f'ver_{b}')
    monkeypatch.setattr(customer_service, '_VARIANT_AGENTS', {})
    monkeypatch.setattr(customer_service, '_PINNED_BRANCHES', {})
    return agents
