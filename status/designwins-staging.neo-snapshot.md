# NEO status — {'running': 0, 'suspended': False, 'uptime_sec': 191070, 'version': '1.5.1', 'agents': {'total': 5, 'running': 0, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 2, 'ready': 22, 'open': 30}, 'partial': True, 'partial_errors': ['sessions: loading session snapshot timed out after 1s', 'store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-30T00:38:44Z
- supervisor: ok v1.5.1 uptime 53h
- city running=0 suspended=False agents={'total': 5, 'running': 0, 'suspended': 0, 'quarantined': 0} work={'in_progress': 2, 'ready': 22, 'open': 30}
- sessions: 2 active; pending human interactions: 0
- NEO findings tracked: 5; duplicate groups: 0
- event index: None last_seq=99086

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- idle core.control-dispatcher 
- idle neo-fixture-1 scaled (min=0, max=2)
- idle neo-fixture-2 

## Continuity alerts
- **orchestrator-idle-with-ready-work** {"ready": 22, "running_agents": 0}
- **in-progress-without-live-session** {"in_progress": 2}
- **city-status-partial** {"errors": ["sessions: loading session snapshot timed out after 1s", "store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
