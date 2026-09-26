FROM python:3.12-alpine

WORKDIR /app
COPY pyproject.toml ./
COPY quorum_analyzer ./quorum_analyzer
RUN pip install --no-cache-dir .

USER 65534:65534
ENTRYPOINT ["quorum-analyzer"]
