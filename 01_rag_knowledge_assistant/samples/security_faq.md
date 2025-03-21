# Data Retention

Customer order records are retained for seven years to satisfy tax law. Support
chat transcripts are deleted after 18 months.

Deleted account data is purged from live systems within 30 days and from encrypted
backups within 90 days.

# Encryption

All customer data is encrypted at rest using AES-256 and in transit using TLS 1.3.
Database backups are encrypted with a separate key managed in the key vault.

# Access Control

Engineers do not have standing access to production customer data. Access is
granted for a maximum of four hours through a reviewed break-glass request, and
every such session is recorded in the audit log.

# Incident Response

Security incidents are triaged within one hour. Customers affected by a confirmed
data breach are notified within 72 hours, as required by GDPR.
