# NEO status — {'running': 0, 'suspended': False, 'uptime_sec': 220269, 'version': '1.5.1', 'agents': {'total': 3, 'running': 0, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 0, 'ready': 0, 'open': 18}, 'partial': True, 'partial_errors': ['sessions: loading session snapshot timed out after 1s', 'city work ready: reading complete ready projection from cache: bead cache unavailable', 'mail: count timed out after 1s', 'store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-27T17:03:37Z
- supervisor: ok v1.5.1 uptime 70h
- city running=0 suspended=False agents={'total': 3, 'running': 0, 'suspended': 0, 'quarantined': 0} work={'in_progress': 0, 'ready': 0, 'open': 18}
- sessions: 0 active; pending human interactions: 0
- NEO findings tracked: 1; duplicate groups: 0
- event index: None last_seq=37589

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- idle core.control-dispatcher 

## Continuity alerts
- **city-status-partial** {"errors": ["sessions: loading session snapshot timed out after 1s", "city work ready: reading complete ready projection from cache: bead cache unavailable", "mail: count timed out after 1s", "store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
- **finding-without-receipt** {"bead": "ds-gb1", "title": "DW-NEO-DEMO-01 \u2014 ISOLATED DEMO bead proves NEO idempotent in"}
