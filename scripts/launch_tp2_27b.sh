#!/usr/bin/env bash
# Launch 27B TP2 training: asus2 (rank 0, master) + dgx1 (rank 1) via 200Gb fabric
set -euo pipefail

ASUS2="samkimasus2@100.68.133.1"
DGX1="samkim@100.81.201.24"
MASTER_IP="10.77.0.5"
MASTER_PORT="29500"

echo "=== Stopping GPU services ==="
ssh -o ConnectTimeout=5 -o BatchMode=yes $ASUS2 "docker stop student-serve 2>/dev/null || true" &
ssh -o ConnectTimeout=5 -o BatchMode=yes $DGX1 "docker stop canon-replica 2>/dev/null || true" &
wait

echo "=== Writing config ==="
ssh -o ConnectTimeout=5 -o BatchMode=yes $ASUS2 "cat > /training/tp2_config.yaml" < /Users/samkim/Harnessv1/deploy/training/llamafactory_power_tables_qwen38_27b_tp2.yaml
ssh -o ConnectTimeout=5 -o BatchMode=yes $DGX1 "cat > /training/tp2_config.yaml" < /Users/samkim/Harnessv1/deploy/training/llamafactory_power_tables_qwen38_27b_tp2.yaml

echo "=== Rank 0 on asus2 ==="
ssh -o ConnectTimeout=5 -o BatchMode=yes $ASUS2 "
nohup docker run --rm --name lora-27b-tp2-r0 \
    --gpus all --network host --shm-size 16g \
    -v /training:/training \
    -e MASTER_ADDR=${MASTER_IP} -e MASTER_PORT=${MASTER_PORT} \
    -e RANK=0 -e WORLD_SIZE=2 -e LOCAL_RANK=0 \
    harness/llamafactory-qwen3-next:20260901 \
    torchrun --nproc_per_node=1 --nnodes=2 --node_rank=0 \
    --master_addr=${MASTER_IP} --master_port=${MASTER_PORT} \
    -m llamafactory.train.tuner /training/tp2_config.yaml \
    > /training/rank0.log 2>&1 &
echo PID=\$!
"

echo "=== Rank 1 on dgx1 ==="
ssh -o ConnectTimeout=5 -o BatchMode=yes $DGX1 "
nohup python3 -m torch.distributed.run \
    --nproc_per_node=1 --nnodes=2 --node_rank=1 \
    --master_addr=${MASTER_IP} --master_port=${MASTER_PORT} \
    -m llamafactory.train.tuner /training/tp2_config.yaml \
    > /training/rank1.log 2>&1 &
echo PID=\$!
"

echo "=== Launched ==="
echo "  tail -f /training/rank0.log  (asus2)"
echo "  tail -f /training/rank1.log  (dgx1)"
