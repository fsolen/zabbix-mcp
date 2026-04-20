#!/bin/bash
set -euo pipefail

# Zabbix MCP Build Script for OpenShift

# Configuration
IMAGE_NAME="${IMAGE_NAME:-zabbix-mcp}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
REGISTRY="${REGISTRY:-}"
NAMESPACE="${NAMESPACE:-zabbix-mcp}"
DOCKERFILE="${DOCKERFILE:-Dockerfile}"
BUILD_CONTEXT="${BUILD_CONTEXT:-.}"
PUSH="${PUSH:-false}"
BUILD_TYPE="${BUILD_TYPE:-local}"  # local, openshift

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Detect container runtime
detect_runtime() {
    if command -v podman &> /dev/null; then
        echo "podman"
    elif command -v docker &> /dev/null; then
        echo "docker"
    else
        error "No container runtime found. Install docker or podman."
    fi
}

RUNTIME=$(detect_runtime)
log "Using container runtime: ${RUNTIME}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        -r|--registry)
            REGISTRY="$2"
            shift 2
            ;;
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -p|--push)
            PUSH="true"
            shift
            ;;
        --name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        --openshift)
            BUILD_TYPE="openshift"
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -t, --tag TAG         Image tag (default: latest)"
            echo "  -r, --registry REG    Registry URL (e.g., image-registry.openshift-image-registry.svc:5000)"
            echo "  -n, --namespace NS    OpenShift namespace (default: zabbix-mcp)"
            echo "  --name NAME           Image name (default: zabbix-mcp)"
            echo "  -p, --push            Push image after build"
            echo "  --no-cache            Build without cache"
            echo "  --openshift           Use OpenShift internal build (BuildConfig)"
            echo "  -h, --help            Show this help"
            echo ""
            echo "Examples:"
            echo "  # Local build with podman/docker"
            echo "  $0 -t v1.0.0"
            echo ""
            echo "  # Push to OpenShift internal registry"
            echo "  $0 -r default-route-openshift-image-registry.apps.cluster.example.com -n zabbix-mcp -t v1.0.0 --push"
            echo ""
            echo "  # Trigger OpenShift BuildConfig"
            echo "  $0 --openshift -t v1.0.0"
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

# OpenShift build using BuildConfig
if [[ "$BUILD_TYPE" == "openshift" ]]; then
    log "Triggering OpenShift build..."
    
    if ! command -v oc &> /dev/null; then
        error "oc CLI not found. Install OpenShift CLI."
    fi
    
    # Check login
    if ! oc whoami &> /dev/null; then
        error "Not logged in to OpenShift. Run: oc login"
    fi
    
    # Start build
    oc start-build ${IMAGE_NAME} -n ${NAMESPACE} --follow
    
    log "OpenShift build completed"
    exit 0
fi

# Build full image name
if [[ -n "$REGISTRY" ]]; then
    FULL_IMAGE="${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:${IMAGE_TAG}"
else
    FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
fi

log "Building image: ${FULL_IMAGE}"

# Get git info for labels
GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build
${RUNTIME} build \
    ${NO_CACHE:-} \
    --label "org.opencontainers.image.created=${BUILD_DATE}" \
    --label "org.opencontainers.image.revision=${GIT_COMMIT}" \
    --label "org.opencontainers.image.source=${GIT_BRANCH}" \
    --label "org.opencontainers.image.title=${IMAGE_NAME}" \
    -t "${FULL_IMAGE}" \
    -f "${DOCKERFILE}" \
    "${BUILD_CONTEXT}"

log "Build completed: ${FULL_IMAGE}"

# Also tag as latest if not already
if [[ "$IMAGE_TAG" != "latest" ]]; then
    if [[ -n "$REGISTRY" ]]; then
        LATEST_TAG="${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:latest"
    else
        LATEST_TAG="${IMAGE_NAME}:latest"
    fi
    ${RUNTIME} tag "${FULL_IMAGE}" "${LATEST_TAG}"
    log "Tagged as: ${LATEST_TAG}"
fi

# Push if requested
if [[ "$PUSH" == "true" ]]; then
    log "Pushing image..."
    
    # For OpenShift internal registry, may need to login first
    if [[ "$REGISTRY" == *"openshift"* ]] || [[ "$REGISTRY" == *"apps."* ]]; then
        if command -v oc &> /dev/null; then
            TOKEN=$(oc whoami -t 2>/dev/null || true)
            if [[ -n "$TOKEN" ]]; then
                ${RUNTIME} login -u $(oc whoami) -p ${TOKEN} ${REGISTRY} --tls-verify=false 2>/dev/null || true
            fi
        fi
    fi
    
    ${RUNTIME} push "${FULL_IMAGE}"
    
    if [[ "$IMAGE_TAG" != "latest" ]]; then
        ${RUNTIME} push "${LATEST_TAG}"
    fi
    
    log "Push completed"
fi

# Show image info
log "Image details:"
${RUNTIME} images "${FULL_IMAGE}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.Created}}"

echo ""
log "Done!"
echo ""
echo "Deploy to OpenShift:"
echo "  oc apply -k openshift/"
echo ""
echo "Or run locally:"
echo "  ${RUNTIME} run -p 8080:8080 ${FULL_IMAGE}"
