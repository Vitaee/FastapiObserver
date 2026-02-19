FROM python:3.13-slim

WORKDIR /app

# The build context is the project root (set in docker-compose.yml).
# Copy the entire package source so pip can install from local source.
COPY . /pkg
RUN pip install --no-cache-dir "/pkg[all]" uvicorn httpx

# Copy just the app file from the example directory
COPY examples/full_stack/app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
