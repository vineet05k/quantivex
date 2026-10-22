# Security

## Reporting a problem

Please report security issues privately to the maintainers (see the repository's contact address) rather than
in a public issue. Include the Quantivex version, the command used and, if possible, a minimal input file.

## What Quantivex protects

- **Credentials.** API keys and IBM tokens are read from the environment or IBM's own account store. They are
  never written to results folders, run records or reports. The browser app does not read keys stored in the
  environment unless OIDC sign-in is configured.
- **Hardware spending.** No language-model tool can submit an IBM job. Submission requires
  `quantivex ibm submit --confirm --max-total-shots N` on a prepared, inspected plan.
- **Input files.** Calibration files are parsed as data only (JSON/CSV), with size limits. Hardware folders are
  checked against their recorded circuit hash before use; QPY circuit files should still only be loaded from
  sources you trust. `quantivex process` does not follow symbolic links and never overwrites an existing folder.
- **Privacy.** Results record input paths relative to the folder you passed, not your home directory. Hardware
  folders you share publicly should be redacted first (IBM instance, account name, job IDs and tags); see
  `docs/DATA_AND_OUTPUT_LOCATIONS.md`.
