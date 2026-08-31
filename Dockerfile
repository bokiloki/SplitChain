FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY splitchain ./splitchain
RUN pip install --no-cache-dir .
USER 65532:65532
ENTRYPOINT ["splitd"]
CMD ["--host", "0.0.0.0", "--port", "8765"]

