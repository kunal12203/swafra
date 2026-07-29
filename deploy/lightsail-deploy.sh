#!/usr/bin/env bash
# Build, push, and deploy swafra-cloud to Lightsail Container Service.
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SERVICE="${SWAFRA_LIGHTSAIL_SERVICE:-swafra-cloud}"
POWER="${SWAFRA_LIGHTSAIL_POWER:-micro}"
SCALE="${SWAFRA_LIGHTSAIL_SCALE:-1}"
LABEL="${SWAFRA_LIGHTSAIL_LABEL:-api}"
IMAGE_LOCAL="${SWAFRA_IMAGE_LOCAL:-swafra-cloud:latest}"
SECRET_ID="${SWAFRA_CLOUD_SECRET_ID:-swafra/cloud/prod}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKDIR="${TMPDIR:-/tmp}/swafra-lightsail-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> ensure Lightsail service ${SERVICE} (${POWER} x${SCALE})"
exists=$(aws lightsail get-container-services --region "$REGION" \
  --query "length(containerServices[?containerServiceName=='${SERVICE}'])" \
  --output text)
if [[ "$exists" == "0" ]]; then
  aws lightsail create-container-service \
    --region "$REGION" \
    --service-name "$SERVICE" \
    --power "$POWER" \
    --scale "$SCALE"
fi

for i in $(seq 1 60); do
  state=$(aws lightsail get-container-services --region "$REGION" \
    --query "containerServices[?containerServiceName=='${SERVICE}'].state" \
    --output text)
  echo "    state=${state}"
  [[ "$state" == "READY" || "$state" == "RUNNING" ]] && break
  sleep 10
done

PLATFORM="${SWAFRA_DOCKER_PLATFORM:-linux/amd64}"
echo "==> build ${IMAGE_LOCAL} (${PLATFORM})"
docker build --platform "$PLATFORM" -t "$IMAGE_LOCAL" "$REPO_ROOT"

echo "==> push image to Lightsail (${SERVICE}/${LABEL})"
if ! command -v lightsailctl >/dev/null 2>&1; then
  echo "lightsailctl plugin missing — installing via aws (see AWS docs)" >&2
fi
PUSH_OUT=$(aws lightsail push-container-image \
  --region "$REGION" \
  --service-name "$SERVICE" \
  --label "$LABEL" \
  --image "$IMAGE_LOCAL")
echo "$PUSH_OUT"
IMAGE_REF=$(printf '%s\n' "$PUSH_OUT" | sed -n 's/.*Refer to this image as "\([^"]*\)".*/\1/p' | tail -1)
if [[ -z "$IMAGE_REF" || "$IMAGE_REF" == "None" ]]; then
  IMAGE_REF=$(aws lightsail get-container-images --service-name "$SERVICE" --region "$REGION" \
    --query "containerImages[0].image" --output text)
fi
echo "    image=${IMAGE_REF}"

echo "==> load runtime env from Secrets Manager (${SECRET_ID})"
aws secretsmanager get-secret-value \
  --region "$REGION" --secret-id "$SECRET_ID" --query SecretString --output text \
  > "$WORKDIR/secret.json"

python3 - "$WORKDIR" "$SERVICE" "$IMAGE_REF" <<'PY'
import json, pathlib, sys
workdir, service, image = sys.argv[1], sys.argv[2], sys.argv[3]
raw = json.loads(pathlib.Path(workdir, "secret.json").read_text())
env = {k: str(v) for k, v in raw.items() if v is not None and not str(k).startswith("_")}
payload = {
    "serviceName": service,
    "containers": {
        "api": {
            "image": image,
            "environment": env,
            "ports": {"8788": "HTTP"},
        }
    },
    "publicEndpoint": {
        "containerName": "api",
        "containerPort": 8788,
        "healthCheck": {
            "path": "/health",
            "intervalSeconds": 15,
            "timeoutSeconds": 5,
            "healthyThreshold": 2,
            "unhealthyThreshold": 4,
            "successCodes": "200",
        },
    },
}
pathlib.Path(workdir, "deployment.json").write_text(json.dumps(payload))
print("public_url=", env.get("SWAFRA_CLOUD_PUBLIC_URL", ""))
PY

echo "==> create deployment"
aws lightsail create-container-service-deployment \
  --region "$REGION" \
  --cli-input-json "file://${WORKDIR}/deployment.json"

echo "==> waiting for ACTIVE deployment"
for i in $(seq 1 80); do
  dep=$(aws lightsail get-container-services --region "$REGION" \
    --query "containerServices[?containerServiceName=='${SERVICE}'].currentDeployment.state" \
    --output text)
  svc=$(aws lightsail get-container-services --region "$REGION" \
    --query "containerServices[?containerServiceName=='${SERVICE}'].state" \
    --output text)
  url=$(aws lightsail get-container-services --region "$REGION" \
    --query "containerServices[?containerServiceName=='${SERVICE}'].url" \
    --output text)
  echo "    service=${svc} deployment=${dep} url=${url}"
  if [[ "$dep" == "ACTIVE" && "$svc" == "RUNNING" ]]; then
    echo "DONE  ${url}"
    exit 0
  fi
  if [[ "$dep" == "FAILED" ]]; then
    echo "DEPLOYMENT FAILED" >&2
    aws lightsail get-container-service-deployments --service-name "$SERVICE" --region "$REGION" \
      --query 'deployments[0]' --output json >&2 || true
    exit 1
  fi
  sleep 15
done
echo "timed out waiting for ACTIVE" >&2
exit 1
