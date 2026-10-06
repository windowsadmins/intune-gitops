# enrollment

One consumer per downstream system, each converging its own system towards the
inventory projection it was handed. No orchestrator, no ordering, no shared
state.

```
inventory.csv
   └── a projection step (lives in each client repo)
         ├── intune.csv  ──> consumers/intune.py   builds the group ladder (here)
         ├── cimian.csv  ──> cimian-gitops: consumers/cimian.py
         └── munki.csv   ──> munki-gitops:  consumers/munki.py
```

This repo holds the shared parts: the naming convention, the Graph client, the
guards, the Intune consumer and the triggers. Each client repo keeps its own
consumer and registers it with the triggers (see "Triggers" below).

## Three properties that make this survivable

**Independent failure.** The blast radius of a bad deploy is one system. If one
consumer is broken, the others neither know nor care.

**No ordering.** Every consumer converges its own system towards the same row,
so the order they run in affects only how long the estate is briefly
inconsistent, not what it converges to.

**Idempotent by construction.** Every consumer is written as "make my system
match this row", never "apply this change". Re-run one twice and the second run
does nothing. You will replay these, usually at the worst moment, and a
consumer that applies deltas will happily apply the same delta twice.

## Running one

Everything plans before it writes:

```
python3 -m consumers.intune out/intune.csv --what-if
```

With no `GRAPH_TOKEN` set, the Intune consumer prints the group plan and stops
without making a single call. That is the first thing to run after forking —
look at the ladder it would build before giving it credentials.

## Layout

| Path | What it is |
|---|---|
| `shared/hierarchy.py` | The naming convention. Inventory columns to group ladder to manifest path, and back. Nothing else builds a group name by hand. |
| `shared/graph.py` | Thin Graph client: paging, group cache, throttle, whatIf. `requests` is imported lazily so the tests need no dependencies. |
| `shared/guards.py` | Desired-set floor and removal cap. These only ever constrain removal. |
| `shared/csvdiff.py` | Change detection against a cached copy, with `FULL_RUN` and `BYPASS_CACHE`. |
| `consumers/intune.py` | The group ladder. Imports nothing cloud-specific. |
| `consumers/registry.py` | Which consumers a trigger can reach. |
| `triggers/azure-blob/` | Azure Functions doorbell. |
| `triggers/generic-webhook/` | The same consumers behind plain HTTP, signed requests only. |

The tests live in `../tests/` with an in-memory Graph: no network, no
credentials, a second to run.

The split between `consumers/` and `triggers/` is the load-bearing one. The blob
trigger is an Azure detail; the convergence logic is not. Keeping the consumers
free of Functions imports is what lets the same code run on a cron box, in a
GitHub Action, or behind the Flask app in `triggers/generic-webhook/`.

## Guards

Adding devices to groups is safe. Removing them is not, and the two failure
modes are worth naming:

- **A collapsed parse.** A wrong path, a shallow checkout, a renamed column —
  the desired set degrades to near-zero, which is a well-formed answer that
  empties every group on a green build. Caught by `desired_floor`.
- **A runaway change.** A bulk edit that moves eight hundred machines because
  somebody sorted a spreadsheet wrong. Caught by `removal_cap`, which skips the
  suspicious group and carries on with the rest rather than failing everything.

Both read their thresholds from the environment. Set them from *your* baseline
and record that baseline in a comment. Defaults inherited from someone else's
estate are decorations, not guards.

| Variable | Default | Meaning |
|---|---|---|
| `MIN_DESIRED_MEMBERSHIPS` | `10` | Floor below which a run is treated as a parse failure |
| `MAX_REMOVAL_PCT` | `10` | Percentage of a group that one run may remove |
| `WHATIF` | unset | `true` to log every write instead of making it |
| `FULL_RUN` | unset | `true` to process every row regardless of the cache |
| `INTUNE_GROUP_PREFIX` | `Devices` | First component of every group name |

## Triggers

Both triggers reach the built-in `intune` consumer. A client repo adds its own
by name, without editing anything here, by putting its enrollment directory on
`PYTHONPATH` and naming the module:

```
export ENROLLMENT_CONSUMERS="cimian=consumers.cimian:converge"
```

The generic webhook rewrites group membership on request, so it fails closed.
Every request must be `Content-Type: text/csv`, which a browser cannot send
cross-origin without a preflight, and must carry two headers:

- `X-Signature-Timestamp`: the current time in unix seconds. A request more
  than five minutes from the server's clock is refused, so a captured request
  cannot be replayed later.
- `X-Signature-256: sha256=<hex>`: an HMAC-SHA256, keyed with `WEBHOOK_SECRET`,
  of the timestamp, a dot, then the raw body.

A missing or stale timestamp or a wrong signature is a 401, any other media type
a 415, and with `WEBHOOK_SECRET` unset every request is refused with a 503.
Compute the signature with:

```
{ printf '%s.' "$TS"; cat out/intune.csv; } | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex
```

Inside the five-minute window a captured request can still be resent. That is
harmless here because every consumer converges to the same state however often
it runs; a consumer that applies deltas would also need a nonce store.

Put the webhook behind TLS; the signature authenticates the body, it does not
hide it.

## Tests

```
python3 -m unittest discover -s ../tests -p 'test_group_ladder.py'
```

Assertions against an in-memory Graph: the ladder shape, the manifest-path round
trip, retired devices never being added, a second run changing nothing, a
collapsed parse removing nothing, a runaway removal being capped, and whatIf
writing nothing.

Gate every sync on these. They are the cheapest insurance in the kit.
