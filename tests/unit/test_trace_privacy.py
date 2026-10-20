from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from rag.core.telemetry import PrivateSpanExporter


def test_fastapi_traces_exclude_queries_raw_paths_and_exception_content() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(PrivateSpanExporter(exporter)))
    app = FastAPI()

    @app.get("/find")
    def find() -> None:
        raise ValueError("private prompt, document text and provider response")

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, exclude_spans=["receive", "send"])
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/find?q=confidential-query&token=secret-token").status_code == 500
        assert client.get("/private-document-title").status_code == 404
    spans = exporter.get_finished_spans()
    encoded = "".join(s.to_json() for s in spans)
    for secret in (
        "private prompt",
        "document text",
        "provider response",
        "confidential-query",
        "secret-token",
        "private-document-title",
    ):
        assert secret not in encoded
    assert any(s.status.status_code == StatusCode.ERROR for s in spans)
    assert any(s.name == "GET /find" for s in spans)
    provider.shutdown()
