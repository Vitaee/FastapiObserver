from observabilityfastapi import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)
from fastapi import FastAPI

app = FastAPI(title="ObservabilityFastAPI Demo")
install_observability(
    app,
    ObservabilitySettings(app_name="demo", service="demo", environment="dev"),
    security_policy=SecurityPolicy(),
    trusted_proxy_policy=TrustedProxyPolicy(),
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    print("Run with: uvicorn main:app --reload")


if __name__ == "__main__":
    main()
