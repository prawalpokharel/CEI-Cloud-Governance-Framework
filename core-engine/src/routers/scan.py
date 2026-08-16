"""
Vulnerability scan ingest.

    POST /v1/scan

Same API-key authentication as snapshot ingest, since the scanner ships with
the agent and shares its credential. Results replace the previous scan for
each image: a superseded scan describes an image nobody is running any more.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_session
from ..db.models import ActorType, ApiKey, Cluster, ImageScan, ImageVulnerability
from ..services import audit
from .ingest import _parse_timestamp, _read_body, authenticate_agent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["scan"])

# A cluster running hundreds of distinct images is plausible; hundreds of
# thousands of stored findings from one submission is not.
MAX_IMAGES_PER_SUBMISSION = 500
MAX_VULNS_PER_IMAGE = 200


@router.post("/scan")
async def ingest_scan(
    request: Request,
    session: AsyncSession = Depends(get_session),
    auth: tuple[ApiKey, Cluster] = Depends(authenticate_agent),
):
    api_key, cluster = auth
    payload = await _read_body(request)

    images = payload.get("images")
    if not isinstance(images, list):
        raise HTTPException(status_code=400, detail="'images' must be a list")
    if len(images) > MAX_IMAGES_PER_SUBMISSION:
        raise HTTPException(
            status_code=413,
            detail=f"At most {MAX_IMAGES_PER_SUBMISSION} images per submission",
        )

    scanned_at = _parse_timestamp(payload.get("scanned_at"))
    scanner_version = payload.get("scanner_version")
    cluster_id = cluster.id
    tenant_id = cluster.tenant_id
    cluster_id_str = str(cluster_id)

    stored = 0
    failed = 0
    for image in images:
        reference = (image.get("reference") or "").strip()
        if not reference:
            continue

        # Replace rather than accumulate. Keeping superseded scans would grow
        # without bound and answer a question nobody asks -- what an image
        # looked like before it was rebuilt.
        existing = (
            await session.execute(
                select(ImageScan).where(
                    ImageScan.cluster_id == cluster_id,
                    ImageScan.image_reference == reference,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await session.execute(
                delete(ImageVulnerability).where(
                    ImageVulnerability.scan_id == existing.id
                )
            )
            await session.delete(existing)
            await session.flush()

        counts = image.get("counts") or {}
        scan_error = image.get("scan_error")
        if scan_error:
            failed += 1

        scan = ImageScan(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            image_reference=reference[:512],
            image_digest=(image.get("digest") or None),
            os_family=image.get("os_family"),
            os_name=image.get("os_name"),
            workload_keys=image.get("workload_keys") or [],
            scanned_at=scanned_at,
            scanner_version=scanner_version,
            critical_count=int(counts.get("CRITICAL", 0)),
            high_count=int(counts.get("HIGH", 0)),
            medium_count=int(counts.get("MEDIUM", 0)),
            low_count=int(counts.get("LOW", 0)),
            unknown_count=int(counts.get("UNKNOWN", 0)),
            fixable_count=int(image.get("fixable_count", 0)),
            scan_error=scan_error,
        )
        session.add(scan)
        await session.flush()

        for vuln in (image.get("vulnerabilities") or [])[:MAX_VULNS_PER_IMAGE]:
            if not vuln.get("id"):
                continue
            session.add(
                ImageVulnerability(
                    tenant_id=tenant_id,
                    cluster_id=cluster_id,
                    scan_id=scan.id,
                    vulnerability_id=str(vuln["id"])[:64],
                    severity=(vuln.get("severity") or "UNKNOWN")[:16],
                    cvss_score=vuln.get("cvss_score"),
                    pkg_name=(vuln.get("pkg_name") or "?")[:256],
                    installed_version=(vuln.get("installed_version") or None),
                    fixed_version=(vuln.get("fixed_version") or None),
                    pkg_class=(vuln.get("pkg_class") or None),
                    title=vuln.get("title"),
                    primary_url=(vuln.get("primary_url") or None),
                )
            )
        stored += 1

    api_key.last_used_at = datetime.now(timezone.utc)

    await audit.record(
        session,
        action="cluster.scanned",
        actor_type=ActorType.agent,
        actor_id=str(api_key.id),
        tenant_id=tenant_id,
        target_type="cluster",
        target_id=cluster_id_str,
        source_ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"images": stored, "failed": failed},
    )
    await session.commit()

    log.info(
        "Stored %d image scan(s) for cluster %s (%d failed)",
        stored, cluster_id_str, failed,
    )
    return {
        "status": "accepted",
        "cluster_id": cluster_id_str,
        "images_stored": stored,
        "images_failed": failed,
    }
