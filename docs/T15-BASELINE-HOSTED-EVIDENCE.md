# T15 hosted wording lane: baseline method, 11 October 2026

This page covers the baseline method of the
[hosted wording lane](T15-COVERAGE-ACCOUNTING.md). It records what actually
ran; it is not a comparison. T15 stays open. The machine-readable record,
with build and trial hashes, is
[t15-baseline-hosted-20261011.json](runtime-evidence/t15-baseline-hosted-20261011.json).
Raw events, transcripts, ledgers and credentials stay outside GitHub.

## Coverage

Counted as completed calls out of declared calls. A call is completed only
when it has a behavioral verdict from a complete capture.

| Brief | Split | Declared fixtures | Declared calls | Completed | Pass | Behavioral fail | Uncovered | Why uncovered |
|---|---|---:|---:|---:|---:|---:|---:|---|
| A | train | 26 | 78 | 0 | 0 | 0 | 78 | no baseline A agent: the build failed |
| A | validation | 14 | 42 | 0 | 0 | 0 | 42 | no baseline A agent; validation also gated |
| B | train | 10 | 30 | 0 | 0 | 0 | 30 | all three attempts ended as platform infrastructure failures (incomparable) |
| B | validation | 10 | 30 | 0 | 0 | 0 | 30 | gated: both methods must finish training first |
| **Total** | | **60** | **180** | **0** | **0** | **0** | **180** | |

How the counts are made:

- **A has no agent.** Both baseline A builds failed (see Builds). Each
  selection gets one build per input, so A is uncovered. The build failure is
  recorded as the baseline method's result for brief A. It is not a pass, not
  a weaker result and not a hosted call failure.
- **B training was attempted three times, and every attempt ended in
  repetition 1 on a staging platform failure.** Each attempt opened 10 fresh
  bank sessions and one simulation, so 30 call rows exist. All 30 are
  `infrastructure` rows with no behavioral verdict. They are incomparable, not
  failures, and the 30 declared calls stay uncovered.
- **Validation is gated for both briefs.** The declared stage order is "all
  training for both methods and briefs before any validation". The comparator
  enforces it per brief as `validation_preceded_paired_training`: no validation
  may start before both methods have finished training. No structured hosted
  training has run, so no baseline validation trial was started.

## Selected baseline

The declared baseline was `2d09c31d2f2d25a0e01466e707f560127634cad3`
(`selection_files_sha256` `c1a066bb…`). Its first live A build failed at
publication. The cause was a shared platform-adapter defect, not the
algorithm: the platform stores an integral float such as `1.0` as `1`, and the
type-strict read-back rejected it. PR #149 corrected this on main.

Before any hosted call, the baseline was re-selected as
`07583ed5d3ebb16bbe543404e0758333d5d43a25` (`selection_files_sha256`
`384664e7…`, issue 16 comment 6106828064). It is 2d09c31 plus exactly two
files taken unchanged from `6c9ab1e`, the deployment read-back adapters
`builder/platform.py` and `builder/stored_workflow.py`. Policy, guards,
prompts, validation, runtime, provenance and `build.py` are unchanged, so it
is still the earlier guarded-builder algorithm. It builds full policy, guards
and a workflow through the same authenticated Codex route and mandatory
tool-free audit. It is not a greeting graph.

## Builds

Every build used `gpt-6.1-sol` with high reasoning, live mode, a 1,800-second
deadline and a 300-second export reserve, and no `--test-cases`. Inputs were
the approved Brief A, and Brief A then Brief B for B, with the official
OpenAPI. Raw hashes: `d57c049c…`, `c59c7009…`, `9a8410b1…`. In every build the
fresh tool-free isolation audit passed with no failures.

| Build | Source | Run | Result |
|---|---|---|---|
| A | 2d09c31 | `20261011T071836290580Z` | `failed` (builder) at publication: read-back type defect. Policy frozen on attempt 3; workflow validated (69 rules). Workflow `e4eaf458…` was created on staging, unpublished, and left untouched |
| B | 2d09c31 | `20261011T071840314623Z` | `failed` (builder): policy extraction timed out on attempt 3 after two rejected attempts |
| A | 07583ed | `20261011T080044454002Z` | `failed` (builder): all three policy attempts rejected for declared unsupported constraints |
| B | 07583ed | `20261011T080048530411Z` | **`exported`** after 1,337.3 s; published v1, read back |

Re-selected baseline A: the three rejected attempts declared the same two
gaps every time (paraphrased here):

1. The promise-date rules conflict when today falls after the week's last
   business day, and no scoped exception is given.
