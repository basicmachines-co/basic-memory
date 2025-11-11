# SPEC-12: OpenTelemetry Observability

## Why

We need comprehensive observability for basic-memory-cloud to:
- Track request flows across our multi-tenant architecture (MCP → Cloud → API services)
- Debug performance issues and errors in production
- Understand user behavior and system usage patterns
- Correlate issues to specific tenants for targeted debugging
- Monitor service health and latency across the distributed system

Currently, we only have basic logging without request correlation or distributed tracing capabilities.

## What

Implement OpenTelemetry instrumentation across all basic-memory-cloud services with:

### Core Requirements
1. **Distributed Tracing**: End-to-end request tracing from MCP gateway through to tenant API instances
2. **Tenant Correlation**: All traces tagged with tenant_id, user_id, and workos_user_id
3. **Service Identification**: Clear service naming and namespace separation
4. **Auto-instrumentation**: Automatic tracing for FastAPI, SQLAlchemy, HTTP clients
5. **Grafana Cloud Integration**: Direct OTLP export to Grafana Cloud Tempo

### Services to Instrument
- **MCP Gateway** (basic-memory-mcp): Entry point with JWT extraction
- **Cloud Service** (basic-memory-cloud): Provisioning and management operations
- **API Service** (basic-memory-api): Tenant-specific instances
- **Worker Processes** (ARQ workers): Background job processing

### Key Trace Attributes
- `tenant.id`: UUID from UserProfile.tenant_id
- `user.id`: WorkOS user identifier
- `user.email`: User email for debugging
- `service.name`: Specific service identifier
- `service.namespace`: Environment (development/production)
- `operation.type`: Business operation (provision/update/delete)
- `tenant.app_name`: Fly.io app name for tenant instances

## How

### Phase 1: Setup OpenTelemetry SDK
1. Add OpenTelemetry dependencies to each service's pyproject.toml:
   ```python
   "opentelemetry-distro[otlp]>=1.29.0",
   "opentelemetry-instrumentation-fastapi>=0.50b0",
   "opentelemetry-instrumentation-httpx>=0.50b0",
   "opentelemetry-instrumentation-sqlalchemy>=0.50b0",
   "opentelemetry-instrumentation-logging>=0.50b0",
   ```

2. Create shared telemetry initialization module (`apps/shared/telemetry.py`)

3. Configure Grafana Cloud OTLP endpoint via environment variables:
   ```bash
   OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-us-east-2.grafana.net/otlp
   OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic[token]
   OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
   ```

### Phase 2: Instrument MCP Gateway
1. Extract tenant context from AuthKit JWT in middleware
2. Create root span with tenant attributes
3. Propagate trace context to downstream services via headers

### Phase 3: Instrument Cloud Service
1. Continue trace from MCP gateway
2. Add operation-specific attributes (provisioning events)
3. Instrument ARQ worker jobs for async operations
4. Track Fly.io API calls and latency

### Phase 4: Instrument API Service
1. Extract tenant context from JWT
2. Add machine-specific metadata (instance ID, region)
3. Instrument database operations with SQLAlchemy
4. Track MCP protocol operations

### Phase 5: Configure and Deploy
1. Add OTLP configuration to `.env.example` and `.env.example.secrets`
2. Set Fly.io secrets for production deployment
3. Update Dockerfiles to use `opentelemetry-instrument` wrapper
4. Deploy to development environment first for testing

## How to Evaluate

### Success Criteria
1. **End-to-end traces visible in Grafana Cloud** showing complete request flow
2. **Tenant filtering works** - Can filter traces by tenant_id to see all requests for a user
3. **Service maps accurate** - Grafana shows correct service dependencies
4. **Performance overhead < 5%** - Minimal latency impact from instrumentation
5. **Error correlation** - Can trace errors back to specific tenant and operation

