# Transfer testing

A transfer test uses two simulator lines on one call:

| Line | Plays | Proves |
|---|---|---|
| Customer line | A synthetic customer persona, served by `/twilio/customer` | The agent agrees to transfer, and the customer reaches a person (or hears the agent come back when nobody answers) |
| Target line | A `transfer_target` scenario, served by `/twilio/voice` | What reached the person before the bridge (briefing or silence), and that the customer's audio reached them after it |

The two legs are tied together by code words. The target line says
"pineapple" after it answers, and the agent never hears that word before the
bridge, so the customer transcript contains it only when the customer was
really connected. The `transfer-to-sales` persona answers with "lighthouse"
only after a new person joins, so the target recording contains it only when
audio flows both ways. Recordings hold only what the calling side says, never
the simulator's own prompts.

## Scenarios

Each is marked `transfer_target: true`, which keeps it out of AMD benchmarks
that run `all`.

| Scenario | Target line behavior | Pair with persona |
|---|---|---|
| `transfer_target_warm_accept` | Answers, listens to the briefing, says the code word | `transfer-to-sales` |
| `transfer_target_assistant_accept` | Answers, agrees when asked, says the code word | `transfer-to-sales` |
| `transfer_target_cold_accept` | Answers and says the code word; no briefing may arrive | `transfer-to-sales` |
| `transfer_target_decline` | Answers, hears the briefing, declines and hangs up | `transfer-to-sales-unreachable` |
| `transfer_target_busy` | Returns busy without answering (`reject: busy`) | `transfer-to-sales-unreachable` |
| `transfer_target_no_answer` | Rings without answering, then rejects (`reject: no_answer`) | `transfer-to-sales-unreachable` |
| any voicemail scenario, e.g. `stock_voicemail_beep_1000` | The transfer reaches a mailbox; the agent must not bridge into it | `transfer-to-sales-unreachable` |

## Staff personas

A target line can also answer with an ElevenLabs agent playing the employee, for dynamic
transfers a script cannot follow: it hears the assistant's briefing, answers an
assistant-mode "can you take this call?", accepts or declines, and talks to the customer.
Name it in the case's target instead of a scenario:

```yaml
target: {line: "+15550100003", persona: staff-warm-accept}
```

The line keeps its `/twilio/voice` webhook: a queued persona takes the next call, otherwise
the line plays its queued or default scenario, so one line serves both. Staff personas set
`role: staff` and run on their own variant agent (`customer sync-agent` creates it) with an
employee prompt; their expectations are judged with the employee as the agent role. Bundled:
`staff-warm-accept`, `staff-assistant-accept`, `staff-cold-accept` and `staff-decline`. The
customer persona's "pineapple" and the employee's "lighthouse" checks still prove the bridge
both ways. Keep scripted targets for busy, no answer and voicemail, where nobody talks.

A no-answer target works around Twilio answering a call as soon as it runs any
TwiML other than `<Reject>`: the simulator holds its reply to the voice webhook for
`PSTN_NO_ANSWER_RING_S` (default 13 s, under Twilio's 15 s webhook timeout), so the
caller hears ringback, and then rejects. Set the agent's transfer ring timeout below
that so the agent gives up while the line is still ringing; with a longer ring timeout the
agent hears busy instead, and the case tests busy handling.

More personas for transfer packs: `transfer-to-sales-department`, `transfer-to-parts`,
`transfer-to-finance` and `transfer-to-named-person` (all expect the code word),
`after-hours-transfer` (the agent must not transfer and should offer a callback),
`reach-service-desk` (a receptionist routes the caller to a service assistant) and
`service-booker` (a plain service booking).

## Running one case

1. Point the agent's transfer destination at the target line, and the customer
   line's allowed callers (`PSTN_CUSTOMER_ALLOWED_CALLERS`) at the agent's
   inbound number.
2. Queue the target scenario on the target line (a managed number with
   `queued` routing):
   `telephony-voice-sim numbers queue <target number id> transfer_target_warm_accept`
3. Place the customer call:
   `telephony-voice-sim customer dial <customer line> --to <agent inbound number> --persona transfer-to-sales --label <run id>`
4. Grade both legs:
   - customer: `telephony-voice-sim customer grade <customer call id> --wait`
     (`must_say: pineapple` passes only when the customer was bridged)
   - target: the call's grade in `telephony-voice-sim calls` or the console
     (`transcript_contains` / `transcript_excludes` on the target recording)
5. Read the agent's own record of the transfer from its call log. For a
   LiveKit agent this is the consult-room `transfer_status` in its call
   metadata.

The target scenarios record what reaches the target line, so they need
`PSTN_ACKNOWLEDGE_PUBLIC_AUDIO` and the same notice and consent controls as
any recorded scenario.
