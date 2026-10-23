FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -u 10001 -m gate && pip install --no-cache-dir "google-colab-cli"
WORKDIR /app
COPY pyproject.toml README.md ./
COPY apps/gate ./apps/gate
COPY packages ./packages
COPY profiles ./profiles
RUN pip install --no-cache-dir .
USER gate
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/_gate/healthz')"
CMD ["python", "-m", "gate.app"]
