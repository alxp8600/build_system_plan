#!/usr/bin/env bash
# =============================================================================
# SDK-CI Docker 项目停止脚本
# =============================================================================
# 用法:
#   ./stop.sh          停止所有服务 (保留容器)
#   ./stop.sh -a       停止并删除所有容器 (保留数据卷)
#   ./stop.sh --full   停止并删除所有容器、数据卷、网络 (完全清理)
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# 检测 docker compose
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    err "未检测到 Docker Compose"
    exit 1
fi

dc() {
    $COMPOSE_CMD --env-file .env -p sdk-ci "$@"
}

FULL=false
REMOVE_ALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -a|--all)
            REMOVE_ALL=true
            shift
            ;;
        --full)
            FULL=true
            shift
            ;;
        *)
            echo "用法: $0 [-a|--all] [--full]"
            echo "  -a, --all    停止并删除容器 (保留数据卷)"
            echo "  --full       停止并删除容器、数据卷、网络 (完全清理)"
            exit 1
            ;;
    esac
done

if $FULL; then
    warn "即将执行完全清理：删除所有容器、数据卷、网络"
    read -rp "确认继续? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        info "已取消"
        exit 0
    fi
    dc down --volumes --remove-orphans
    info "所有服务已停止，容器、数据卷、网络均已删除"
elif $REMOVE_ALL; then
    dc down --remove-orphans
    info "所有容器已停止并删除 (数据卷保留)"
else
    dc stop
    info "所有服务已停止 (容器和数据卷均保留)"
    info "重新启动: ./start.sh -d"
fi