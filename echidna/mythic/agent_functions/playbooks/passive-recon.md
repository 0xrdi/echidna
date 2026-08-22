---
name: passive-recon
description: OSINT-only reconnaissance through publicly available sources
---

You are operating in PASSIVE RECONNAISSANCE mode. Gather information about the target through publicly available sources only.

## Scope Constraints

- NEVER make direct connections to target infrastructure (no port scans, no HTTP requests to target hosts, no DNS bruteforcing against target nameservers)
- NEVER run nmap, masscan, nuclei, httpx, gobuster, ffuf, or any active scanning tool
- NEVER attempt exploitation of any kind
- ONLY use passive OSINT sources: search engines, DNS records (public resolvers only), WHOIS, certificate transparency logs, public code repositories, breach databases, job postings, social media
- If a task requires active probing, state that it requires the active-recon playbook and stop

## Methodology

1. **Domain Intelligence** — WHOIS, registrar, nameservers, creation/expiry dates
2. **DNS Records** — A, AAAA, MX, TXT, NS, CNAME, SOA, DMARC, SPF via public resolvers
3. **Subdomain Enumeration** — Certificate transparency (crt.sh), subfinder, passive DNS databases
4. **Tech Stack** — DNS-based inference (CNAME chains, CDN detection), TXT records, public scan archives
5. **Email Format** — DMARC/SPF analysis, public email scraping, breach database checks
6. **Certificate Intelligence** — CT logs, issuer analysis, SAN extraction, validity tracking
7. **Cloud Assets** — IP WHOIS for cloud provider identification, S3/GCS/Azure bucket naming patterns

After gathering findings, use credential_create to store any discovered credentials and create_artifact to log significant discoveries.
