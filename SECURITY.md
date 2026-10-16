# Security and data handling

- Keep all credentials in `backend/.env` or a secret manager; never place keys in frontend code, committed files, logs, or API responses.
- The backend validates URL schemes/hosts, upload extensions and size, and returns structured errors.
- The default SQLite file and generated exports may contain user-provided text. Do not commit them; use encryption and access controls for production deployments.
- Review each platform's API terms, privacy obligations, retention requirements, and rate limits before enabling an integration.
- Do not add scraping, CAPTCHA bypasses, credential collection, or rate-limit evasion to this project.
- For a public deployment, add authentication/authorization, CSRF protection where applicable, HTTPS, secret rotation, database backups, malware scanning for uploads, and audit logging.
