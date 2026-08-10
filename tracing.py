"""OpenTelemetry (OTLP) Tracing Initialization and Instrumentation Module for OmaTasku.

Provides robust, defensive distributed tracing setup that falls back gracefully
to no-op operations if OpenTelemetry is not installed or when collector is offline.
"""

import os
import sys
import logging

# Defensive import wrapping to guarantee the server remains 100% stable
# even if OpenTelemetry libraries are not yet installed or missing.
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


# pylint: disable=too-few-public-methods
class SuppressOTELConnectionTracebackFilter(logging.Filter):
    """Custom logging filter that suppresses verbose tracebacks when OTLP collector is offline."""

    def __init__(self, name: str = ""):
        super().__init__(name)
        self.already_warned = False

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        is_conn_error = (
            "Connection refused" in message
            or "Failed to export" in message
            or "Max retries exceeded" in message
        )

        # Check exception details if present
        if record.exc_info:
            _, exc_value, _ = record.exc_info
            if exc_value and (
                "Connection refused" in str(exc_value)
                or "Max retries exceeded" in str(exc_value)
            ):
                is_conn_error = True

        if is_conn_error:
            # Silence the full traceback log entirely
            # Instead, we print a clean, simple, 1-line warning message to stderr ONCE
            if not self.already_warned:
                err_msg = (
                    "OmaTasku Warning: Failed to export traces to OTLP collector "
                    "(Connection refused). Is your OTLP collector running?"
                )
                print(err_msg, file=sys.stderr)
                self.already_warned = True
            return False  # Discard the full log record (hiding the traceback)

        return True


def apply_otel_logging_filter():
    """Applies the custom suppression filter to OpenTelemetry loggers."""
    otel_filter = SuppressOTELConnectionTracebackFilter()
    for logger_name in [
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
        "opentelemetry.exporter.otlp",
        "opentelemetry.sdk.trace.export"
    ]:
        logging.getLogger(logger_name).addFilter(otel_filter)


def init_tracer():
    """Initializes OpenTelemetry Tracer Provider and registers OTLP exporter if configured."""
    if not HAS_OTEL:
        print("OmaTasku Trace Info: OpenTelemetry not installed. Tracing is disabled.")
        return None

    # Apply our custom silent traceback logger filter to prevent connection-refused spam
    apply_otel_logging_filter()

    # Retrieve service name and endpoint from standard OpenTelemetry environment variables
    service_name = os.getenv("OTEL_SERVICE_NAME", "omatasku")
    otlp_endpoint = (
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )

    if not otlp_endpoint:
        # If no endpoint is configured, we fallback to a local tracer without exporting
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        trace.set_tracer_provider(provider)
        print("OmaTasku Trace Info: OTEL_EXPORTER_OTLP_ENDPOINT not set. Traces won't be exported.")
        return trace.get_tracer(service_name)

    print(f"OmaTasku Trace Info: Initializing OTLP tracing. Endpoint: {otlp_endpoint}")

    # Define tracer resources
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Configure OTLP Exporter (defaults to http/protobuf which is standard and lightweight)
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    span_processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(span_processor)

    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


# Initialize the global tracer instance
tracer_instance = init_tracer()


def get_tracer():
    """Returns the active global OpenTelemetry tracer."""
    if HAS_OTEL and tracer_instance:
        return tracer_instance
    # Fallback to standard no-op trace provider if OTEL is disabled
    # pylint: disable=import-outside-toplevel
    from opentelemetry import trace as noop_trace
    return noop_trace.get_tracer("omatasku-noop")


def instrument_fastapi_app(app):
    """Instruments the FastAPI application to automatically trace all incoming requests."""
    if HAS_OTEL and os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # pylint: disable=broad-except
        try:
            FastAPIInstrumentor.instrument_app(app)
            print("OmaTasku Trace Info: FastAPI app successfully instrumented with OpenTelemetry.")
        except Exception as e:
            print(f"OmaTasku Warning: Could not instrument FastAPI app: {e}")
