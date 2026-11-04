# astraeus-otel

OpenTelemetry SDK bootstrap for the [Astraeus](https://github.com/ASneakyToast/astraeus) package suite.

One call wires traces, structured logs, and OTEL export across `starlette-cms`, `astraeus-media`, and
`starlette-cms-gateways`. Without this package, all OTEL calls in those packages are no-ops.

## Usage

```python
from astraeus_otel import setup_telemetry, TelemetryConfig

# Option 1 — explicit config
setup_telemetry(TelemetryConfig(service_name="my-service"))

# Option 2 — read from OTEL_* / ASTRAEUS_* env vars
setup_telemetry()
```

## Configuration

`TelemetryConfig` is a Pydantic `BaseSettings` model. It reads standard OTEL environment variables
first, then Astraeus-specific ones for anything OTEL doesn't cover.

| Env var | Field | Default |
|---|---|---|
| `OTEL_SERVICE_NAME` | `service_name` | `"astraeus"` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `otlp_endpoint` | `None` (no export) |
| `OTEL_LOG_LEVEL` | `log_level` | `INFO` |
| `ASTRAEUS_ENABLE_CONSOLE` | `enable_console` | `False` |
| `ASTRAEUS_STRUCTLOG_RENDERER` | `structlog_renderer` | `"auto"` |

`structlog_renderer = "auto"` uses `ConsoleRenderer` when stdout is a TTY, `JSONRenderer` otherwise.
