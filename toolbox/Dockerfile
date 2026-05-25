FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# ============================================================
# APT: network and recon tools
# ============================================================
RUN apt-get update && apt-get install -y \
    curl wget python3 python3-pip \
    nmap sqlmap proxychains4 \
    dnsutils whois netcat-openbsd jq \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ============================================================
# Node.js 20.x + OpenAI Codex CLI (binary required by SDK)
# ============================================================
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex

# ============================================================
# Go-based recon tools (subfinder, httpx, nuclei)
# ============================================================
RUN curl -fsSL https://go.dev/dl/go1.23.5.linux-amd64.tar.gz | tar -C /usr/local -xzf - \
    && export PATH=$PATH:/usr/local/go/bin \
    && GOBIN=/usr/local/bin go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest \
    && GOBIN=/usr/local/bin go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest \
    && GOBIN=/usr/local/bin go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest \
    && GOBIN=/usr/local/bin go install -v github.com/OJ/gobuster/v3@latest \
    && rm -rf /usr/local/go /root/go

# ============================================================
# Python: FastAPI + Agent SDKs (Claude + Codex)
# ============================================================
RUN pip3 install fastapi uvicorn claude-agent-sdk openai-codex-sdk

COPY server.py /app/server.py
COPY skills/ /app/skills/

# ============================================================
# Non-root user
# ============================================================
RUN useradd -m -s /bin/bash -N toolbox \
    && chown -R toolbox:nogroup /app \
    && mkdir -p /tmp/toolbox && chown toolbox:nogroup /tmp/toolbox

WORKDIR /app
USER toolbox

EXPOSE 6789

CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "6789"]
