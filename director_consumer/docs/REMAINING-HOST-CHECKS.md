# REMAINING-HOST-CHECKS — after the 05C corrective pass

Everything software-side is green (173/173 director, rehearsal 22/22).
Host-only gates remain NOT RUN until the Ryzen exists (no privileged M5
changes, no deployment):

1. Real OS uid separation (service vs operator) + permission enforcement
   on the approvals directory (executable steps in RYZEN-COMMISSIONING.md).
2. systemd restart behavior under kill -9.
3. Real NAS mount/transport identity (`require_existing_root` live).
4. OpenCode version + session binding against the installed schema
   (the SessionTokenTransport contract needs a production adapter that
   establishes origin from OpenCode's authenticated session metadata).
5. Model endpoint policy smoke against real registered endpoints.
6. Backup/restore with receiver-receipt reconciliation on a populated
   workspace.
7. Source freshness behavior during M5 disconnection.

Nothing above is claimed as tested; the software contracts they verify
are proven by the rehearsal and the regression suites.
