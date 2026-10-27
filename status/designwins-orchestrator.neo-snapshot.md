# NEO status — {'running': 2, 'suspended': False, 'uptime_sec': 6469, 'version': '1.5.1', 'agents': {'total': 9, 'running': 2, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 1, 'ready': 23, 'open': 32}, 'partial': True, 'partial_errors': ['rig pilot-rig work ready: reading complete ready projection from cache: bead cache unavailable', 'store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-27T21:22:07Z
- supervisor: ok v1.5.1 uptime 1h
- city running=2 suspended=False agents={'total': 9, 'running': 2, 'suspended': 0, 'quarantined': 0} work={'in_progress': 1, 'ready': 23, 'open': 32}
- sessions: 2 active; pending human interactions: 0
- NEO findings tracked: 0; duplicate groups: 3
- event index: None last_seq=62644

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- RUN core.control-dispatcher 
- idle pilot-rig/core.control-dispatcher 
- idle harness/core.control-dispatcher 
- RUN engineer-1 scaled (min=0, max=2)
- idle engineer-2 
- idle harness/harness-engineer-1 scaled (min=0, max=2)
- idle harness/harness-engineer-2 

## Continuity alerts
- **city-status-partial** {"errors": ["rig pilot-rig work ready: reading complete ready projection from cache: bead cache unavailable", "store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
- **duplicate-findings** {"items": [{"task_id": "PIN-TABLES-VISION-01", "beads": ["hw-b5l", "do-y5b"]}, {"task_id": "CURVES-WINTER-01", "beads": ["hw-wfn", "do-77q"]}, {"task_id": "PACKAGE-TEMP-AXES-01", "beads": ["hw-37d", "do-b6p"]}]}
