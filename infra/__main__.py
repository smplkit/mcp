"""Pulumi program for the smplkit Jobs MCP server (ADR-057).

A spoke stack that reuses ``ProductServiceStack`` from ``smplkit-infra`` and
references the hub (``smplkit/app``) for shared VPC/cluster/DNS resources — the
same shape as the Jobs spoke, **minus the worker and minus the database** (the
MCP server is stateless). The ALB health check points at the server's
``/health`` route; the MCP endpoint is served under ``/api/mcp`` so it routes
through the standard CloudFront -> ALB ``/api/*`` pattern (ADR-011).
"""
from __future__ import annotations

import base64
from pathlib import Path

import pulumi

from smplkit_infra import ProductServiceStack

# ---------------------------------------------------------------------------
# Branded landing page (platform identity, not Jobs-specific)
# ---------------------------------------------------------------------------
# The page is self-contained: the official dark-mode smplkit logo (a bundled
# static asset) is inlined as a data URI so the served HTML has no external
# dependencies.
_STATIC = Path(__file__).resolve().parent.parent / "static"
_logo_b64 = base64.b64encode((_STATIC / "smplkit-logo.png").read_bytes()).decode("ascii")
_landing_html = (_STATIC / "landing.html").read_text(encoding="utf-8").replace(
    "__LOGO_SRC__", f"data:image/png;base64,{_logo_b64}"
)

# Glama connector ownership verification (https://glama.ai/mcp/faq). The file
# `static/.well-known/glama.json` is served at
# https://mcp.smplkit.com/.well-known/glama.json — CloudFront's default route
# sends /.well-known/* (other than oauth-protected-resource*) to the landing
# bucket. It's a fixed ownership doc (maintainer email), not dynamic content,
# so it lives as a static object. ProductServiceStack manages only
# `landing_page_html`, so it's published manually from the committed source:
#   aws s3 cp static/.well-known/glama.json \
#     s3://<landing bucket>/.well-known/glama.json --content-type application/json

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
config = pulumi.Config()
service_name = "mcp"
name = f"{service_name}-{pulumi.get_stack()}"

# Reference the platform stack for shared hub resources (VPC, subnets, ECS
# cluster, public/private DNS zones).
platform = pulumi.StackReference(f"smplkit/app/{pulumi.get_stack()}")

# Backend image tag — set via `pulumi config set mcp:imageTag <sha>` by CI.
image_tag = config.get("imageTag")
# X-CloudFront-Secret for origin isolation (ADR-018). Optional: when unset the
# component skips the header requirement.
cloudfront_origin_secret = config.get_secret("cloudfrontOriginSecret")
# The Jobs API host the server forwards customer requests to (ADR-057 §4).
jobs_base_domain = config.get("jobsBaseDomain") or "jobs.smplkit.com"

# WorkOS AuthKit — MCP one-tap OAuth (ADR-058). An empty AuthKit domain (the
# default) keeps OAuth off and the server runs the API-key path unchanged. Set
# `pulumi config set mcp:oauthAuthkitDomain <https://…authkit.app>` to turn on
# the resource-server + token-exchange path.
oauth_authkit_domain = (config.get("oauthAuthkitDomain") or "").rstrip("/")
app_internal_url = config.get("appInternalUrl") or "http://app.internal.smplkit.local"
_oauth_env = (
    {
        "MCP_OAUTH_AUTHORIZATION_SERVERS": oauth_authkit_domain,
        "MCP_OAUTH_JWKS_URI": f"{oauth_authkit_domain}/oauth2/jwks",
        "MCP_OAUTH_ISSUER": oauth_authkit_domain,
        "MCP_OAUTH_APP_INTERNAL_URL": app_internal_url,
    }
    if oauth_authkit_domain
    else {}
)

# ---------------------------------------------------------------------------
# Product Service Stack (api only — stateless, no worker, no database)
# ---------------------------------------------------------------------------
stack = ProductServiceStack(
    name,
    service_name=service_name,
    domain="mcp.smplkit.com",
    internal_domain="mcp.internal.smplkit.local",
    pulumi_project_name="mcp",
    image_tag=image_tag,
    # The server exposes /health (not the JSON:API /api/liveness probe).
    health_check_path="/health",
    environment_variables={
        "JOBS_BASE_DOMAIN": jobs_base_domain,
        **_oauth_env,
    },
    # When OAuth is on, route /.well-known/oauth-protected-resource* to the
    # server so it serves its own RFC 9728 PRM (ADR-058) — replaces the static
    # S3 fallback. Requires smplkit-infra >= 1.9.0.
    oauth_well_known_path=bool(oauth_authkit_domain),
    landing_page_html=_landing_html,
    # Hub references.
    vpc_id=platform.require_output("vpc_id"),
    vpc_cidr=platform.require_output("vpc_cidr"),
    public_subnet_ids=platform.require_output("public_subnet_ids"),
    private_subnet_ids=platform.require_output("private_subnet_ids"),
    ecs_cluster_arn=platform.require_output("ecs_cluster_arn"),
    route53_public_zone_id=platform.require_output("route53_public_zone_id"),
    route53_private_zone_id=platform.get_output("route53_private_zone_id"),
    cloudfront_origin_secret=cloudfront_origin_secret,
    # ADR-059: associate this service's CloudFront with the shared platform
    # WebACL instead of creating a per-service one.
    waf_web_acl_arn=platform.require_output("shared_waf_web_acl_arn"),
    # Stateless: no db_secret_arn, no rds_security_group_id, no worker_command.
)

# ---------------------------------------------------------------------------
# Stack Exports
# ---------------------------------------------------------------------------
pulumi.export("repositoryUrl", stack.ecr_repository_url)
pulumi.export("publicAlbDnsName", stack.public_alb_dns_name)
pulumi.export("internalAlbDnsName", stack.internal_alb_dns_name)
pulumi.export("ecsClusterArn", platform.require_output("ecs_cluster_arn"))
pulumi.export("ecsServiceArn", stack.ecs_service_arn)
pulumi.export("cloudfrontDistributionId", stack.cloudfront_distribution_id)
