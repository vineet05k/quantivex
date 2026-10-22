# Architecture - Quantivex 1.3.0

The LLM plans a sequence of seven allowlisted scientific/visualization tools. The
agent validates finite JSON and strict schemas, starts an isolated numerical worker,
and returns a templated summary of the computed outputs (`narrative.py`), followed by the
model's explanation labelled unverified with unmatched numbers flagged, and the raw evidence. Task relevance/completion remains a review step.
No LLM tool can submit an IBM job.

The numerical path is geometry -> cached/PySCF integrals -> Jordan-Wigner Hamiltonian
-> local noiseless UCCSD -> compact or UCCSD circuit -> target compilation -> grouped
measurements -> covariance-aware estimator -> finite-sample sampling interval.
Singlet and HF spatial-symmetry reference metadata are explicit; ideal VQE spin is checked.
The custom snapshot model supports symmetric CZ only. It remains an approximate
calibration-derived model, not a faithful reconstruction of every device process.

The separate CLI IBM path is prepare -> review -> confirm/shot budget -> submit receipt
-> poll/recover -> counts/result. Actual backend targets support CX/CZ/ECR without
rewriting them as CZ. QPY binds groups and numerical context; instance is reviewed and
checked at submission/recovery/fetch. These are trusted-local-file integrity checks,
not signatures. Plans from earlier releases are intentionally rejected.

GUI defaults to loopback, keeps server secrets out of widget values, and restricts IBM
to local tests. Optional OIDC plus an email allowlist gates all widgets. Server LLM
credentials require that gate and a matching configured endpoint. Real provider, IdP,
network deployment and physical QPU acceptance are separate from local protocol tests.

Tools run in a subprocess with a configurable budget (`QUANTIVEX_TOOL_TIMEOUT`, default 900 s;
POSIX: matching CPU limit and 8 GiB address space).

Each completed tool is checkpointed before another model request. Filesystem failure,
SIGKILL or power loss can prevent a final record update; retained running checkpoints
and events must not be interpreted as completed experiments.


## Batch processing (1.3.0)

`quantivex process` (`process.py`) runs without the agent loop: `ibm_formats.detect_kind` classifies each
input (IBM calibration CSV, IBM BackendProperties JSON, Quantivex snapshot, hardware-run folder), the
calibration files are converted to validated snapshots and simulated with the same functions the tools use,
and hardware folders are re-analysed from IBM's returned counts after plan, circuit and manifest checks. The
language model only writes the closing explanation from the computed summary; `narrative.check_draft` flags
any number in it that matches no result. The output folder is assembled under a temporary name, checksummed,
and renamed when complete.
