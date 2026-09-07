# Dealer-group PBX simulation

A working model of a car dealership group's phone system, built so questions about
call routing and agent handoff can be answered by running something rather than by
asking the client's telecom vendor.

Valley Auto Group is a fictional example. The vendor names and policy settings
describe this configured model; they do not establish product-wide capabilities
or defaults. Confirm actual switch and SBC settings for a real installation.

The model lives in `apps/simulator-backend/src/telephony_voice_simulator/pbxsim/`:

| File | Contents |
| --- | --- |
| `model.py` | Declarative config types: sites, trunks, coverage paths, hunt groups, queues, IVR menus, class of restriction, transfer policy |
| `dealer_group.py` | Valley Auto Group — the modelled group itself |
| `router.py` | Deterministic router that walks a call and emits a timeline |
| `transfer.py` | Agent-initiated transfer, gated by each rooftop's real limits |
| `scenarios.py` | 30 cases with expected outcomes |
| `validation.py` | Reusable dial-plan validation with errors naming each source |

This is separate from `domains/pbx.py`, which stores four hand-written dealership
call *stories*. This package models the switch, so any call can be routed.

## The modelled group

**Valley Auto Group**, three rooftops that grew by acquisition and therefore run
three different switches — the normal situation, and the reason a single
integration recipe never works across a group.

| Rooftop | Platform | Border element | Main DID | Extensions |
| --- | --- | --- | --- | --- |
| Valley Ford | Avaya IP Office 500 V2 R11 | AudioCodes Mediant 800 | `+1 415 555 0100` | `1xxx` |
| Valley Honda | Cisco CUCM 12.5 + CUBE | Cisco CUBE (ISR 4331) | `+1 415 555 0200` | `2xxx` |
| Valley Used Car Center | NEC SV9100 | Carrier PRI gateway | `+1 415 555 0300` | `3xxx` |

A **group BDC** (Business Development Center) sits physically at the Ford rooftop
and takes sales and service-scheduling overflow for all three stores.

### Numbering plan

Uniform across the group: dial `8` + four digits to reach any station at any
rooftop — `81200` is Ford service advisor 1, `82200` is a Honda advisor. Local
four-digit dialing works within a store. The auto attendants accept
dial-by-extension, so an outside caller can use the same numbers.

### Trunks

| Trunk | Type | Signaling | Channels |
| --- | --- | --- | --- |
| `ford-pri-1` | PRI | Q.931 | 23 |
| `ford-sip-1` | SIP via SBC | SIP | 20 |
| `honda-sip-1` | SIP via CUBE | SIP | 30 |
| `used-pri-1` | Fractional PRI | Q.931 | 8 |
| `tie-ford-honda` | 4-wire E&M tie | wink start | 4 |
| `tie-ford-used` | 2-wire E&M tie | immediate start | 2 |

There is **no Honda-to-Used tie trunk**, so those calls tandem through Ford and
hold a channel on both tie trunks for their whole duration. The tie trunks are the
scarcest resource in the group and fail long before the carrier trunks do.

The two tie trunks differ deliberately. Wink start makes the far end confirm its
digit receiver is ready before digits are outpulsed, so a seize collision (glare)
is detected and retried. Immediate start outpulses after a fixed 70 ms guard time,
so glare destroys the address digits and the call lands on the far-end attendant
instead of the station the caller asked for. Both behaviors are modelled and
testable.

### DID inventory

Includes department direct numbers, two individual DIDs, two marketing tracking
numbers (which change attribution but not routing), and two hosted numbers that
belong to the voice agent. `telephony-voice-sim dealer-map` prints the full list.

## How routing works

A call resolves through string destination references, which is what makes the
config read like a dial plan:

```
ext:1200            hunt:ford-service-advisors     queue:ford-service
ivr:ford-main       time:ford-service-hours        vm:ford-service
attendant:ford      tie:honda/2200                 external:+14155550900
agent:bdc-overflow  announce:ford-holiday          busy / hangup
```

The router walks: carrier trunk seizure (with channel-capacity check) → border
element normalization → inbound route → time condition → auto attendant → hunt
group, queue, or station → coverage path → terminal outcome. It carries a hop
budget and loop detection, and emits a CDR.

Station presence is the dealership-specific part. Alongside `available`, `busy`,
`offline` and `dnd` there is **`with_customer`** — the advisor is at a car with a
customer, so the phone rings out rather than returning busy. Most Ford service
advisors are in that state by default, because that is the actual condition a
voice agent is bought to cover.

