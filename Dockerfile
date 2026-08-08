FROM itsafeaturemythic/mythic_python_base:latest

WORKDIR /Mythic/
COPY [".", "."]

# Install aiohttp for async HTTP requests to LLM APIs
RUN python3 -m pip install aiohttp

CMD ["python3", "main.py"]
