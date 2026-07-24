# Home cluster

Flux reconciles this directory as the root Kustomization. Each major unit has
its own Flux `Kustomization` so dependencies and blast radius remain explicit.

```text
clusters/home-cluster/
├── flux-system/          Flux controllers and reconciliation graph
├── apps/
│   ├── ai-stack/         Local inference and agent platform
│   └── suppr/            Sanitized production application manifests
├── infrastructure/
│   ├── kube-vip/         Highly available Kubernetes API virtual IP
│   ├── gpu/              NVIDIA and Intel GPU enablement
│   ├── storage/          SMB CSI support
│   └── cattle-system/    Rancher deployment
├── monitoring/           Prometheus, Grafana, alerting, and Cilium metrics
├── sources/              Custom service build contexts
└── tools/                Developer-side utilities
```

## Conventions

- Desired state changes flow through Git and Flux.
- Stateful RWO workloads use rollout strategies compatible with their storage.
- Namespaces use default-deny policy; new flows require both caller egress and
  callee ingress.
- Sensitive Secret manifests are omitted from this public mirror.
- Values using `example.com`, `example.invalid`, or `192.0.2.0/24` are
  documentation placeholders.

The public tree is intended for architecture review and static validation. It
is not a deployable cluster configuration.
