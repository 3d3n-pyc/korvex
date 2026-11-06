# Security policy

## Supported versions

Korvex is before 1.0: fixes go into the latest release only. Upgrade to
it to get them.

| Version          | Supported |
| ---------------- | --------- |
| latest 0.x       | yes       |
| older            | no        |

## Reporting a vulnerability

Please don't open a public issue. Report it privately on GitHub instead:
[Security → Report a vulnerability](https://github.com/3d3n-pyc/korvex/security/advisories/new).
Only the maintainer sees it.

Say what's affected (the router, the built-in server, the App, a
middleware...), which version, and how to reproduce it, with a minimal
app if you can.

You'll get an answer within a week. Once a fix is ready, it's released,
then the advisory is published, crediting you unless you'd rather not
be named.

## Scope

Korvex is meant to run behind a reverse proxy (nginx, Caddy, Traefik, a
cloud load balancer) that handles TLS; see the
[deployment guide](docs/deployment.md). Reports about the built-in
server exposed directly to the internet are welcome, but are judged
with that in mind.
