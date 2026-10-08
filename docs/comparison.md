# Comparison with other tools

QuadDeploy is a small project built to show an approach. The tools below are more mature, and for most real needs one of them is the better choice. This page says where QuadDeploy differs.

The entries are based on each project's own documentation, not on running the tools side by side. Check each project for its current features.

## Summary

| Tool | Direction | What it deploys | Checks image signatures itself |
|---|---|---|---|
| QuadDeploy | Pull | Quadlet unit files from Git, with images pinned to digests | Yes, required |
| `podman auto-update` | Pull | New images for units that already exist | No |
| FetchIt | Pull | Several Podman deployment methods from Git | Not that I found documented |
| Materia | Pull | Quadlets from Git | Not that I found documented |
| Kamal | Push | Docker containers over SSH | Not that I found documented |
| Kubernetes with a GitOps controller | Pull | Kubernetes resources | Through an admission policy add-on |

## podman auto-update

[`podman auto-update`](https://docs.podman.io/en/latest/markdown/podman-auto-update.1.html) ships with Podman. A unit marked `AutoUpdate=registry` gets its image re-pulled when the registry has a newer one, and Podman restarts the unit. If the restart fails, Podman rolls back to the previous image. A systemd timer runs it daily by default.

It is the simplest way to keep images fresh, and for many hosts it is all that is needed.

The differences:

- **Scope.** It updates images. It does not install, change or remove unit files, so the units still have to reach the host some other way.
- **Signatures.** Its documentation does not mention signature checks. Podman can enforce them separately through `policy.json`.
- **Rollback trigger.** It rolls back when the restart fails. QuadDeploy also rolls back when the unit starts but a health URL does not answer.
- **Rollback scope.** It rolls back the image. QuadDeploy rolls back the unit file and the image together, because the digest is written in the file.

## FetchIt

[FetchIt](https://github.com/containers/fetchit) is a GitOps tool for Podman from the `containers` organisation. It polls Git repos and applies changes through several methods, including systemd units, Ansible and `podman kube`. It is broader than QuadDeploy and can manage more than one kind of workload. Its most recent tagged release that I found is v0.0.2, from May 2024.

QuadDeploy does one of those jobs, Quadlet files, and adds mandatory signature verification, digest pinning and a health-gated rollback.

## Materia

[Materia](https://github.com/stryan/materia) is a GitOps tool for deploying Quadlets, which is the same job as QuadDeploy. I know it only from its description. If you want a maintained tool for Quadlets from Git, look at it first.

## Kamal

[Kamal](https://kamal-deploy.org/) deploys Docker containers to servers over SSH, with zero-downtime switching through a proxy. It is push-based: the machine that runs the deploy holds SSH access to the servers.

That is the main difference. QuadDeploy's host fetches its own changes, so CI holds no credentials for it. Kamal in turn offers things QuadDeploy does not, such as zero-downtime deploys and more than one server.

## Kubernetes

A single-node Kubernetes such as k3s, with a GitOps controller such as Flux or Argo CD and an admission policy for signatures, does everything QuadDeploy does and far more.

It also brings a control plane to run, upgrade and secure. QuadDeploy is for the case where the workload is a few containers on one machine, and systemd is already the supervisor. If you need more than one node, rolling updates or autoscaling, use Kubernetes.

## When to use which

- You only want fresh images for units you already manage: use `podman auto-update`.
- You want a maintained Git-driven tool for Podman: look at Materia and FetchIt.
- You have several servers and want zero-downtime deploys: use Kamal or Kubernetes.
- You want to see how signature verification, digest pinning and rollback fit together in about a thousand lines of Python: read QuadDeploy.
