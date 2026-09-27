FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY splitchain ./splitchain
# Git checkouts may preserve restrictive modes; the runtime UID must be able
# to import the package after the image drops root privileges.
RUN chmod -R a+rX /app \
    && pip install --no-cache-dir .
RUN mkdir -p /var/lib/splitchain /var/lib/olc && chown 65532:65532 /var/lib/splitchain /var/lib/olc
USER 65532:65532
ENTRYPOINT ["splitd"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
