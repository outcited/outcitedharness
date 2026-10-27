# NEO status — {'running': 0, 'suspended': False, 'uptime_sec': 225642, 'version': '1.5.1', 'agents': {'total': 3, 'running': 0, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 0, 'ready': 0, 'open': 22}, 'partial': True, 'partial_errors': ['city work ready: reading complete ready projection from cache: bead cache unavailable', 'store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-27T18:50:33Z
- supervisor: ok v1.5.1 uptime 71h
- city running=0 suspended=False agents={'total': 3, 'running': 0, 'suspended': 0, 'quarantined': 0} work={'in_progress': 0, 'ready': 0, 'open': 22}
- sessions: 0 active; pending human interactions: 0
- NEO findings tracked: 5; duplicate groups: 0
- event index: None last_seq=38725

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- idle core.control-dispatcher 

## Continuity alerts
- **city-status-partial** {"errors": ["city work ready: reading complete ready projection from cache: bead cache unavailable", "store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
