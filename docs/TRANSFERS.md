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
| any voicemail scenario, e.g. `stock_voicemail_beep_1000` | The transfer reaches a mailbox; the agent must not bridge into it | `transfer-to-sales-unreachable` |

Not covered yet: a target that rings without answering. Twilio answers a call
as soon as it runs any TwiML other than `<Reject>`, so a no-answer target needs
a number with no voice URL or a carrier-side setup.

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
