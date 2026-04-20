#!/bin/bash
set -euo pipefail

# Zabbix MCP OpenShift Deployment Script

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

NAMESPACE="${NAMESPACE:-zabbix-mcp}"
ROUTE_HOST="${ROUTE_HOST:-}"
ACTION="${1:-apply}"

# Check oc login
if ! oc whoami &> /dev/null; then
    error "Not logged in to OpenShift. Run: oc login <cluster-url>"
fi

CLUSTER=$(oc whoami --show-server)
log "Connected to: ${CLUSTER}"
log "User: $(oc whoami)"

case "$ACTION" in
    apply|deploy)
        log "Deploying to namespace: ${NAMESPACE}"
        
        # Create project if not exists
        if ! oc get project ${NAMESPACE} &> /dev/null; then
            log "Creating project ${NAMESPACE}..."
            oc new-project ${NAMESPACE} --display-name="Zabbix MCP" || true
        fi
        
        # Switch to namespace
        oc project ${NAMESPACE}
        
        # Apply manifests
        log "Applying manifests..."
        oc apply -k openshift/
        
        # Wait for deployments
        log "Waiting for deployments..."
        oc rollout status deployment/zabbix-mcp-api -n ${NAMESPACE} --timeout=300s || true
        oc rollout status deployment/zabbix-mcp-worker -n ${NAMESPACE} --timeout=300s || true
        
        # Get route
        ROUTE_URL=$(oc get route zabbix-mcp-api -n ${NAMESPACE} -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
        
        echo ""
        log "Deployment completed!"
        echo ""
        echo "Resources:"
        oc get pods,svc,route -n ${NAMESPACE}
        echo ""
        if [[ -n "$ROUTE_URL" ]]; then
            echo "API URL: https://${ROUTE_URL}"
            echo "Health:  https://${ROUTE_URL}/health"
            echo "Metrics: https://${ROUTE_URL}/metrics"
        fi
        ;;
        
    delete|destroy)
        warn "Deleting all resources in ${NAMESPACE}..."
        read -p "Are you sure? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            oc delete -k openshift/ --ignore-not-found
            log "Resources deleted"
        fi
        ;;
        
    build)
        log "Starting build..."
        oc start-build zabbix-mcp -n ${NAMESPACE} --follow
        ;;
        
    logs)
        COMPONENT="${2:-api}"
        log "Showing logs for ${COMPONENT}..."
        oc logs -f -l app.kubernetes.io/component=${COMPONENT} -n ${NAMESPACE}
        ;;
        
    status)
        log "Status for ${NAMESPACE}:"
        echo ""
        echo "=== Pods ==="
        oc get pods -n ${NAMESPACE} -o wide
        echo ""
        echo "=== Services ==="
        oc get svc -n ${NAMESPACE}
        echo ""
        echo "=== Routes ==="
        oc get route -n ${NAMESPACE}
        echo ""
        echo "=== Events (last 10) ==="
        oc get events -n ${NAMESPACE} --sort-by='.lastTimestamp' | tail -10
        ;;
        
    *)
        echo "Usage: $0 {apply|delete|build|logs|status}"
        echo ""
        echo "Commands:"
        echo "  apply   - Deploy to OpenShift"
        echo "  delete  - Remove all resources"
        echo "  build   - Trigger BuildConfig"
        echo "  logs    - Show logs (api|worker)"
        echo "  status  - Show deployment status"
        exit 1
        ;;
esac
