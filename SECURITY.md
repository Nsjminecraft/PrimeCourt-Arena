# Security Policy

## Overview

PrimeCourt Arena is a tennis court booking and management system that handles sensitive user data, payment information, and administrative functions. We take security seriously and appreciate the community's help in identifying and responsibly disclosing security vulnerabilities.

## Reporting Security Vulnerabilities

### How to Report

If you discover a security vulnerability in PrimeCourt Arena, please report it responsibly by contacting us directly:

**Primary Contact:** coolguy100 on Discord

### What to Include

When reporting a security vulnerability, please include:

- A clear description of the vulnerability
- Steps to reproduce the issue
- Potential impact assessment
- Any proof-of-concept code (if applicable)
- Suggested remediation (if available)

### Response Timeline

We are committed to addressing security issues promptly:

- **Initial Response:** Within 48 hours of receiving your report
- **Status Updates:** Every 7 days until resolution
- **Resolution:** Critical vulnerabilities within 30 days, other issues within 90 days

## Scope

This security policy covers vulnerabilities in:

### In Scope

- **Authentication & Authorization**
  - User login/signup processes
  - Password storage and handling
  - Session management
  - Admin privilege escalation
  - Role-based access control

- **Payment Processing**
  - Stripe integration security
  - Payment data handling
  - Membership management

- **Data Protection**
  - User personal information
  - Booking data
  - Email communications
  - Database security (MongoDB)

- **Application Security**
  - Input validation and sanitization
  - Cross-Site Scripting (XSS)
  - SQL/NoSQL injection
  - Cross-Site Request Forgery (CSRF)
  - Server-Side Request Forgery (SSRF)

- **Infrastructure**
  - Environment variable handling
  - Secret management
  - Email configuration security

### Out of Scope

- Social engineering attacks
- Physical security issues
- Denial of Service (DoS) attacks
- Issues in third-party dependencies (unless directly exploitable in our implementation)
- Brute force attacks on login forms
- Missing security headers (unless leading to direct exploitation)

## Security Best Practices for Users

### For Regular Users
- Use strong, unique passwords
- Keep your account information up to date
- Log out after each session, especially on shared devices
- Report suspicious activity immediately

### For Administrators
- Use strong authentication credentials
- Regularly review user permissions and access levels
- Monitor system logs for unusual activity
- Keep environment variables and secrets secure
- Use the temporary admin routes responsibly and remove them after use

### For Developers
- Follow secure coding practices
- Validate all user inputs
- Use parameterized queries for database operations
- Implement proper error handling without information disclosure
- Keep dependencies updated
- Use environment variables for sensitive configuration
- Implement proper logging without exposing sensitive data

## Known Security Considerations

The application implements several security measures:

- Password hashing using PBKDF2 with SHA-256
- Session-based authentication
- Role-based access control (admin, coach, member)
- Environment variable usage for sensitive configuration
- Stripe integration for secure payment processing
- Input validation and length restrictions

## Vulnerability Disclosure Timeline

1. **Day 0:** Vulnerability reported
2. **Day 1-2:** Initial assessment and acknowledgment
3. **Day 3-14:** Investigation and reproduction
4. **Day 15-30:** Development of fix (critical issues)
5. **Day 31-90:** Development of fix (non-critical issues)
6. **Day of Fix:** Security update released
7. **30 days post-fix:** Public disclosure (if appropriate)

## Security Updates

Security updates will be communicated through:
- Direct notification to the reporting party
- Repository commit messages
- Release notes for significant security fixes

## Recognition

We appreciate the security research community's efforts in making PrimeCourt Arena more secure. While we don't currently offer a bug bounty program, we will acknowledge security researchers who responsibly disclose vulnerabilities (with their permission).

## Legal

We request that security researchers:
- Make a good faith effort to avoid privacy violations and disruption to others
- Only interact with test accounts you own or with explicit permission
- Do not access or modify data belonging to other users
- Do not perform testing that could negatively affect our users

We commit to:
- Work with you to understand and resolve the issue quickly
- Not pursue legal action against researchers who comply with this policy
- Acknowledge your contribution (with your permission)

## Contact Information

**Security Contact:** coolguy100 on Discord

For general inquiries or non-security issues, please use the standard repository issue tracker.

---

*This security policy is effective as of the date of this document and may be updated periodically to reflect changes in our security practices and procedures.*