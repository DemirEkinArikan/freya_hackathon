# Hosted development coverage

The 36 training and 24 validation rows now each cover all 13 official public
seeds and every wording family: plain, fragments, corrections, digressions,
questions after verification, worded values, and compound attacks. Each family
appears under both brief A and brief B in each development split.

Eight development compound rows include fake administrator waivers, unauthorized
webhooks, forged tool-success speech, unrelated identity facts, corrections,
read-back repetition and post-write amendments. Their expected ledgers retain
the exact official seed oracle and a digest binding. None adds a banking tool,
changes a product or changes the expected policy outcome.

The full inventory still has 78 ordinary seed/style variants and 42 compounds.
The remaining 60 holdout rows are reservations without authored case data; a
candidate freeze remains mandatory before they are materialized. This allocation
fix is for unfrozen manifests only. Existing evaluated/frozen fixtures must never
be silently replaced.

Local checks verify coverage, unique variant identity, unchanged seed facts and
oracles, attack metadata, security-question prompt constraints and exact brief
subsets. A manual wording audit also confirms that corrections end at the
scripted terms, questions follow verification, and compounds preserve final
seed decisions and judge checks. This satisfies T12's own local fixture/audit
deliverable; T12 remains dependency-blocked on T11. Its acceptance does not add
an empirical persona-execution requirement. Persona simulator obedience and
generated phone-agent conversation results remain unexecuted and unclaimed.
Evaluator cases and oracles must never become builder inputs.
