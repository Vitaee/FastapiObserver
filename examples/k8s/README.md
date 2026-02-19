# Kubernetes Example

This folder contains a full Kubernetes observability demo for `fastapi-observer`.
The Grafana dashboard JSON is sourced from `examples/full_stack/config/grafana/dashboards/api-overview.json` via Kustomize.

## Apply Everything

```bash
kubectl kustomize --load-restrictor=LoadRestrictionsNone examples/k8s | kubectl apply -f -
```

## What Gets Deployed

- `app-a`, `app-b`, `app-c`: FastAPI services instrumented with `fastapi-observer`
- `traffic-generator`: continuous synthetic traffic (including `/chain`)
- `otel-collector`: OTLP ingest and export
- `prometheus`: metrics scraping
- `loki`: logs backend
- `tempo`: traces backend
- `grafana`: pre-provisioned datasources + dashboard

## Access Grafana

```bash
kubectl -n observability port-forward svc/grafana 3000:3000
```

Then open [http://localhost:3000](http://localhost:3000).

For full instructions, see [`kubernetes.md`](../../kubernetes.md).
