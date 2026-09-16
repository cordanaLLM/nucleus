# Support & Community

Welcome to the `cordanaLLM/nucleus` community! This guide outlines how to get assistance, report defects, and collaborate on kernel engineering.

---

## Documentation Portal

Always check the authoritative documentation portal first:
- Documentation portal: `https://lusoris.github.io/nucleus`
- Architecture & Principles: [`docs/principles.md`](docs/principles.md)
- Kernel Release Matrix: [`versions.json`](versions.json)

---

## Support Channels

### 1. GitHub Discussions
For general questions, kernel configuration guidance, hardware compatibility queries (e.g. Blackwell RTX 5090, Intel Battlemage Xe2, AMD ROCm 10), and community Q&A, join [GitHub Discussions](https://github.com/cordanaLLM/nucleus/discussions).

### 2. Issue Tracker
The issue tracker is reserved for actionable defects, build failures, and structured proposals:
- **Bug Reports**: If you encounter a reproducible compilation failure, kernel panic in standard configurations, or packaging bug, open a [Bug Report](https://github.com/cordanaLLM/nucleus/issues/new?template=bug_report.yml). Include host architecture, compiler toolchain, kernel stream, and complete build logs.
- **Feature Requests**: To request a new kconfig fragment, architecture support, or upstream patch set, submit a [Feature Request](https://github.com/cordanaLLM/nucleus/issues/new?template=feature_request.yml).
- **Security Issues**: Do NOT open public issues for security vulnerabilities. Consult [SECURITY.md](SECURITY.md) for confidential reporting instructions.
