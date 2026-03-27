#!/bin/bash
# 맵/이미지 파일 동기화 스크립트
# Active → Standby 서버로 맵 파일을 동기화합니다.
#
# 사용법:
#   ./sync-maps.sh <standby_ip> [ssh_user]
#
# 예시:
#   ./sync-maps.sh 192.168.0.22 und
#
# crontab 등록 (5분마다):
#   */5 * * * * /home/und/hyunDai-project/scripts/sync-maps.sh 192.168.0.22 und >> /var/log/map-sync.log 2>&1

set -euo pipefail

STANDBY_IP="${1:?사용법: $0 <standby_ip> [ssh_user]}"
SSH_USER="${2:-und}"
MAPS_DIR="/home/${SSH_USER}/hyunDai-project/BackEnd/static/maps/"
REMOTE_DIR="${SSH_USER}@${STANDBY_IP}:${MAPS_DIR}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 맵 동기화 시작 → ${STANDBY_IP}"

rsync -avz --delete \
  "${MAPS_DIR}" \
  "${REMOTE_DIR}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 맵 동기화 완료"
