# NEO status — {'running': 0, 'suspended': False, 'uptime_sec': 5643, 'version': '1.5.1', 'agents': {'total': 5, 'running': 0, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 0, 'ready': 21, 'open': 22}, 'partial': True, 'partial_errors': ['sessions: loading session snapshot timed out after 1s', 'store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded']} @ 2026-10-31T03:58:03Z
- supervisor: ok v1.5.1 uptime 1h
- city running=0 suspended=False agents={'total': 5, 'running': 0, 'suspended': 0, 'quarantined': 0} work={'in_progress': 0, 'ready': 21, 'open': 22}
- sessions: 0 active; pending human interactions: 0
- NEO findings tracked: 5; duplicate groups: 0
- event index: None last_seq=130573

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- idle core.control-dispatcher 
- idle neo-fixture-1 scaled (min=0, max=2)
- idle neo-fixture-2 

## Continuity alerts
- **orchestrator-idle-with-ready-work** {"ready": 21, "running_agents": 0}
- **city-status-partial** {"errors": ["sessions: loading session snapshot timed out after 1s", "store health: counting retained bead rows: hydrating list fallback: list timed out: context deadline exceeded"]}
