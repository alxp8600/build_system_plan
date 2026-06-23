#!/usr/bin/env bash
# =============================================================================
# SDK-CI Docker 项目启动脚本
# =============================================================================
# 用法:
#   ./start.sh             拉取/构建镜像并启动所有服务 (前台运行)
#   ./start.sh -d          后台运行所有服务
#   ./start.sh --build     强制重新构建本地镜像后启动
#   ./start.sh -d --build  后台运行 + 强制重建
#   ./start.sh down        停止并移除所有服务
#   ./start.sh logs        查看所有服务日志
#   ./start.sh ps          查看服务运行状态
#   ./start.sh restart     重启所有服务
# =============================================================================

set -euo pipefail

# ========================= 颜色输出 =========================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "${CYAN}[STEP]${NC} $*"; }

# ========================= 前置检查 =========================
check_prerequisites() {
    # 检查 docker
    if ! command -v docker &>/dev/null; then
        err "未检测到 docker，请先安装 Docker Engine"
        exit 1
    fi

    if ! docker info &>/dev/null 2>&1; then
        err "Docker 未运行或当前用户无权限，请启动 Docker 并确保用户已加入 docker 组"
        exit 1
    fi

    # 检查 docker compose 子命令 (v2) 或 docker-compose (v1)
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
        info "检测到 Docker Compose v2"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        info "检测到 Docker Compose v1"
    else
        err "未检测到 Docker Compose，请安装 docker compose plugin 或 docker-compose"
        exit 1
    fi

    # 检查 .env 文件
    if [ ! -f ".env" ]; then
        err ".env 文件不存在，请确保已创建环境变量文件"
        exit 1
    fi

    # 检查 compose.yaml
    if [ ! -f "compose.yaml" ]; then
        err "compose.yaml 文件不存在"
        exit 1
    fi
}

# ========================= 基础操作 =========================
dc() {
    $COMPOSE_CMD --env-file .env -p sdk-ci "$@"
}

show_banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║        SDK-CI  Docker 部署系统           ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
    echo ""
}

print_service_info() {
    echo ""
    info "服务端口映射:"
    echo "  ├── Caddy (反代)       → http://localhost:8080"
    echo "  ├── Minio API          → http://localhost:9000"
    echo "  ├── Minio Console      → http://localhost:9001"
    echo "  ├── Catalog DB (PG)    → localhost:5432"
    echo "  ├── Upload-Token       → http://localhost:8001"
    echo "  ├── Decrypt-Proxy      → http://localhost:8002"
    echo "  ├── Grafana            → http://localhost:3000"
    echo "  └── Jenkins            → http://localhost:8081"
    echo ""
}

# ========================= 主逻辑 =========================
main() {
    check_prerequisites

    local DETACH=false
    local BUILD=false
    local CMD=""

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--detach)
                DETACH=true
                shift
                ;;
            --build)
                BUILD=true
                shift
                ;;
            down|stop)
                CMD="down"
                shift
                ;;
            logs)
                CMD="logs"
                shift
                ;;
            ps|status)
                CMD="ps"
                shift
                ;;
            restart)
                CMD="restart"
                shift
                ;;
            *)
                echo "用法: $0 [-d] [--build] [down|logs|ps|restart]"
                exit 1
                ;;
        esac
    done

    # 执行对应命令
    case "$CMD" in
        down)
            step "正在停止并移除所有服务..."
            dc down --remove-orphans
            info "所有服务已停止并移除"
            exit 0
            ;;
        logs)
            dc logs -f
            exit 0
            ;;
        ps)
            dc ps -a
            exit 0
            ;;
        restart)
            step "正在重启所有服务..."
            dc restart
            info "所有服务已重启"
            exit 0
            ;;
        *)
            ;;
    esac

    show_banner

    # 拉取远程镜像 + 构建本地镜像
    step "正在拉取/更新远程镜像..."
    dc pull 2>/dev/null || warn "部分镜像拉取失败，将尝试本地构建"

    step "正在构建本地镜像 (upload-token / decrypt-proxy / jenkins)..."
    if $BUILD; then
        dc build --no-cache
    else
        dc build
    fi

    # 启动服务
    if $DETACH; then
        step "正在后台启动所有服务..."
        dc up -d --remove-orphans
        info "所有服务已在后台启动"
        print_service_info
        info "查看日志: ./start.sh logs"
        info "查看状态: ./start.sh ps"
        info "停止服务: ./start.sh down"
    else
        step "正在启动所有服务 (前台模式，Ctrl+C 停止)..."
        print_service_info
        dc up --remove-orphans
    fi
}

main "$@"