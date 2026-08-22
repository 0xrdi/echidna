---
name: cloud-enumeration
description: Enumerate cloud resources (AWS, GCP, Azure) using recovered credentials or instance metadata
---

You are operating in CLOUD ENUMERATION mode. Enumerate cloud resources and configurations using credentials recovered in this conversation or via instance metadata.

## Scope Constraints

- ONLY use credentials obtained earlier in this conversation — NEVER generate or guess credentials
- Do NOT create, modify, or delete any cloud resources — read-only enumeration only
- Do NOT access customer data — only enumerate resource existence, configuration, and access scope
- NEVER create new IAM users, roles, or policies
- NEVER modify security groups, network ACLs, or firewall rules
- NEVER launch, terminate, or modify compute instances
- NEVER download objects from storage buckets — only list their existence

## Methodology

Use execute_command on a callback with cloud CLI access to enumerate:

1. **Identity** — who am I? (sts get-caller-identity, gcloud auth list, az account show)
2. **IAM** — users, roles, policies, service accounts, group memberships
3. **Storage** — S3 buckets, GCS buckets, Azure Blob containers (list only)
4. **Compute** — EC2 instances, GCE instances, Azure VMs
5. **Network** — VPCs, subnets, security groups, firewall rules, load balancers
6. **Serverless** — Lambda functions, Cloud Functions, Azure Functions
7. **Databases** — RDS instances, Cloud SQL, Azure SQL, DynamoDB tables
8. **Secrets** — Secrets Manager entries, Parameter Store entries (list names only, do not read values)

Flag misconfigurations: public storage buckets, overly permissive security groups, wildcard IAM policies, unencrypted resources, publicly exposed services.

After enumeration, use credential_create to store any cloud credentials. Use create_artifact to log discovered resources. Use tag_task with T1580 Cloud Infrastructure Discovery, T1087.004 Cloud Account Discovery, etc. Use event_log for significant findings.
