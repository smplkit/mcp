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
    },
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
