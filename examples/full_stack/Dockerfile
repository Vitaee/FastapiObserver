FROM python:3.13-slim

WORKDIR /app

# Copy only package inputs, not the checkout's environment, Git data or build outputs.
COPY pyproject.toml README.md LICENSE /pkg/
COPY src /pkg/src
# Current SQL tracing supports SQLAlchemy 2.0. Query counting also works on 2.1.
RUN pip install --no-cache-dir "/pkg[prometheus,otel,otel-httpx,otel-sqlalchemy]" \
    uvicorn httpx2 "sqlalchemy>=2.0,<2.1"

COPY examples/full_stack/app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
