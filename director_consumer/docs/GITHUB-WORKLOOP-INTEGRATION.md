# DIRECTOR-WORKLOOP-01 — existing-service GitHub issue pickup

This change connects M5's `gh_workloop.py` to `DirectorConsumer.run_once()`.
It **does not** implement or authorize mailbox delivery, autonomous agents,
production promotion, or acknowledgments. Do not mistake GitHub comment
evidence for a trusted transport ACK.

## Commissioning (after review; no new daemon)

The project must already be registered in `director.db`. Add these two
flags to the **existing** `harness-director.service` run command:

```
--github-project-id designwins --github-repo outcited/outcitedharness
```

Do not enable the private `outcited/designwins-v2` source until the
Director service has a supported read credential for that repo; if GitHub
denies access, the status snapshot reports `source_state: ERROR` and
`issues_observed: null`, not a fake empty queue. No PAT belongs in argv.

Each normal 600-second tick then runs issue ingest BEFORE project
reconciliation, into the **project-scoped** TaskStore. The branch and PR
do not automatically alter production systemd units.

Status on the Director host:

```sh
cat /var/lib/harness-director/github-workloop-latest.json
ls -t /var/lib/harness-director/briefings/BRIEFING-*.md | head -1
```

The briefing includes task-definition states and notes whether a
`MailboxDelivery.record_ack`-style `acked` message row was observed.
A TaskStore row marked `acknowledged` without that origin is **not
proof of an authenticated delivery**. The adapter does not mutate
an acknowledgment state and does not send mail.

## Reviewer gates

1. Read a new issue on the next real Director tick with no operator
   copy/paste. Re-poll/restart and show no duplicate task/events.
2. Verify `project_id=designwins` using the registered ProjectRegistry,
   not a machine/config directory name. Cross-project task IDs stay scoped.
3. A GitHub issue from an untrusted author cannot set an accountable
   owner, trigger delivery, or grant approval. Missing fields remain
   `untrusted-definition`; source outages remain `ERROR`.
4. Issue/commit comments may show progress but NEVER establish a
   trusted AgentMail ACK by themselves.
5. Use separately approved host UID/recipient/message-scope gates before
   testing *one* actual mailbox delivery, then independently verify the
   receipt. Existing authorization code is unchanged.

References:
- https://github.com/outcited/outcitedharness/issues/2
- https://github.com/outcited/outcitedharness/issues/8
