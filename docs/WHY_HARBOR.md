# Why HarborWindow is a real operational problem

*This document is about the problem, deliberately not about the architecture.
For the architecture see `docs/ARCHITECTURE.md`; for the evidence index see
`EVIDENCE.md`.*

## The 30-second version

> A small-island freight run has one loaded boat, three sailing slots, and a
> weather window that moves. The met office owns the **forecast**; the vessel's
> operator owns the **limits that forecast is read against**. The shipper owns
> the manifest. The harbourmaster owns the schedule. No one of them can authorise
> a departure alone, and by the time all three agree, the forecast has changed
> again. Harbor is the system that decides when it is actually safe to sail — and
> refuses to let anyone, including the AI, declare that it is.

## Why three specialists, and not one agent with all the facts

The obvious objection is that a single model given every fact could just decide.
The answer is institutional, not technical — and it is worth being precise about
that, because the technical version of the argument is weak.

**The weak version, stated so it can be set aside.** One could argue a single
agent "has no way to know which constraint moved". That is not true in general: a
single implementation can source-version its inputs, timestamp them, and diff
them. Harbor does not rest on that claim.

**The real argument is that four boundaries are separate, and stay separate even
if one model could technically ingest everything.**

| boundary | what it means here |
|---|---|
| **information scope** | the forecast, the manifest and the schedule live in three organisations and three systems; each actor reads only its own facts |
| **action scope** | each actor claims only its own unit of work — `window`, `load`, `slot` — and cannot act inside another's |
| **authority domain** | the harbourmaster does not get to assert that the weather is safe; the shipper does not get to assert that a berth is free. Neither does a model that has read all three |
| **identity / policy boundary** | a managed actor runs under a Google-issued Agent Identity, and what an agent identity may reach is decided by IAM and the Gateway rather than by the code it is running. First demonstrated as two separate proofs on two engines, then converged on 2026-08-31: engine `6110651869841850368` is gateway-bound and runs the actor end-to-end (not for all three actors) — see [`EVIDENCE.md`](../EVIDENCE.md) |

Collapsing those into one agent does not simplify the problem. It manufactures an
authority that does not exist in the real operation, and it is the first thing an
operator would reject — not because the model is untrustworthy, but because the
liability position is not the model's to hold.

**Each constraint also changes on its own clock.** Marine forecasts update
hourly. Manifests change when a truck is late. Slots change when another vessel
overruns its berth. Harbor's answer to that is not agent count — it is the
revision fence: a proposal is bound to the revision it was built from and cannot
commit against a later one, whoever proposed it.

**Someone is accountable when a boat sails badly.** "The model said it was fine"
is not an auditable answer. The record has to show which party reported which
constraint, what was proposed on the strength of it, and who accepted it. That
requirement shapes the architecture whether or not there is an AI in it.

In Harbor these are three bounded actors with genuinely disjoint information:

| actor | owns | cannot see |
|---|---|---|
| `window-agent` | the weather window, and the operating limits it must be read against (`max_wind_kph`, `max_rain_mm`, `crossing_hours`) | cargo weight, which slots exist |
| `cargo-agent` | manifest weight, vessel capacity | weather, sailing schedule |
| `harbormaster-agent` | sailing slots, bookings, island landing cutoff | weather, cargo |

They coordinate by publishing constraints to a shared record, not by reading each
other's data. `window-agent` cannot read `sailing_slots`; it reads the constraint
the harbourmaster published. That indirection is the operation, not an
implementation detail.

## What actually goes wrong

The seeded scenario is a real shape, not a toy. A boat is booked to sail at
12:00, carrying 320 kg against a 500 kg capacity, crossing in 1.5 hours, and it
must land before the island cutoff at 18:00. Sailing slots exist at 12:00, 14:00
and 16:00.

The forecast puts wind over the 35 kph limit at both ends of the crossing at
12:00, 13:00, 16:00 and 17:00. Only the 14:00 departure is safe. Nothing in the
booking knows that. The booking says 12:00.

Then the harder failure, which is the one that motivates the whole design:

> The window agent reads the forecast, correctly works out that 14:00 is the only
> safe departure, and proposes it. While that proposal is in flight, a new
> forecast arrives that makes 14:00 unsafe too. The proposal is still *internally
> correct* — it was right about the world it read. If the system commits it, a
> loaded boat sails into weather the system already knew about.

That is not a hypothetical. It is the ordinary way marginal-window operations go
wrong: a decision was made correctly, conditions moved, and nothing re-asked the
question before the decision became action.

## What bounded coordination plus authoritative verification buys

- **A specialist can be wrong, or silent, without the others inventing its
  facts.** Scope is enforced, so no actor can paper over another's gap.
- **Confidence is not evidence.** Every candidate is recomputed from
  authoritative facts before anything changes. An over-confident proposal is
  rejected on the same terms as a careless one.
- **A correct decision cannot commit into a world that has moved.** Proposals are
  bound to the state they were computed against, so the failure above is
  structurally impossible rather than unlikely.
- **The record is the account.** Who reported what, what was proposed, who
  verified it, and what became operational truth are separate, attributed
  entries — which is what an operator or an insurer would actually ask for.

## Why an AI belongs here at all

Not to decide. To *read a changing world and propose*. Working out that 14:00 is
the only viable departure means combining a forecast, a crossing duration, a
capacity limit, a schedule and a landing cutoff — the kind of small, constant,
tedious reconciliation that humans do badly under time pressure and do not enjoy.
That is worth automating.

What is not worth automating is the authority to declare the result true. Harbor
keeps those two things apart on purpose: the model proposes, and deterministic
code that no model can reach decides what becomes operational truth.

## Why HarborWindow, and not StormSlot

Harbor carries two scenarios, and the choice between them was not made on taste.
The first comparison between them was invalid, and had to be thrown away: at that
point StormSlot *measured* weather but let nothing depend on it — the truck was
dispatched into the storm hour regardless — while HarborWindow had no rejectable
proposal path at all, so it could never demonstrate a refusal. The two failures
ran in opposite directions, which made a side-by-side score actively misleading
rather than merely incomplete: each scenario would have been flattered on exactly
the dimension it could not support.

Both were repaired against the same hard gates — weather must change a decision,
a false proposal must be deterministically rejectable, authoritative state must
move only after verification — and only once both survived those gates on the
common substrate was the flagship chosen.

The weather gate is checked with a negative control rather than by inspection:
the same world is replayed with a benign forecast, and the booked slot must
survive untouched. A scenario that reschedules under calm weather is reacting to
something other than the weather; a scenario whose slot never moves either way is
not being steered by it at all. That control is the only thing that distinguishes
weather being load-bearing from weather being decorative.

Once both scenarios passed, the mechanical gates stopped separating them — which
is the expected outcome, because they are admission criteria rather than a score.
Five of the six are machine-checked by `python -m app.gate`; the sixth, whether a
stranger follows the demo without long narration, is reported `[manual]` rather
than approximated, since an event count is not a proxy for comprehension.

HarborWindow leads because its single
operational decision is legible in one sentence and its constraints are genuinely
held by three parties who cannot each see the others' facts. StormSlot stays,
passing the same gates on the same substrate, because that is the evidence that
the architecture is not built around one story.

## The unlikely hero

Harbourmasters, island freight operators and small marine logistics outfits are
not standard corporate roles, and they are running exactly this decision every
day on spreadsheets, phone calls and weather apps — with real cargo, a real boat
and a real tide. The failure mode is not a bad quarterly report. It is a boat in
weather it should not be in.
