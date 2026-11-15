# Kubernetes Example

Kustomize manifests for three FastAPI services with `fastapi-observer`, a
traffic generator, an OpenTelemetry Collector, Prometheus, Loki, Tempo and
Grafana. The Grafana dashboard JSON comes from
`examples/full_stack/config/grafana/dashboards/api-overview.json`.

```bash
kubectl kustomize --load-restrictor=LoadRestrictionsNone examples/k8s | kubectl apply -f -
```

Deployment, verification and cleanup steps are in the
[Kubernetes guide](https://vitaee.github.io/FastapiObserver/kubernetes/).
