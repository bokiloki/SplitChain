FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY splitchain ./splitchain
RUN pip install --no-cache-dir .
RUN mkdir -p /var/lib/splitchain && chown 65532:65532 /var/lib/splitchain
USER 65532:65532
ENTRYPOINT ["splitd"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
