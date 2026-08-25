FROM itsafeaturemythic/mythic_python_base:latest

WORKDIR /Mythic/
COPY [".", "."]

# Install aiohttp for async HTTP requests to LLM APIs
# and mythic-container >= 0.7.0rc9 for Chat container support (ChatBase)
RUN python3 -m pip install aiohttp "mythic-container>=0.7.0rc9"

CMD ["python3", "main.py"]
