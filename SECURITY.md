# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x | Yes |
| < 1.0 | No |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them via:

1. **Email**: Send details to the maintainers via a private channel (see repository contact info)
2. **GitHub Security Advisory**: Use [GitHub's private vulnerability reporting](https://github.com/your-org/ctrlplane-enhanced/security/advisories/new)

Please include:
- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact
- Suggested fix (if you have one)

We will acknowledge your report within 48 hours and provide a more detailed response within 7 days.

## Security Considerations

### API Keys
This project handles sensitive API keys (Anthropic, OpenAI, GitHub, Ctrlplane). These must **never** be committed to the repository. Use environment variables via .env (which is git-ignored) or a secrets manager.

### Auto-Rollback
The auto-rollback feature can trigger production rollbacks. It requires authentication via X-API-Key header. In production, set CTRLPLANE_AGENT_API_KEY to a strong secret.

### Rate Limiting
The API has built-in rate limiting (default: 60 requests/minute). Adjust RATE_LIMIT_PER_MINUTE environment variable for your deployment.

### SQLite
The default SQLite backend is suitable for single-instance deployments. For multi-instance deployments, replace with PostgreSQL via the db_path configuration pointing to a shared database.

### Network Access
The sidecar should not be exposed to the public internet. Deploy behind an internal load balancer or API gateway that restricts access to authorized services.