2. A closing priority is defined only for a transfer after a recorded promise.

Re-selected baseline B: policy frozen on attempt 2 (one repair, 80 active
rules), then workflow attempt 1 passed validation (94 rules) with verified
guard compilation. Deployment: workflow
`78549564-c7e8-413f-b68f-51aa1e3f358e`, agent
`e94bcf35-d85a-4ae0-bce3-2b736ef95fa3`, version
`1cf8b48a-cb1a-462f-a97d-50e23c28987d` (v1), export `680b10c4…`. Before the
first trial, a locked read-back confirmed:

- the stored graph equals the export (`301410a0…`);
- v1 is the sole published version;
- the phone model is `experimental-5`.

## Hosted conditions

These match the conditions recorded for all eight development trials on
issue 16. Structured trials must use identical values.

| Setting | Value |
|---|---|
| Repetitions | 3 |
| Concurrency | 5 (kit maximum) |
| Call cap | 420 s |
| `--budget-seconds` | 9000 (an abort bound only) |
| Models | phone `experimental-5`; persona and analysis `gemini-3-flash-preview` |
| Selections | dedicated `hosted-{train,validation}-{A,B}.json`, raw hashes `68bd208a…`, `87840e34…`, `6ebb7c7e…`, `e65b11df…` |
| Evaluator | clean detached main `ae40623`, with the deployment manifest read back before, after and on every call |
| Platform gateway retries | `platform_gateway_retries: 5` (PR #156) |
| Bank route | `--bank-connect-address 188.114.96.7`, checked by exact-IP verified TLS before each trial |

Two changes were recorded on issue 16 before use, during this lane:

- **PR #156** (head `fe774a1b…`, merge `ae40623d…`). Before it, a single
  gateway 502 or 504 on a status poll discarded the whole repetition and
  orphaned a simulation that kept running. Platform GETs that return 502, 503
  or 504 are now retried up to five times, 10 s apart. Each retry is recorded
  in the report's `platform_retries`, and the policy is recorded in the
  conditions. Writes, other statuses and bank calls are unchanged.
- **Bank route** (comment 6107469173). System DNS returned two answer sets for
  the bank host: `188.114.96.7`/`.97.7` verified, while `188.114.96.3`/`.97.3`
  timed out. Main's T11 diagnostic therefore passed and failed
  intermittently. The documented optional connect address keeps the
  URL/Host/SNI, public-CA and hostname verification, and changes only the TCP
  destination.

## Hosted trials, baseline B training

| Attempt | Trial | Evaluator / route | Simulation | Outcome |
|---|---|---|---|---|
| 1 | `B-train-20261011T082423Z` | `991847f` / system DNS | `a10f333d…` | infrastructure: status poll HTTP 504 after 93 s; 0 retries available |
| 2 | `B-train-20261011T084530Z` | `991847f` / system DNS | `7c699918…` | infrastructure: status poll HTTP 502 after 90 s; 0 retries available |
| 3 | `B-train-20261011T091257Z` | `ae40623` / `188.114.96.7` | `4322498a…` | infrastructure: about 25 min of clean polling, then a sustained staging outage from 09:38Z. Five recorded poll retries (504/503) and five recorded pin read-back retries (504) all failed |

During the attempt 3 outage, unauthenticated staging requests answered in
under 0.3 s (`/api/health` 200, `/api/v2/workspaces` 401). Authenticated
requests hung for 60 s or more until about 10:06Z. Every trial record is
retained unmodified (`trial.json` and `results.json` hashes are in the
machine-readable record). Repetition results from orphaned simulations were
never harvested into a trial, because that would be a reconstructed record.

Four launches were refused by the bank TLS precondition before the route
decision (08:44:21Z, 09:06:19Z, 09:07:31Z, 09:10:21Z). They opened no session
and no simulation.

## Production bundles

None. A bundle needs complete train and validation trial records
(`benchmarks.bundle.trial_record` requires `status: recorded`, a complete
calendar and every fixture and repetition). No baseline trial is complete.

## Remaining for this method

1. Baseline B training: a fresh attempt under the same conditions once
   simulation `4322498a…` is terminal on the server and staging is stable.
2. Baseline B validation, after structured B training completes, with the
   same evaluator and route.
3. Baseline A stays uncovered for this comparison. Its re-selected source and
   inputs have had their one build.
4. Bundle B (train and validation) with `benchmarks.bundle`, then compare
   against the structured B bundle.

No hosted pass, comparison, candidate freeze, holdout use or issue closure is
claimed.
