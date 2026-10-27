# NEO status — {'running': 0, 'suspended': False, 'uptime_sec': 221997, 'version': '1.5.1', 'agents': {'total': 3, 'running': 0, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 0, 'ready': 0, 'open': 19}, 'partial': True, 'partial_errors': ['city work ready: reading complete ready projection from cache: bead cache unavailable', 'store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-27T17:32:51Z
- supervisor: ok v1.5.1 uptime 70h
- city running=0 suspended=False agents={'total': 3, 'running': 0, 'suspended': 0, 'quarantined': 0} work={'in_progress': 0, 'ready': 0, 'open': 19}
- sessions: 0 active; pending human interactions: 0
- NEO findings tracked: 2; duplicate groups: 0
- event index: None last_seq=37907

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- idle core.control-dispatcher 

## Continuity alerts
- **city-status-partial** {"errors": ["city work ready: reading complete ready projection from cache: bead cache unavailable", "store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
- **finding-without-receipt** {"bead": "ds-0iu", "title": "DW-NEO-GATE-02 \u2014 gate demo full proof"}
- **finding-without-receipt** {"bead": "ds-gb1", "title": "DW-NEO-DEMO-01 \u2014 ISOLATED DEMO bead proves NEO idempotent in"}
