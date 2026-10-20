"""OpenTelemetry tracing (§5.3). Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set; the OTel API is a no-op
without a provider, so spans in the code cost nothing when disabled. Any OTLP/HTTP backend works:
Jaeger locally (compose `observability` profile) or Langfuse (`<host>/api/public/otel` + auth header).
Spans carry identifiers, models, token counts and scores — never prompts, questions or document text."""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from rag.core.config import Settings

tracer = trace.get_tracer("rag")


def _headers(raw: str) -> dict[str, str]:
    pairs = (item.split("=", 1) for item in raw.split(",") if "=" in item)
    return {k.strip(): v.strip() for k, v in pairs}


def setup_tracing(settings: Settings, service: str, app: FastAPI | None = None) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    headers = settings.otel_exporter_otlp_headers
    exporter = OTLPSpanExporter(
        endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces",
        headers=_headers(headers.get_secret_value()) if headers else None,
    )
    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "deployment.environment": settings.env})
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics", tracer_provider=provider)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
