#!/bin/bash
# Continuous phase-2 burn: structured extraction across the corpus, forever.
# Uses student when its endpoint answers; V4.1 fallback otherwise.
cd /Users/samkim/Harnessv1
while true; do
    .venv/bin/python scripts/burn_aisle.py --aisle mcu --limit 500 >> /Volumes/M5_4TB/extract-results/burn-continuous.log 2>&1
    .venv/bin/python scripts/burn_aisle.py --aisle power --limit 500 >> /Volumes/M5_4TB/extract-results/burn-continuous.log 2>&1
    sleep 300
done
