# Security and Data Protection

This document describes the **Mestinghem AI** platform's approach to security, data protection and service resilience. We design the system with "security by default and by design".

- Operator: **Mestinghem AI UAB**, Vilnius, Lithuania
- Security contact: **security@mestinghem.ai**
- Version: 1.0

## 1. Access control and authentication

- Passwords are stored only as irreversible cryptographic hashes with a per-user salt; recovery is impossible, only reset.
- API access uses short-lived tokens with limited lifetime and session-refresh mechanisms.
- Two-factor authentication (TOTP) is supported to protect your account.
- Failed sign-in attempts are rate-limited and an account may be temporarily locked to prevent brute force.
- Personal API keys are issued on request, stored as hashes, and can be revoked at any time.

## 2. Module permission model

Access to sensitive modules (terminal, device control) is governed by separate permissions. You explicitly enable only the capabilities you need. Risky features are off by default.

## 3. Execution isolation (terminal sandbox)

Terminal commands run in an isolated workspace bound to your account. Security policies block:
- attempts to escape the sandbox or access system paths;
- destructive operations (deleting the root filesystem, formatting);
- downloading and running third-party scripts from untrusted sources;
- actions requiring privilege escalation.

## 4. Device-control security

Connecting an agent to control your computer is built on explicit consent and least privilege:
- the device pairs via a single-use, short-lived code and a unique device key;
- commands are signed and verified for authenticity;
- a permission profile limits the set of allowed actions;
- risky and critical actions require your explicit confirmation and never run automatically;
- operations capable of causing irreparable harm are forbidden outright.

See the Device Control Policy for details.

## 5. Data protection

- Encryption in transit on all connections (TLS).
- Least-privilege access to data.
- Personal data stored in jurisdictions with adequate protection or under Standard Contractual Clauses.
- Regular backups and recovery capability.
- User data segregation; access to another user's data is technically excluded.

## 6. Application security

- Inputs are validated and sanitised; outputs are sanitised.
- HTTP security headers and request-origin restrictions are applied.
- Dependencies are updated and known vulnerabilities are remediated.
- An audit log of significant events supports incident investigation.

## 7. Monitoring and incident response

We monitor security events and anomalies. In the event of an incident likely to result in a high risk to users' rights and freedoms, we notify the supervisory authority within 72 hours and affected users without undue delay, as required by the GDPR.

## 8. Responsible disclosure

We welcome reports from security researchers. The process is described in the Vulnerability Disclosure document. Report issues to **security@mestinghem.ai**.

## 9. User responsibility

Security is a shared task. Do not share credentials, use strong unique passwords, enable two-factor authentication, and grant the agent only the permissions you need.

---

Mestinghem AI UAB · Vilnius, Lithuania · security@mestinghem.ai
