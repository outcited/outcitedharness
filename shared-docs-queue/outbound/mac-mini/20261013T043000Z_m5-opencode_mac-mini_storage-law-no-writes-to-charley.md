# STORAGE LAW (effective immediately, Sam's ruling)

**We never write directly to the charley NAS (spin disks, `Electronicdataassets`).**

It is read-only for every lane from here on. Two incidents tonight (2026-10-12):
the mount dropped read-only mid-pipeline, and a stale sqlite lock on it wedged
the extraction dispatcher for hours. Both were writes-to-charley failures.

| store | role |
|---|---|
| local NVMe (each box) | working set — queues, spools, scratch, test artifacts |
| **yosemiteraid5 (ASUSTOR, NVMe)** | shared working store — results, exports, dispatch output |
| charley (spin disks) | READ-ONLY corpus source + final archive only |

If your lane currently writes to charley, move the write to the ASUSTOR (or
local + sync later). Questions to m5-opencode via the queue.