Terminal outcomes: `answered_human`, `answered_agent`, `voicemail`, `mailbox_full`,
`answering_service`, `busy`, `congestion`, `no_answer`, `abandoned`,
`unassigned_number`, `loop_detected`, `hop_limit`, `rejected`.

## What the model says about voice agents

Three agent integrations are configured, one per ingress style, plus a fourth on
the worst rooftop:

| Agent | Rooftop | Ingress | Consequence |
| --- | --- | --- | --- |
| `bdc-overflow` | Ford | Registered as extension `1999` | Inside the dial plan; bare extensions are addressable |
| `service-did` | Ford | Own hosted DID, service line forwards to it | Outside the dial plan; depends on the Diversion header to know which rooftop and department the caller wanted |
| `honda-trunk` | Honda | SIP trunk peered with CUBE | Inside the dial plan; REFER works but is consumed by the SBC |
| `used-lot` | Used | Own hosted DID, no SIP relationship | Outside everything; inband DTMF over G.729 |

The transfer matrix falls out of the config rather than being asserted:

| Rooftop | Blind REFER | Attended REFER | Bridge | DTMF re-dial | External transfer | Bare extensions from trunk |
| --- | --- | --- | --- | --- | --- | --- |
| Ford | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ |
| Honda | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ |
| Used | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |

Three findings the model makes concrete:

1. **All three sites support bridging to a reachable target.** It needs no switch
   transfer feature. An outside agent must use a routable station DID, and a full
   inbound trunk can still prevent the handoff. It costs a second leg
   and keeps the agent in the media path for the call's whole life — and that is
   exactly what buys the ability to take the caller back when the target does not
   answer.
2. **No rooftop in this group permits trunk-to-trunk transfer.** Class of
   restriction blocks connecting an inbound trunk call to an outbound trunk, so an
   agent cannot hand a caller to a mobile, an on-call number, or the answering
   service through REFER in this configuration.
3. **A blind transfer that succeeds at the SIP layer can still lose the caller.**
   Honda's CUBE consumes the REFER and completes the transfer itself: the agent is
   released immediately and receives no NOTIFY progress. If the target then rings
   out, or the tie trunk is full, nobody is holding the caller. The
   `agent-blind-transfer-strands-caller` case demonstrates exactly this.

## Channel cost of each integration pattern

For a store with a small trunk group, how the agent is attached is not only a
feature question. It sets how many carrier channels one agent-handled call
occupies, and therefore how many calls the store can take at once.

An agent reached by **forwarding to an outside number** is never handed the call.
The switch holds the inbound leg and re-originates a second leg across its own
trunk group — two channels for the call's whole duration, and a third if the
caller has to be handed back. An agent reached over a **private SIP trunk**, or
registered as a station, is inside the dial plan: the inbound leg is the only
carrier channel involved, and handing back to a station adds none.

On an eight-channel trunk group:

| Pattern | Channels per call | Concurrent agent calls | With a handoff back |
| --- | --- | --- | --- |
| Forwarding to an outside number | 2 | 4 | 2 |
| Agent's own published DID | 2 | 4 | 2 |
| Private SIP trunk | 1 | 8 | 8 |
| Registered as a station | 1 | 8 | 8 |

The published-DID row assumes the dealership forwards the inbound call to that
number. When a carrier routes directly to the agent, choose `carrier_routed`:
the inbound call uses no dealership channel, and a handoff back uses one.

Forwarding halves a store's usable capacity before the agent has done anything,
and cuts it to a quarter once handoffs are involved. `capacity.py` computes this
for any line count, and `forwarded-agent-exhausts-the-last-channel` demonstrates
the failure: the inbound leg takes the last free channel and the switch then has
nothing left to reach the agent with, so the call is lost at the point of
handover. `registered-agent-costs-one-channel` is the same call surviving under
peering.

```bash
uv run telephony-voice-sim dealer-capacity --site used --channels 8
```

## Running it

Every command works without a database, credentials, or network access.

Print the dial plan, trunks, DID inventory, agent integrations, and transfer matrix:

```bash
uv run telephony-voice-sim dealer-map
```

Run the whole case suite (exit code is non-zero if any case regresses):

```bash
uv run telephony-voice-sim dealer-suite
```

List the cases, or run one and read its full timeline:

```bash
uv run telephony-voice-sim dealer-case
```

```bash
uv run telephony-voice-sim dealer-case agent-blind-transfer-strands-caller
```

