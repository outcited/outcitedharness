"""DIRECTOR-01 — continuous impact consumer.

Observation-only loop over existing engineering feeds. Consumes derived
truth, classifies impact deterministically, journals evidence-linked
assessments, and PROPOSES agentmail messages and human escalations.
Never sends, never mutates production, never promotes inference to fact.
"""

__version__ = "0.1.0"
MODE_OBSERVE = "observe"  # the only implemented mode
