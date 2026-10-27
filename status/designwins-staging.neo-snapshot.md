# NEO status — {'running': 0, 'suspended': False, 'uptime_sec': 4663, 'version': '1.5.1', 'agents': {'total': 5, 'running': 0, 'suspended': 0, 'quarantined': 0}, 'work': {'in_progress': 0, 'ready': 22, 'open': 23}, 'partial': True, 'partial_errors': ['sessions: resolving scoped store: applying patches: patches.agent[0]: agent patch: name is required', 'mail: beadmail count: listing messages: scanning message beads: bd list: applying patches: patches.agent[0]: agent patch', 'store health: counting retained bead rows: hydrating list fallback: resolving scoped store: applying patches: patches.ag']} @ 2026-10-27T20:51:59Z
- supervisor: ok v1.5.1 uptime 1h
- city running=0 suspended=False agents={'total': 5, 'running': 0, 'suspended': 0, 'quarantined': 0} work={'in_progress': 0, 'ready': 22, 'open': 23}
- sessions: 2 active; pending human interactions: 0
- NEO findings tracked: 5; duplicate groups: 0
- event index: None last_seq=40201

## Agents
- idle bd.dog-1 scaled (min=0, max=2)
- idle bd.dog-2 
- idle core.control-dispatcher 
- idle neo-fixture-1 scaled (min=0, max=2)
- idle neo-fixture-2 

## Continuity alerts
- **orchestrator-idle-with-ready-work** {"ready": 22, "running_agents": 0}
- **city-status-partial** {"errors": ["sessions: resolving scoped store: applying patches: patches.agent[0]: agent patch: name is required", "mail: beadmail count: listing messages: scanning message beads: bd list: applying patches: patches.agent[0]: agent patch: name is required\nbd query (wisps): applying patches: patches.agent[0]: agent patch: name is required", "store health: counting retained bead rows: hydrating list fallback: resolving scoped store: applying patches: patches.agent[0]: agent patch: name is required"]}
