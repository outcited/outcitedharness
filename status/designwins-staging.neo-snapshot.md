# NEO status — {'running': 2, 'suspended': False, 'uptime_sec': 273139, 'version': '1.5.1', 'agents': {'total': 5, 'running': 2, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 2, 'ready': 22, 'open': 28}, 'partial': True, 'partial_errors': ['store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-30T23:26:34Z
- supervisor: ok v1.5.1 uptime 76h
- city running=2 suspended=False agents={'total': 5, 'running': 2, 'suspended': 0, 'quarantined': 0} work={'in_progress': 2, 'ready': 22, 'open': 28}
- sessions: 2 active; pending human interactions: 0
- NEO findings tracked: 5; duplicate groups: 0
- event index: None last_seq=125843

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- RUN core.control-dispatcher 
- RUN neo-fixture-1 scaled (min=0, max=2)
- idle neo-fixture-2 

## Continuity alerts
- **city-status-partial** {"errors": ["store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
