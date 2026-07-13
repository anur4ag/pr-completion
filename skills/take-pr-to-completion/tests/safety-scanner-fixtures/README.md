# pr-completion-safety-test-data

Data-only payloads for merge-ready safety scanner regressions.

Files here may embed forbidden mutation strings as fixtures.
They are exempt from the release safety scan only when:

1. they live under `tests/safety-scanner-fixtures/`;
2. they carry the `pr-completion-safety-test-data` marker; and
3. they are non-executable data (not `.py` / `.sh` helpers under `scripts/`).

A marker line in a production script or executable helper never grants exemption.
