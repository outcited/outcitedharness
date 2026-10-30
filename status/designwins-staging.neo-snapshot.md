# NEO status — {'running': 1, 'suspended': False, 'uptime_sec': 265894, 'version': '1.5.1', 'agents': {'total': 5, 'running': 1, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 2, 'ready': 22, 'open': 29}, 'partial': True, 'partial_errors': ['store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-30T21:25:48Z
- supervisor: ok v1.5.1 uptime 74h
- city running=1 suspended=False agents={'total': 5, 'running': 1, 'suspended': 0, 'quarantined': 0} work={'in_progress': 2, 'ready': 22, 'open': 29}
- sessions: 2 active; pending human interactions: 0
- NEO findings tracked: 5; duplicate groups: 0
- event index: None last_seq=123441

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- RUN core.control-dispatcher 
- idle neo-fixture-1 scaled (min=0, max=2)
- idle neo-fixture-2 

## Continuity alerts
- **city-status-partial** {"errors": ["store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