Route an arbitrary call. This one is a Saturday afternoon service call with every
advisor tied up, followed by the agent bridging the caller to the service manager:

```bash
uv run telephony-voice-sim dealer-call --to +14155550102 --day sat --time 14:00 --presence 1200=busy --presence 1201=busy --presence 1202=busy --presence 1220=busy --transfer-method bridge --transfer-target ext:1210
```

Useful flags on `dealer-call`:

| Flag | Effect |
| --- | --- |
| `--day` / `--time` / `--date` | Move the call across schedules and holidays |
| `--digits` | DTMF entries, repeated once per menu level |
| `--presence EXT=STATE` | Change a station's presence |
| `--occupied TRUNK=N` | Mark N channels busy to force congestion |
| `--glare TRUNK` | Force a seize collision on a tie trunk |
| `--patience` | Seconds the caller will hold before abandoning |
| `--transfer-method` / `--transfer-target` / `--transfer-agent` | Attempt a handoff after the call is answered |

## What the 30 cases cover

**Inbound routing** — direct department DIDs, attendant menus, dial-by-extension
locally and across rooftops, linear vs UCD hunt behavior, invalid-entry retry
budgets, coverage from an unregistered station to a mailbox.

**Time of day** — service closed while sales is open on the same day, after-hours
mailbox that is full, a holiday branch that plays an announcement and hangs up with
no message path at all, and an after-hours forward to a paid answering service.

**Capacity** — an eight-channel PRI saturating during a weekend sale (the model
emits a congestion CDR without answer supervision), tie-trunk exhaustion,
and a caller abandoning the queue before anyone answers.

**Tie-trunk behavior** — tandem routing through Ford, glare misrouting a call to
the wrong store's attendant, and intersite congestion.

**The agent** — queue overflow reaching a registered agent, a forwarded call
arriving on the agent's own DID, and eight transfer cases covering REFER rejection,
successful bridging, bridging into an unexpected voicemail, a 302 attempted after
answer, DTMF re-dial working at one rooftop and losing digits at another,
SBC-consumed REFER, blocked external transfer, and a blind transfer stranding the
caller.

## Extending the model

Add a rooftop by appending a `Site` plus its trunks, extensions, menus and
schedules in `dealer_group.py`. Add a behavior by adding a case to
`scenarios.py` — `test_pbxsim.py` parametrizes over `CASES`, so a new case is
automatically a test.

Use `validate_group(group)` to collect duplicate identifiers, dangling references,
misrouted station DIDs, invalid schedules and invalid capacity or timing values.
Routing, transfers and capacity reports reject invalid groups before execution.
Cycles remain valid inputs so loop detection can be tested as a call outcome.

Create variants with `dataclasses.replace`; replacement groups build their own
lookup index automatically. You do not need to manage a private cache:

```python
from dataclasses import replace
from telephony_voice_simulator.pbxsim import default_group, validate_group

group = default_group()
variant = replace(group, extensions=tuple(
    replace(station, state="available") if station.number == "1200" else station
    for station in group.extensions
))
assert not validate_group(variant)
```

Schedules support overnight windows: `(1320, 120)` opens at 22:00 and closes at
02:00 the next day. A holiday on the current date overrides an overnight opening.
IVR `invalid_target` can send invalid input to a destination immediately; `repeat`
retains the retry behavior.

External forwards reserve an outbound-capable trunk channel. Station coverage
also observes its class-of-restriction rules for external and intersite routing.
Bridged transfers from outside use the target station's DID and inbound capacity;
internal transfers to another site use tie trunks. `TransferAttempt.previous_transfers`
enforces each site's `max_transfer_hops`, and attended REFER requires both the
switch and border element to permit Replaces. Transfer completion timestamps
include the target leg's duration.

## Deliberate limits

This is a routing and policy model, not a media stack. It does not implement SIP
on the wire, carry audio, model jitter or codec transcoding quality, or replace
testing against a real SBC. Timings are representative rather than measured. Its
purpose is to answer *where does this call go, and what can an agent do with it* —
before anyone spends a week on a client's integration.

Each call is simulated independently: callers do not reserve capacity across
separate runs, presence is fixed for a call, and hunt strategies have no persistent
rotation or agent-idle history. Transfer target CDRs account for the new leg;
the original call's occupied channels must be supplied through `occupied_channels`
when evaluating a handoff. The model assumes external numbers answer and does
not simulate their carrier failures or caller-ID behavior.
