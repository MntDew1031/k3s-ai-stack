# Suppr application architecture

This directory preserves a sanitized view of a production-style application:

- Separate web and API deployments
- PostgreSQL and Redis data services
- Persistent uploads
- LAN-restricted administration
- Cloudflare Tunnel ingress
- Default-deny Cilium policy
- Scheduled notifications, voting, digest, cleanup, and backup jobs
- Off-cluster backup support

All image sources are intentionally absent. Every `image:` field uses a
non-resolving, role-based placeholder under `example.invalid`; neither private
nor third-party registry locations and versions are represented. Registry
authentication, application secrets, product launch notes, live domains,
private addresses, and operational recovery details are also excluded.

These manifests are provided to demonstrate workload design and security
boundaries; they are not deployable as published.
