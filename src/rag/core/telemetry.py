"""OpenTelemetry tracing (§5.3). Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set; the OTel API is a no-op
without a provider, so spans in the code cost nothing when disabled. Any OTLP/HTTP backend works:
Jaeger locally (compose `observability` profile) or Langfuse (`<host>/api/public/otel` + auth header).
Spans carry identifiers, models, token counts and scores — never prompts, questions or document text."""

from collections.abc import Sequence

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from rag.core.config import Settings

tracer = trace.get_tracer("rag")

_SAFE_ATTRIBUTES = {
    "http.method",
    "http.request.method",
    "http.route",
    "http.status_code",
    "http.response.status_code",
    "rag.purpose",
    "gen_ai.system",
    "gen_ai.request.model",
    "rag.route_position",
    "rag.outcome",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "rag.job_id",
    "rag.attempt",
    "rag.retrieval.candidates",
    "rag.retrieval.top_score",
    "rag.retrieval.reranked",
}


class PrivateSpanExporter(SpanExporter):
    """Filter at the export boundary, including spans recorded by FastAPI's exception middleware."""

    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        clean = []
        for span in spans:
            attrs = {k: v for k, v in (span.attributes or {}).items() if k in _SAFE_ATTRIBUTES}
            name = span.name
            if span.kind == trace.SpanKind.SERVER:
                method = attrs.get("http.request.method", attrs.get("http.method", "HTTP"))
                name = f"{method} {attrs.get('http.route', 'unmatched')}"
            clean.append(
                ReadableSpan(
                    name=name,
                    context=span.context,
                    parent=span.parent,
                    kind=span.kind,
                    resource=span.resource,
                    attributes=attrs,
                    events=(),
                    links=(),
                    status=trace.Status(span.status.status_code),
                    start_time=span.start_time,
                    end_time=span.end_time,
                    instrumentation_scope=span.instrumentation_scope,
                )
            )
        return self.exporter.export(clean)

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporter.force_flush(timeout_millis)


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
    provider.add_span_processor(BatchSpanProcessor(PrivateSpanExporter(exporter)))
    trace.set_tracer_provider(provider)
    if app is not None:
        # No per-message ASGI spans: an SSE answer would otherwise emit one span per token.
        FastAPIInstrumentor.instrument_app(
            app, excluded_urls="healthz,readyz,metrics", tracer_provider=provider, exclude_spans=["receive", "send"]
        )


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