### Testing Checklist
- [x] Single request creates connected trace across all services
- [x] Tenant attributes present on all spans
- [x] Background jobs (ARQ) appear in traces
- [x] Database queries show in trace timeline
- [x] HTTP calls to Fly.io API tracked
- [x] Traces exported successfully to Grafana Cloud
- [x] Can search traces by tenant_id in Grafana
- [x] Service dependency graph shows correct flow

### Monitoring Success
- All services reporting traces to Grafana Cloud
- No OTLP export errors in logs
- Trace sampling working correctly (if implemented)
- Resource usage acceptable (CPU/memory)

## Dependencies
- Grafana Cloud account with OTLP endpoint configured
- OpenTelemetry Python SDK v1.29.0+
- FastAPI instrumentation compatibility
- Network access from Fly.io to Grafana Cloud

## Implementation Assignment
**Recommended Agent**: python-developer
- Requires Python/FastAPI expertise
- Needs understanding of distributed systems
- Must implement middleware and context propagation
- Should understand OpenTelemetry SDK and instrumentation

## Follow-up Tasks

### Enhanced Log Correlation
While basic trace-to-log correlation works automatically via OpenTelemetry logging instrumentation, consider adding structured logging for improved log filtering:

1. **Structured Logging Context**: Add `logger.bind()` calls to inject tenant/user context directly into log records
2. **Custom Loguru Formatter**: Extract OpenTelemetry span attributes for better log readability
3. **Direct Log Filtering**: Enable searching logs directly by tenant_id, workflow_id without going through traces

This would complement the existing automatic trace correlation and provide better log search capabilities.

## Alternative Solution: Logfire

After implementing OpenTelemetry with Grafana Cloud, we discovered limitations in the observability experience:
- Traces work but lack useful context without correlated logs
- Setting up log correlation with Grafana is complex and requires additional infrastructure
- The developer experience for Python observability is suboptimal

### Logfire Evaluation

**Pydantic Logfire** offers a compelling alternative that addresses your specific requirements:

#### Core Requirements Match
- ✅ **User Activity Tracking**: Automatic request tracing with business context
- ✅ **Error Monitoring**: Built-in exception tracking with full context
- ✅ **Performance Metrics**: Automatic latency and performance monitoring
- ✅ **Request Tracing**: Native distributed tracing across services
- ✅ **Log Correlation**: Seamless trace-to-log correlation without setup

#### Key Advantages
1. **Python-First Design**: Built specifically for Python/FastAPI applications by the Pydantic team
2. **Simple Integration**: `pip install logfire` + `logfire.configure()` vs complex OTLP setup
3. **Automatic Correlation**: Logs automatically include trace context without manual configuration
4. **Real-time SQL Interface**: Query spans and logs using SQL with auto-completion
5. **Better Developer UX**: Purpose-built observability UI vs generic Grafana dashboards
6. **Loguru Integration**: `logger.configure(handlers=[logfire.loguru_handler()])` maintains existing logging

#### Pricing Assessment
- **Free Tier**: 10M spans/month (suitable for development and small production workloads)
- **Transparent Pricing**: $1 per million spans/metrics after free tier
- **No Hidden Costs**: No per-host fees, only usage-based metering
- **Production Ready**: Recently exited beta, enterprise features available

#### Migration Path
The existing OpenTelemetry instrumentation is compatible - Logfire uses OpenTelemetry under the hood, so the current spans and attributes would work unchanged.

### Recommendation

**Consider migrating to Logfire** for the following reasons:
1. It directly addresses the "next to useless" traces problem by providing integrated logs
2. Dramatically simpler setup and maintenance compared to Grafana Cloud + custom log correlation
3. Better ROI on observability investment with purpose-built Python tooling
4. Free tier sufficient for current development needs with clear scaling path

The current Grafana Cloud implementation provides a solid foundation and could remain as a backup/export target, while Logfire becomes the primary observability platform.

## Status
**Created**: 2024-01-28
**Status**: Completed (OpenTelemetry + Grafana Cloud)
**Next Phase**: Evaluate Logfire migration
**Priority**: High - Critical for production observability
