# Deploy swafra-cloud (AWS)

Public edge: Lightsail Container Service `swafra-cloud` (micro) → Cognito JWT + Aurora Postgres.

## One-shot (local)

```bash
export AWS_PROFILE=admin AWS_DEFAULT_REGION=us-east-1
export PATH="$HOME/.local/bin:$PATH"   # lightsailctl
bash deploy/lightsail-deploy.sh
```

Requires Secrets Manager secret `swafra/cloud/prod` (SWAFRA_CLOUD_* env map).

## CI/CD

GitHub Actions workflow `.github/workflows/deploy-cloud.yml` runs on pushes to `production`.

Set repository secret on **samartho4/swafra**:

- `AWS_DEPLOY_ROLE_ARN` = ARN of IAM role `swafra-github-deploy` (OIDC)

Trust + permissions live in:

- `deploy/iam-github-oidc-trust.json`
- `deploy/iam-github-oidc-policy.json`

Apply:

```bash
aws iam update-assume-role-policy --role-name swafra-github-deploy \
  --policy-document file://deploy/iam-github-oidc-trust.json
aws iam put-role-policy --role-name swafra-github-deploy \
  --policy-name swafra-lightsail-deploy \
  --policy-document file://deploy/iam-github-oidc-policy.json
```

**OIDC note (2026):** this repo uses GitHub’s immutable `sub` claim
(`repo:samartho4@142962370/swafra@1314140584:ref:refs/heads/production`).
Classic `repo:owner/name:...` alone will fail `AssumeRoleWithWebIdentity`.

## Smoke

```bash
curl -fsS https://swafra-cloud.82f7bmkh1axgr.us-east-1.cs.amazonlightsail.com/health
# MCP: POST /mcp with Authorization: Bearer <Cognito access token>
```
